# =============================================================================
# Claude CLI (Max) runner — agents/core/cli.py
# =============================================================================
# WHAT: A Runner backed by the `claude` CLI (Claude Code) logged in with a Max
#       subscription — an alternative to the pay-per-token API backend. The CLI
#       runs its OWN tool loop and reaches Brain's tools over MCP directly (Brain
#       is an MCP server), so no in-process tool loop is needed here.
#
# WHY point the CLI at Brain's /mcp: Brain already speaks MCP with bearer-token
#       auth — exactly what the CLI wants as an MCP server. We hand the CLI an
#       --mcp-config for Brain (with this agent's token), and it can call the
#       same tools any agent gets, governed by the same access-list.
#
# TRAPS (learned in v1, preserved here):
#   • The CLI silently PREFERS ANTHROPIC_API_KEY over the Max login and then
#     bills per token. So we STRIP ANTHROPIC_API_KEY from the subprocess env.
#   • CLAUDE_CONFIG_DIR must point at a MOUNTED volume or the login is lost on
#     every container recreation. The agent passes it in.
#
# LIMITATIONS (be honest): the `claude` CLI carries Claude Code's own base
#   (coding-assistant) system prompt; we can only APPEND the soul via
#   --append-system-prompt, not fully replace it. And the CLI is single-shot, so
#   we serialize the recent history into the prompt as a transcript. The exact
#   CLI flags / MCP-config shape depend on the installed claude-code version and
#   may need tuning on first real run — they're centralized as constants below.
#
# COST (why this file is shaped the way it is): every turn pays for Claude
#   Code's own base prompt + its built-in tool schemas + our appended head —
#   tens of KB that never change — and the CLI re-runs its whole tool loop on
#   top of that. Two things keep it from being re-billed in full each time:
#     • --append-system-prompt gets ONLY the stable head. The volatile block
#       (clock, profile, recalled memories) goes into the prompt BODY instead.
#       While the two were concatenated, the appended system differed on every
#       message and the CLI's cached prefix broke right at the soul boundary.
#     • A follow-up turn --resume's the session it is following up on. The
#       self-check gate used to open a fresh session and replay the entire
#       transcript and system prompt just to ask "is this draft OK?", which
#       roughly doubled the cost of every gated turn.
#
# HOW: `ClaudeCliRunner(model, brain_mcp_url, agent_token, config_dir).run(...)`.
# =============================================================================

import asyncio
import json
import logging
import os
import tempfile

from infra.quota import detect_quota

from agents.core.history import Message
from agents.core.runner import RunResult, StatusCallback, TurnUsage, status_detail
from agents.core.strings import quota_exhausted

logger = logging.getLogger(__name__)

# Headless flags. Centralized so they're easy to adjust for a CLI version.
#
# We do NOT use --permission-mode bypassPermissions / --dangerously-skip-
# permissions: Claude Code refuses those when running as root (which the
# container is), and they'd also let the model run ANYTHING (Bash, file edits).
# Instead we ALLOW-LIST exactly what the agent needs — no bypass, minimal
# surface. "mcp__<server>" allows every tool from the MCP server we name
# "brain" in _mcp_config(); "Read" lets the CLI view files INSIDE the container
# (how the agent looks at photos the owner sends — the connector saves them to
# /tmp and passes the path). Bash/Write/network tools stay off: the container
# holds secrets (bot token, DB access), and untrusted content (web results,
# forwarded messages) makes prompt-injection + Bash a real exfiltration path.
_ALLOWED_TOOLS = "mcp__brain Read"
# Timeout semantics (reworked 2026-07-29 after a live research turn hit the old
# flat 180s and died mid-work): a WORKING turn streams tool events continuously,
# so "stuck" is measured as SILENCE on stdout, not total duration. The hard cap
# is only a runaway backstop — a deep-research turn legitimately runs minutes.
_TOOL_TIMEOUT_S = 600.0                   # hard cap for one CLI turn (backstop)
_IDLE_TIMEOUT_S = 120.0                   # no stream event for this long = stuck
# Per-line buffer for the CLI's stdout stream. asyncio's default is 64 KiB,
# which a single stream-json event blows through as soon as a tool result
# carries a photo (base64 of a Read image) — readline() then raises and the
# whole turn dies. 32 MiB fits any Telegram photo with a wide margin.
_STREAM_LIMIT = 32 * 1024 * 1024
# The generic "the CLI exited badly and we could not tell why" reply. Named
# because it is ALSO the retry signal for a failed --resume: an expired or
# missing session lands here, while the specific failures (timeout, quota, not
# logged in) get their own text and must NOT be retried — a timeout would cost
# another ten minutes, and a spent quota would fail identically the second time.
_ERR_BACKEND = "Error: the assistant backend failed on this message."


def _result_usage(event: dict) -> TurnUsage:
    """Token/cost accounting out of the CLI's final 'result' event.

    Defensive on every field: this is telemetry, and a schema change in the CLI
    must cost us a log line, never a reply."""
    raw = event.get("usage")
    usage = raw if isinstance(raw, dict) else {}

    def _n(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) else 0

    cost = event.get("total_cost_usd")
    return TurnUsage(
        input_tokens=_n("input_tokens"),
        output_tokens=_n("output_tokens"),
        cache_read_tokens=_n("cache_read_input_tokens"),
        cache_write_tokens=_n("cache_creation_input_tokens"),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
    )


class ClaudeCliRunner:
    """Runs a turn via the `claude` CLI against Brain's MCP (Max subscription)."""

    def __init__(
        self,
        model: str,
        brain_mcp_url: str,
        agent_token: str,
        config_dir: str,
        *,
        timeout: float = _TOOL_TIMEOUT_S,
        language: str = "en",
    ) -> None:
        self._model = model
        self._mcp_url = brain_mcp_url.rstrip("/") + "/mcp"
        self._token = agent_token
        self._config_dir = config_dir
        self._timeout = timeout
        self._language = language

    def _mcp_config(self) -> str:
        """--mcp-config JSON pointing the CLI at Brain's HTTP MCP endpoint with
        this agent's bearer token."""
        return json.dumps(
            {"mcpServers": {"brain": {
                "type": "http", "url": self._mcp_url,
                "headers": {"Authorization": f"Bearer {self._token}"},
            }}}
        )

    @staticmethod
    def _transcript(messages: list[Message], runtime: str = "") -> str:
        """Serialize the recent history into a labeled transcript (the CLI takes
        a single prompt, not a message array). The last line is the new ask.

        `runtime` (the clock, the profile, the memories recalled for this
        message) is prepended HERE rather than appended to the system prompt on
        purpose: it changes every turn, and anything volatile inside
        --append-system-prompt invalidates the cached prefix behind it."""
        out = []
        for m in messages:
            who = "User" if m.get("role") == "user" else "Assistant"
            out.append(f"{who}: {m.get('content', '')}")
        body = "\n".join(out)
        return f"{runtime}\n\n---\n\n{body}" if runtime else body

    @staticmethod
    def _last_message(messages: list[Message]) -> str:
        """Just the newest message's text — the whole prompt for a RESUMED turn,
        where the CLI already holds everything before it in the session."""
        if not messages:
            return ""
        content = messages[-1].get("content", "")
        return content if isinstance(content, str) else str(content)

    def _env(self) -> dict:
        # STRIP ANTHROPIC_API_KEY so the CLI uses the Max login, not API billing.
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        if self._config_dir:
            env["CLAUDE_CONFIG_DIR"] = self._config_dir
            # Belt-and-braces: if the CLI ever "migrated" itself into
            # <config>/local (it renames the system binary when it does), make
            # that copy findable too instead of dying with "not found".
            env["PATH"] = f"{self._config_dir}/local:" + env.get("PATH", "")
        # Never let the CLI self-migrate/update out from under us mid-turn.
        env["DISABLE_AUTOUPDATER"] = "1"
        return env

    @staticmethod
    def _parse_event(
        line: bytes,
        tools_used: list[str],
        meta: dict | None = None,
    ) -> tuple[str | None, list[tuple[str, str]]]:
        """One stream-json line -> (final_text_if_result_event, statuses).
        Statuses are (bare_tool_name, detail) for each tool_use block seen.

        `meta`, when given, is filled in-place with the turn's out-of-band
        facts: "session_id" (so a follow-up turn can --resume instead of
        replaying) and "usage" (a TurnUsage — the CLI reports both tokens and
        real dollars in the result event). It is an optional OUT-parameter
        rather than a third return value so the line is parsed exactly once —
        these lines can be megabytes when a tool result carries a photo."""
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, []
        if meta is not None:
            # Present on every event type, including the 'init' one that arrives
            # before any work — so we learn the id even if the turn later dies.
            session_id = event.get("session_id")
            if isinstance(session_id, str) and session_id:
                meta["session_id"] = session_id
        if event.get("type") == "result":
            if meta is not None:
                meta["usage"] = _result_usage(event)
            return event.get("result") or "", []
        statuses: list[tuple[str, str]] = []
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    # MCP tools arrive as "mcp__brain__find_online" — strip the
                    # server prefix so callers see the same bare names the API
                    # backend reports.
                    name = str(block.get("name", "")).split("__")[-1]
                    tool_input = block.get("input") or {}
                    tools_used.append(name)
                    statuses.append((name, status_detail(tool_input)))
        return None, statuses

    async def _consume(
        self,
        proc: asyncio.subprocess.Process,
        on_status: StatusCallback | None,
        tools_used: list[str],
        meta: dict,
    ) -> str | None:
        """Read the CLI's stream-json stdout to the end. Returns the final text
        from the 'result' event (None if the stream ended without one), and
        fills `meta` with the session id and usage as they go past."""
        final: str | None = None
        while True:
            try:
                # Idle timeout, not total: each event resets the clock. A turn
                # that is actively calling tools never trips this.
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=_IDLE_TIMEOUT_S)
            except ValueError:
                # A line even bigger than _STREAM_LIMIT. Drain it chunk by
                # chunk so the stream can continue, and skip the event — losing
                # one status beats killing the turn.
                logger.warning("stream-json line exceeded %d bytes; skipping it", _STREAM_LIMIT)
                while True:
                    chunk = await proc.stdout.read(1 << 16)
                    if not chunk or b"\n" in chunk:
                        break
                continue
            if not line:
                break
            result_text, statuses = self._parse_event(line, tools_used, meta)
            if result_text is not None:
                final = result_text
            for name, detail in statuses:
                if on_status is not None:
                    try:
                        await on_status(name, detail)
                    except Exception:
                        logger.exception("on_status callback failed")
        await proc.wait()
        return final

    async def run(
        self,
        system: str,
        messages: list[Message],
        on_status: StatusCallback | None = None,
        *,
        runtime: str = "",
        resume: str | None = None,
    ) -> RunResult:
        """One turn. `system` is the STABLE head — it and only it goes into
        --append-system-prompt, so the CLI's cached prefix survives between
        messages; `runtime` rides in the prompt body instead. `resume` continues
        an earlier session (from a prior RunResult.session_id) rather than
        replaying the transcript."""
        result = await self._invoke(system, messages, on_status, runtime, resume)
        if resume is not None and result.text == _ERR_BACKEND:
            # A resume can fail for reasons that have nothing to do with this
            # turn — the session expired, or the config volume was recreated
            # under us. Replaying costs latency; NOT replaying costs the owner
            # their answer, so we retry once from scratch. Only the GENERIC
            # failure retries (see _ERR_BACKEND).
            logger.warning("Resuming session %s failed; replaying the transcript", resume)
            result = await self._invoke(system, messages, on_status, runtime, None)
        return result

    async def _invoke(
        self,
        system: str,
        messages: list[Message],
        on_status: StatusCallback | None,
        runtime: str,
        resume: str | None,
    ) -> RunResult:
        # Write the MCP config (which carries the bearer TOKEN) to a private temp
        # file rather than passing it inline on argv — otherwise the token would
        # be visible to `ps` inside the container. mkstemp creates it 0600.
        fd, cfg_path = tempfile.mkstemp(suffix=".json", prefix="mcp-")
        os.write(fd, self._mcp_config().encode())
        os.close(fd)
        # A resumed session already holds the history, the runtime block and the
        # draft — sending only the new message is the entire point of resuming.
        prompt = (
            self._last_message(messages) if resume
            else self._transcript(messages, runtime)
        )
        # stream-json (requires --verbose in -p mode) instead of plain text:
        # the CLI runs its own tool loop, and the event stream is the ONLY way
        # to see tool calls as they happen — which powers live status updates
        # and tells the Agent whether this turn searched the web (the trigger
        # for the self-verification pass).
        cmd = [
            "claude", "-p", prompt,
            "--append-system-prompt", system,
            "--mcp-config", cfg_path,
            "--model", self._model,
            "--allowedTools", _ALLOWED_TOOLS,
            "--output-format", "stream-json",
            "--verbose",
        ]
        if resume:
            cmd += ["--resume", resume]
        tools_used: list[str] = []
        # Filled by the stream parser: "session_id" and "usage".
        meta: dict = {}
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=self._env(), limit=_STREAM_LIMIT,
            )
            # Drain stderr concurrently so a chatty CLI can't fill the pipe and
            # deadlock while we're blocked on stdout.
            stderr_task = asyncio.create_task(proc.stderr.read())
            final = await asyncio.wait_for(
                self._consume(proc, on_status, tools_used, meta), timeout=self._timeout
            )
            err = await stderr_task
        except asyncio.TimeoutError:
            # wait_for only cancels our reader; the subprocess is still alive
            # (its tool loop keeps burning the Max quota). Kill and reap it so we
            # don't leak an orphan on every timeout.
            logger.error(
                "claude CLI timed out (silent >%.0fs or total >%.0fs)",
                _IDLE_TIMEOUT_S, self._timeout,
            )
            stderr_task.cancel()
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return RunResult(
                "Error: the assistant took too long to respond.",
                tools_used, usage=meta.get("usage"),
            )
        except FileNotFoundError:
            logger.error("`claude` CLI not found on PATH")
            return RunResult("Error: the CLI backend is unavailable.", tools_used)
        finally:
            os.path.exists(cfg_path) and os.remove(cfg_path)

        if proc.returncode != 0 or not (final or "").strip():
            # No/empty 'result' event counts as a failure too: returning ""
            # would make the connector silently send nothing to the owner.
            # The CLI reports some errors on stdout (e.g. login required), some on
            # stderr — log both so failures are diagnosable from `make logs`.
            err_text = err.decode(errors="replace")
            # Log the RESULT text too, not just stderr: the CLI reports most
            # failures (login required, quota, model errors) inside the result
            # event on stdout and leaves stderr empty — logging only stderr
            # produced "stderr=" with no cause at all, which is how a wiped
            # claude-auth volume looked like a mystery.
            logger.error(
                "claude CLI failed (rc=%s): result=%r stderr=%s",
                proc.returncode, (final or "")[:400], err_text[:400] or "<empty>",
            )
            combined = f"{err_text}\n{final or ''}".lower()
            # A spent subscription exits non-zero exactly like a crash, so
            # checked BEFORE the login check — "usage limit reached" contains
            # neither "login" nor "authenticat", but a quota message that also
            # mentions signing in would otherwise be reported as a login problem
            # and send the owner to re-authenticate a session that is fine.
            spent, reset_at = detect_quota(combined)
            if spent:
                return RunResult(
                    quota_exhausted(self._language, reset_at),
                    tools_used, usage=meta.get("usage"),
                )
            if "not logged in" in combined or "login" in combined or "authenticat" in combined:
                return RunResult(
                    "Error: Claude CLI is not logged in. Run once on the host: "
                    "docker compose exec kaya claude   (then /login)",
                    tools_used, usage=meta.get("usage"),
                )
            return RunResult(_ERR_BACKEND, tools_used, usage=meta.get("usage"))
        return RunResult(
            final.strip(), tools_used,
            usage=meta.get("usage"), session_id=meta.get("session_id"),
        )
