# =============================================================================
# Claude CLI runner — infra/wardenkit/clirunner.py
# =============================================================================
# WHAT: The one supported way for a project's Warden to run the `claude` CLI:
#       build the command, stream its events, and return a CliRun that carries
#       the exit code, the model's ANSWER, a diagnosable tail, and whether the
#       subscription is spent.
#
# WHY it lives in the kit and not in each project's warden.py: the first real
#       project (hashi) hand-rolled this, and the hand-rolled version had six
#       defects at once — stderr thrown away, the answer discarded on success, a
#       leading `-` in the prompt parsed as a flag, no idea what a spent quota
#       looks like, a ten-minute wait on a run that never started, and GH_TOKEN
#       readable by every agent it spawned. Every one of those is the same class
#       of bug: the kit handed a project a transport and let it write the hard
#       part itself. Written once here, they are unwritable there.
#
# WHY it does NOT parse the pipeline's output beyond the CLI's own `result`
#       event: the answer belongs to whoever produced it. A Warden's job is
#       translation — Directive in, command out, result back. Every place it
#       starts interpreting what the fleet MEANT is a second, worse copy of the
#       fleet, and it goes stale the moment a persona changes its format.
#
# HOW:  runner = ClaudeRunner(cwd="/repo", model="sonnet", effort="low")
#       run = await runner.run("what is the status of the project?",
#                              agent="product-owner-ohno")
#       if run.quota_exhausted: ...            # terminal, never `blocked`
#       if not run.ok:          ...            # run.tail says why
#       return JobResult(state="done", summary=run.answer)
# =============================================================================

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path

from infra.quota import detect_quota

logger = logging.getLogger(__name__)

# A single stream-json event can carry a whole file's contents. The default
# asyncio limit (64 KiB) turns that into a ValueError mid-run, so raise it —
# and _consume still handles the overflow rather than trusting this number.
_STREAM_LIMIT = 8 * 1024 * 1024

# How much of the merged output we keep for diagnosis. The TAIL, not the head:
# the last lines are where it actually broke, and the whole stream would flood
# a Telegram report.
_TAIL_CHARS = 8000

# Two timeouts, because "silent so far" and "silent since it began" are
# different failures with different right answers. Ten minutes is the correct
# patience for a pipeline mid-thought and the wrong patience for one that never
# started — and on a project pinned at MAX_CONCURRENT=1, each wedged run costs
# the whole slot for the full wait.
_FIRST_OUTPUT_TIMEOUT_S = 120.0
_IDLE_TIMEOUT_S = 600.0

# Variables the CLI must never see. ANTHROPIC_API_KEY: its presence silently
# switches a Max-subscription fleet onto API billing. GH_TOKEN: `gh` is
# authenticated from its own config file (see the kit's entrypoint recipe), so
# the variable is redundant here — and every agent with Bash can `printenv` it.
_STRIPPED_ENV = ("ANTHROPIC_API_KEY", "GH_TOKEN")

@dataclass
class CliUsage:
    """What one `claude -p` turn cost, in tokens and dollars.

    A self-contained twin of `agents.core.runner.TurnUsage` — wardenkit's only
    dependencies are grpcio and protobuf (it's the one thing another repo
    imports from Kaizen), so it can't import agents/core. Same fields, same
    defensive-on-every-field parsing, kept here so a project's warden.py can
    push this straight onto a Directive's Status without touching agents/core.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def __bool__(self) -> bool:
        # A turn with nothing captured (e.g. the CLI never got as far as a
        # `result` event) shouldn't push an all-zero row to the Hub.
        return bool(
            self.input_tokens or self.output_tokens
            or self.cache_read_tokens or self.cache_write_tokens
        )


@dataclass
class CliRun:
    """One `claude -p` invocation, as the caller sees it.

    `answer` is the deliverable for any kind whose output is prose. It is
    populated on SUCCESS as well as failure — the bug this replaces used the
    captured text only in the `code != 0` branch, so a research Directive
    finished, produced an answer, and reported a list of file paths.
    """

    code: int = 0
    answer: str = ""
    tail: str = ""
    started: bool = False           # did the CLI emit anything at all
    timed_out: bool = False
    quota_exhausted: bool = False
    quota_reset: str = ""           # human-readable, "" when it did not say
    tools_used: list[str] = field(default_factory=list)
    usage: CliUsage = field(default_factory=CliUsage)

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out and not self.quota_exhausted

    def failure_summary(self) -> str:
        """One line the owner can act on — never a bare exit code.

        A plain failure is visible. A failure with no stated reason sends
        somebody into `docker exec` to find out that the CLI was not logged in.
        """
        if self.quota_exhausted:
            when = f" Сброс в {self.quota_reset}." if self.quota_reset else ""
            return (
                f"⏳ Подписка исчерпана.{when} "
                "Само это не повторится — отправь заново после сброса."
            )
        if self.timed_out and not self.started:
            return (
                f"CLI не выдал ни одного события за {int(_FIRST_OUTPUT_TIMEOUT_S)}с — "
                "скорее всего он не запустился (нет логина, нет бинаря, битый флаг). "
                f"Хвост вывода: {self.tail[-600:] or '<пусто>'}"
            )
        if self.timed_out:
            return f"CLI молчал дольше {int(_IDLE_TIMEOUT_S)}с и был снят. Хвост: {self.tail[-600:]}"
        return f"claude exited {self.code}. Хвост вывода: {self.tail[-800:] or '<пусто>'}"


class ClaudeRunner:
    """Runs the `claude` CLI for one project, with the kit's defaults."""

    def __init__(
        self,
        *,
        cwd: str | Path = "/repo",
        model: str = "sonnet",
        effort: str = "",
        config_dir: str = "",
        allowed_tools: str = "",
        permission_mode: str = "",
        first_output_timeout: float = _FIRST_OUTPUT_TIMEOUT_S,
        idle_timeout: float = _IDLE_TIMEOUT_S,
        total_timeout: float | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        self._cwd = str(cwd)
        self._model = model
        # Declared, not implicit — and defaulting LOW deliberately. The caveat
        # belongs right here, where it is set: low is right while the fleet
        # writes plans and decisions, and WRONG once it produces code. Effort
        # not spent writing is effort a reviewer pays back with interest, so a
        # /develop pipeline should raise this per phase rather than inherit it.
        self._effort = effort
        self._config_dir = config_dir
        self._allowed_tools = allowed_tools
        # "" renders no flag — and in `-p` mode that means the CLI AUTO-DENIES
        # every Write/Edit and still exits 0, so a pipeline "succeeds" having
        # produced nothing but an apology. A fleet that edits files must set
        # this (the kit's warden template does); a consult-only runner may not.
        self._permission_mode = permission_mode
        self._first_output_timeout = first_output_timeout
        self._idle_timeout = idle_timeout
        self._total_timeout = total_timeout
        self._extra_args = list(extra_args or [])

    # -- command and environment -------------------------------------------
    def _env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
        if self._config_dir:
            env["CLAUDE_CONFIG_DIR"] = self._config_dir
            # If the CLI ever "migrated" itself into <config>/local (it renames
            # the system binary when it does), make that copy findable instead
            # of dying with "not found".
            env["PATH"] = f"{self._config_dir}/local:" + env.get("PATH", "")
        # Never let the CLI self-update out from under a running fleet: it
        # migrates itself into the mounted config dir, and the next volume wipe
        # then deletes the binary.
        env["DISABLE_AUTOUPDATER"] = "1"
        return env

    def _build(
        self, prompt: str, *, system: str, agent: str, model: str, effort: str
    ) -> list[str]:
        # A prompt is passed as an ARGUMENT VALUE, so a leading dash is parsed
        # as an option name and the run dies with the entire prompt echoed back
        # as `error: unknown option '--- what the owner said …'`. Trivially
        # triggered by any templated prefix that opens with `---`, which is why
        # the kit's prompt templates use `===` instead. One guard here covers
        # every caller.
        if prompt.startswith("-"):
            prompt = "\n" + prompt
        cmd = [
            "claude", "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",                       # required by stream-json in -p mode
            "--model", model or self._model,
        ]
        # `--agent` is what makes a conversation cheap: it consults ONE persona
        # instead of running a slash command, and every slash command runs a
        # pipeline, creates directories and reports paths. "What is the status
        # of the project?" must cost one persona-turn, not a fleet run.
        if agent:
            cmd += ["--agent", agent]
        if system:
            cmd += ["--append-system-prompt", system]
        eff = effort or self._effort
        if eff:
            cmd += ["--effort", eff]
        if self._allowed_tools:
            cmd += ["--allowedTools", self._allowed_tools]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        return cmd + self._extra_args

    # -- the run -----------------------------------------------------------
    async def run(
        self,
        prompt: str,
        *,
        system: str = "",
        agent: str = "",
        model: str = "",
        effort: str = "",
        on_event=None,
    ) -> CliRun:
        """Run one turn and return everything the caller could need to report.

        Never raises on a CLI failure: a failed run is a RESULT, and a handler
        that has to wrap this in try/except ends up reporting the exception
        instead of the reason.
        """
        cmd = self._build(prompt, system=system, agent=agent, model=model, effort=effort)
        out = CliRun()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self._cwd,
                # Its own process GROUP, so a kill reaches the whole tree. The
                # CLI routinely starts children through Bash (a dev server, an
                # MCP process); killing only the parent leaves them holding our
                # stdout pipe — and burning the subscription — after a timeout.
                start_new_session=True,
                stdout=asyncio.subprocess.PIPE,
                # MERGED, not a separate pipe. Everything the CLI reports before
                # it can emit JSON — no credential, bad flag, missing binary —
                # goes to stderr, and a pipe nobody reads both loses that AND
                # fills its buffer until the child wedges. The drain below only
                # collects, never parses, so merging is safe.
                stderr=asyncio.subprocess.STDOUT,
                env=self._env(),
                limit=_STREAM_LIMIT,
            )
        except FileNotFoundError:
            out.code = 127
            out.tail = "`claude` not found on PATH inside the container"
            return out

        try:
            await asyncio.wait_for(self._consume(proc, out, on_event),
                                   timeout=self._total_timeout)
        except asyncio.TimeoutError:
            out.timed_out = True
        finally:
            if proc.returncode is None:
                # wait_for only cancels OUR reader — the child is still alive and
                # its tool loop keeps burning the subscription. Kill the whole
                # group (see start_new_session above) so a timeout does not leak
                # an orphan tree on every wedged run.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
            # BOUNDED, deliberately: asyncio's wait() resolves only once every
            # inherited pipe is CLOSED, and a live grandchild holding stdout
            # keeps it open — verified to hold this coroutine (and the
            # heartbeat riding on it) for the grandchild's whole lifetime.
            # A handler that never returns never reports, the lease never
            # expires, and at MAX_CONCURRENT=1 the project is dead until a
            # human restarts the container. Ten seconds, then report the kill.
            try:
                out.code = await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.error(
                    "child not reaped in 10s (a grandchild still holds the "
                    "pipe?) — reporting -9"
                )
                out.code = -9

        self._detect_quota(out)
        if not out.ok:
            logger.warning("claude failed: %s", out.failure_summary())
        return out

    async def _consume(self, proc, out: CliRun, on_event) -> None:
        """Read the merged stream to the end, filling `out` as it goes."""
        tail: list[str] = []
        tail_len = 0
        while True:
            # The FIRST read gets the short timeout, every later one the long
            # idle timeout. Each event resets the clock, so a turn that is
            # actively calling tools never trips it.
            budget = self._idle_timeout if out.started else self._first_output_timeout
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=budget)
            except asyncio.TimeoutError:
                out.timed_out = True
                break
            except ValueError:
                # A line bigger than _STREAM_LIMIT. Drain it chunk by chunk so
                # the stream can continue — losing one event beats killing a run
                # that is otherwise fine.
                logger.warning("stream-json line exceeded %d bytes; skipping", _STREAM_LIMIT)
                while True:
                    chunk = await proc.stdout.read(1 << 16)
                    if not chunk or b"\n" in chunk:
                        break
                continue
            if not line:
                break
            out.started = True
            text = line.decode(errors="replace")
            tail.append(text)
            tail_len += len(text)
            while tail_len > _TAIL_CHARS and len(tail) > 1:
                tail_len -= len(tail.pop(0))
            self._parse_event(text, out, on_event)
        out.tail = "".join(tail).strip()

    @staticmethod
    def _parse_event(text: str, out: CliRun, on_event) -> None:
        """One stream-json line → the answer, the tools used, an optional hook.

        Non-JSON lines are the POINT of merging stderr, so they are kept in the
        tail and otherwise ignored rather than treated as an error.
        """
        try:
            event = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "result":
            # The CLI's closing text. For any Directive whose deliverable is
            # prose, THIS is the deliverable — captured on success too.
            out.answer = str(event.get("result") or "").strip()
            out.usage = ClaudeRunner._result_usage(event)
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    # MCP tools arrive as "mcp__brain__find_online" — strip the
                    # server prefix so callers see bare names.
                    out.tools_used.append(str(block.get("name", "")).split("__")[-1])
        if on_event is not None:
            try:
                on_event(event)
            except Exception:
                logger.exception("on_event callback failed")

    @staticmethod
    def _result_usage(event: dict) -> CliUsage:
        """Token/cost accounting out of the CLI's final 'result' event.

        Mirrors `agents/core/cli.py`'s `_result_usage` — same event shape,
        same defensiveness: this is telemetry, and a schema change in the CLI
        must cost us a log line, never a crashed pipeline."""
        raw = event.get("usage")
        usage = raw if isinstance(raw, dict) else {}

        def _n(key: str) -> int:
            value = usage.get(key)
            return value if isinstance(value, int) else 0

        cost = event.get("total_cost_usd")
        return CliUsage(
            input_tokens=_n("input_tokens"),
            output_tokens=_n("output_tokens"),
            cache_read_tokens=_n("cache_read_input_tokens"),
            cache_write_tokens=_n("cache_creation_input_tokens"),
            cost_usd=float(cost) if isinstance(cost, (int, float)) else 0.0,
        )

    @staticmethod
    def _detect_quota(out: CliRun) -> None:
        """Recognise a spent subscription and say so, with the reset time.

        WHY this must never be reported as `blocked`: `blocked` is a LEASED
        state — it means "an agent asked the owner a question and is holding the
        job open", and the kit heartbeats through it deliberately. A Warden that
        returns `blocked` and stops heartbeating leaves the lease to expire
        (120s), the sweeper requeues (every 30s), the quota is still spent, and
        the Directive loops every two and a half minutes for the whole outage,
        notifying the owner on every turn. Terminal and clearly labelled is the
        honest shape.

        Only on a run that FAILED (nonzero exit): a spent subscription always
        exits nonzero, while a successful answer may merely DISCUSS these
        marker strings — the fleet working on this very repo does, constantly
        — and flagging that would turn a finished run into a fake outage.
        """
        if out.code == 0:
            return
        out.quota_exhausted, out.quota_reset = detect_quota(f"{out.tail}\n{out.answer}")


__all__ = ["ClaudeRunner", "CliRun", "CliUsage"]
