# =============================================================================
# Agent facade — agents/core/agent.py
# =============================================================================
# WHAT: Ties the pieces together into the one thing a connector calls:
#       `await agent.reply(user_text) -> reply_text`. It builds the system
#       prompt (soul + recalled memories + clock), loads local history, runs the
#       tool-use loop against Brain, and persists the turn.
#
# WHY the prompt is built as TWO strings, not one (this is the whole prompt-cache
#       story): the head — soul + protocol + tool usage notes, ~20 KB — is
#       byte-identical on every turn; the runtime block — the clock, the owner
#       profile, the memories recalled for THIS message — changes every time.
#       They used to be concatenated here and handed to the runner as a single
#       `system` string, which meant the volatile tail invalidated the cached
#       prefix right at the soul boundary and we re-paid for the whole head on
#       every message, every tool iteration. Now they travel separately and each
#       backend places them where its cache can hold: a marked system block +
#       an unmarked one (API), or --append-system-prompt + the prompt body (CLI).
#       Assemble ONLY stable text into the head — anything with a timestamp,
#       a recall result or a counter in it belongs in the runtime block.
#
# WHY recall runs before the loop (and is cached in the client): the agent pulls
#       facts relevant to THIS message from Brain's shared memory and injects
#       them, so the agent "remembers everything" without the prompt growing. The
#       BrainMCPClient caches recall (the plan's latency mitigation), so a burst
#       of related messages costs one round trip, not one per message.
#
# WHY recall failure never blocks a reply: memory is an enhancement, not a
#       dependency. If Brain's recall errors, we log and answer without it — the
#       agent degrades to "no recall", it does not go mute.
#
# HOW: construct with a soul string, an LLMClient, a BrainMCPClient, and a
#       History; call reply(). The connector (Telegram/voice) owns I/O.
# =============================================================================

import datetime
import logging
import time
from zoneinfo import ZoneInfo

from agents.core.history import History, Message
from agents.core.llm import LLMClient
from agents.core.loop import AgentLoop
from agents.core.mcp_client import BrainMCPClient
from agents.core.prompts import (
    AGENT_WAKE_HISTORY,
    AGENT_WAKE_SKIP,
    AGENT_WAKE_TEMPLATE,
    CURRENT_TIME_TEMPLATE,
    EMPTY_RECALL_MARKER,
    FINAL_MARKER,
    SEARCH_OPS_CAP,
    SEARCH_PROTOCOL,
    STYLE_REQUEST_TEMPLATE,
    TRACKER_EVENT_HISTORY,
    TRACKER_EVENT_TEMPLATE,
    VERIFY_OK_MARKER,
    VERIFY_REQUEST_TEMPLATE,
)
from agents.core.runner import Runner, RunResult, StatusCallback, TurnUsage
from agents.core.tool_usage import render_tool_usage

logger = logging.getLogger(__name__)

# Tools whose use marks a turn as "researched the web" — those turns get the
# fact-checking self-verification pass before the answer is sent. Name used by
# the status callback for the verification phase itself: "self_check".
SEARCH_TOOLS = {"find_online", "read_page", "WebSearch", "WebFetch"}

# Drafts at least this long get the style-only gate even without search: long
# answers are where the model slides back into its default AI register. Short
# replies skip it — gating "ok, done" would double latency for nothing.
STYLE_GATE_MIN_CHARS = 300

# Quick-lookup exemption (owner's call, 2026-07-29): a SHORT answer from a
# SHALLOW search (this many ops or fewer) skips the fact-check — re-verifying
# «what's the weather» doubles latency for one low-stakes claim the model just read.
# Deep research or a long answer still always gets the full pass.
QUICK_LOOKUP_MAX_OPS = 3

# How many search operations a turn must have made before the owner is TOLD
# that facts are being re-checked (owner's call, 2026-07-29): «double-checking
# the facts» on a turn that did one lookup — or none at all, when only the style
# gate ran — reads as random noise. The gate itself still runs; only the
# status line is gated, so the owner sees it exactly when a real research
# turn is genuinely being verified.
STATUS_SELF_CHECK_MIN_OPS = 2


class Agent:
    """One agent: soul + Brain (tools/memory) + local history + tool loop."""

    def __init__(
        self,
        *,
        soul: str,
        brain: BrainMCPClient,
        history: History,
        runner: Runner | None = None,
        llm: LLMClient | None = None,
        gate_runner: Runner | None = None,
        recall_enabled: bool = True,
        timezone: str = "UTC",
    ) -> None:
        self._soul = soul
        self._brain = brain
        self._history = history
        self._recall_enabled = recall_enabled
        self._timezone = timezone
        # The turn engine: an explicit runner (e.g. ClaudeCliRunner), or — the
        # default API backend — the tool-use loop over `llm` + Brain's tools.
        if runner is not None:
            self._runner: Runner = runner
        elif llm is not None:
            self._runner = AgentLoop(llm, brain)
        else:
            raise ValueError("Agent needs either a runner or an llm")
        # Optional cheaper engine for the self-check gate (_final_gate). The
        # style-only gate is deletion work — spotting a cliché and cutting it —
        # which a smaller model does about as well for a fraction of the cost,
        # and the gate fires on a large share of turns. Default None = reuse the
        # main runner, which also unlocks session resume (see _final_gate).
        self._gate_runner = gate_runner

    async def reply(self, user_text: str, on_status: StatusCallback | None = None) -> str:
        """Handle one user message end to end and return the reply text.
        `on_status` (optional) receives live progress: each tool call the turn
        makes, plus a "self_check" phase when the draft gets fact-checked."""
        await self._history.append("user", user_text)
        messages = await self._history.load()
        if not messages:
            # Shouldn't happen (we just appended), but guard the loop's contract.
            messages = [{"role": "user", "content": user_text}]

        head, runtime = await self._build_prompt(user_text)
        result = await self._runner.run(head, messages, on_status, runtime=runtime)
        text = await self._final_gate(head, runtime, messages, result, on_status)

        # An empty (or failed) reply is NOT persisted: the Messages API rejects
        # a message with empty content, so a single such turn inside the history
        # window breaks EVERY later request — the agent goes mute until it is
        # restarted. The failed turn is still visible to the connector (it gets
        # the text back and tells the owner); it just never becomes context.
        if text and not text.startswith("Error:"):
            await self._history.append("assistant", text)
        await self._archive_exchange(user_text, text)
        return text

    async def wake(self, note: str, on_status: StatusCallback | None = None) -> str | None:
        """Run a turn started by the agent's OWN fired self-reminder.

        Returns the text to send the owner, or None when the agent decided the
        follow-through was to stay quiet.

        WHY this is a sibling of reply() and not a special user message: the
        turn needs the SAME context a normal turn has — recent history, recalled
        memories, the clock, the tools — but a different framing (nobody asked
        anything; the note is an instruction to itself). Everything except the
        framing is deliberately shared.

        WHY recall runs on the NOTE and not on the whole wake prompt: the note
        is the actual subject ("the owner landed — ask how the flight went"), while
        the surrounding template is scaffolding that would only add noise to a
        semantic search.

        WHY history stores a SHORT marker while the model sees the long
        template: future turns need to know a self-note fired (so the owner's
        reply has something to answer), but they must not re-read the
        instructions — and the owner's next message must not look like a reply
        to a wall of harness text."""
        return await self._self_initiated_turn(
            seed=note,
            marker=AGENT_WAKE_HISTORY.format(note=note),
            prompt=AGENT_WAKE_TEMPLATE.format(note=note, skip=AGENT_WAKE_SKIP),
            label="Wake",
            on_status=on_status,
        )

    async def notify(
        self, event: str, on_status: StatusCallback | None = None
    ) -> str | None:
        """Run a turn started by NEWS FROM A MODULE — today, the project tracker.

        Returns the text to send the owner, or None to stay quiet.

        WHY a turn and not a relayed string: the event may be a question one of
        a project's agents is blocked on, and answering it means calling a tool.
        A relay would leave the owner replying into nothing. It also lets the
        agent check the directive's current state before speaking, instead of
        repeating a line that may already be stale.

        WHY it shares everything with wake() except the framing: the two differ
        only in who wrote the seed text — the agent itself, or a module. History,
        recall, the clock, the tools and the silence protocol are identical, and
        keeping them literally shared is what stops them drifting apart."""
        return await self._self_initiated_turn(
            seed=event,
            marker=TRACKER_EVENT_HISTORY.format(event=event),
            prompt=TRACKER_EVENT_TEMPLATE.format(event=event, skip=AGENT_WAKE_SKIP),
            label="Tracker event",
            on_status=on_status,
        )

    async def _self_initiated_turn(
        self,
        *,
        seed: str,
        marker: str,
        prompt: str,
        label: str,
        on_status: StatusCallback | None = None,
    ) -> str | None:
        """One turn nobody asked for: a fired self-note, or news from a module.

        `seed` is the subject (what recall searches on — the surrounding
        template is scaffolding that would only add noise to a semantic
        search); `marker` is the short line persisted to history; `prompt` is
        the full framing the model sees THIS turn only.
        """
        await self._history.append("user", marker)
        messages = await self._history.load()
        # Swap the just-persisted marker for the full instructions, THIS turn
        # only — same trick as the self-check gate: never persist scaffolding.
        messages = messages[:-1] + [{"role": "user", "content": prompt}]

        head, runtime = await self._build_prompt(seed)
        result = await self._runner.run(head, messages, on_status, runtime=runtime)
        # Silence is decided BEFORE the self-check gate: a turn that researched
        # (>QUICK_LOOKUP_MAX_OPS ops) would otherwise send the bare marker
        # through the verifier, which can rewrite it into a real message — and
        # "the owner is left in peace" is exactly what must not be negotiable.
        if (result.text or "").strip().startswith(AGENT_WAKE_SKIP):
            logger.info("%s turn: agent chose silence", label)
            return None
        text = await self._final_gate(head, runtime, messages, result, on_status)

        if not text or text.startswith("Error:"):
            # A failed turn stays silent: the owner never asked for this
            # message, so an error apology out of nowhere would be worse
            # than nothing. The marker in history records that it fired.
            logger.warning("%s turn produced no usable text; staying silent", label)
            return None
        if text.strip().startswith(AGENT_WAKE_SKIP):
            logger.info("%s turn: agent chose silence", label)
            return None

        await self._history.append("assistant", text)
        await self._archive_exchange(marker, text)
        return text

    async def _archive_exchange(self, user_text: str, reply_text: str) -> None:
        """Log the finished exchange to Brain's conversation archive (episodic
        memory) so it stays searchable after it rolls out of the local history
        window. Best-effort: the archive must never break a reply."""
        if not reply_text or reply_text.startswith("Error:"):
            return  # a failed turn is not a conversation worth finding later
        try:
            await self._brain.call_tool(
                "log_conversation",
                {"owner_message": user_text, "agent_reply": reply_text},
            )
        except Exception:
            logger.exception("Failed to archive the exchange in Brain (non-fatal)")

    async def _final_gate(
        self,
        head: str,
        runtime: str,
        messages: list[Message],
        result: RunResult,
        on_status: StatusCallback | None,
    ) -> str:
        """One bounded self-check turn before anything reaches the owner:
        - a turn that searched the web gets the FULL check — every factual
          claim against an actually-opened source (re-searching where proof is
          missing) plus voice-rule violations;
        - a long turn without search gets the style-only check (voice rules
          are prompt-side too, but the gate is what actually enforces them —
          the model invents new clichés faster than a ban list grows).

        WHY one pass and not a loop: the check turn can itself run several
        tool operations, so it IS the "keep searching" cycle — but with a hard
        stop. Unbounded loops would mean unbounded waits when the internet
        simply doesn't contain the proof.

        WHY it RESUMES the draft's session when it can: the gate is a second
        full turn, and it used to be dispatched as one — same head, same
        runtime block, the entire transcript replayed, just to ask "is this
        draft OK?". On a backend that keeps sessions (the CLI) that doubled the
        cost of every gated turn for information the backend was already
        holding. Resuming sends the check request alone. It is skipped when a
        separate gate runner is configured, since that is a different model and
        a different session — there is nothing of ours to resume into.

        The draft is never persisted — history gets only the final text."""
        text = result.text
        if not text or text.startswith("Error:"):
            self._log_usage(result.usage, gated=False)
            return text
        searched = [t for t in result.tools_used if t in SEARCH_TOOLS]
        if searched and len(searched) <= QUICK_LOOKUP_MAX_OPS and len(text) < STYLE_GATE_MIN_CHARS:
            self._log_usage(result.usage, gated=False)
            return text  # quick lookup, short answer — not worth a second turn
        if searched:
            remaining = max(1, SEARCH_OPS_CAP - len(searched))
            request = VERIFY_REQUEST_TEMPLATE.format(remaining=remaining)
        elif len(text) >= STYLE_GATE_MIN_CHARS:
            request = STYLE_REQUEST_TEMPLATE
        else:
            self._log_usage(result.usage, gated=False)
            return text
        # Announce the check ONLY when it is a real research verification:
        # the style-only gate says nothing about facts, and a single lookup
        # isn't worth telling the owner about (see STATUS_SELF_CHECK_MIN_OPS).
        if on_status is not None and len(searched) >= STATUS_SELF_CHECK_MIN_OPS:
            try:
                await on_status("self_check", "")
            except Exception:
                logger.exception("on_status callback failed")
        gate_messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": request},
        ]
        # A dedicated gate runner is a different model/session: it gets the full
        # transcript. The main runner gets the session handle and, on a backend
        # that keeps one, sends only `request`.
        runner = self._gate_runner or self._runner
        resume = None if self._gate_runner is not None else result.session_id
        try:
            verdict = await runner.run(
                head, gate_messages, on_status, runtime=runtime, resume=resume
            )
        except Exception:
            # The gate is a quality filter, not a dependency — never let it
            # eat an already-written answer.
            logger.exception("Self-check turn failed; sending the draft")
            self._log_usage(result.usage, gated=True)
            return text
        self._log_usage((result.usage or TurnUsage()) + verdict.usage, gated=True)
        vtext = verdict.text.strip()
        if not vtext or vtext.startswith("Error:"):
            return text
        if vtext.startswith(VERIFY_OK_MARKER):
            logger.info("Self-check: draft approved (search ops: %d)", len(searched))
            return text
        # Strip everything up to the FINAL marker — that's where checker
        # commentary lives when the model chats before the answer. A rewrite
        # with NO marker is suspect (the checker ignored the output contract):
        # keep the draft rather than risk sending commentary to the owner.
        if FINAL_MARKER in vtext:
            logger.info("Self-check: draft corrected (search ops: %d)", len(searched))
            return vtext.split(FINAL_MARKER, 1)[1].strip() or text
        logger.warning("Self-check rewrite lacked %s — keeping the draft", FINAL_MARKER)
        return text

    # How long a fetched owner profile stays fresh (it changes rarely; a cached
    # copy avoids a Brain round trip on every message).
    _PROFILE_TTL_S = 300.0

    def _log_usage(self, usage: TurnUsage | None, *, gated: bool) -> None:
        """Record what the turn cost. Nothing else in the system does — without
        this line the only visible symptom of an expensive turn is the Max
        subscription running out. `gated` says whether the self-check second
        turn is included, which is the single biggest swing in the number.

        Watch cache_read: after the first message of a conversation it should
        dominate `in`. If it sits at 0, some volatile text has leaked into the
        prompt head and the cached prefix is being rebuilt every turn."""
        if usage is None:
            return
        if not (usage.input_tokens or usage.output_tokens or usage.cache_read_tokens):
            return  # a backend that reports nothing — don't log an empty line
        logger.info("Turn usage (gate=%s): %s", "yes" if gated else "no", usage.summary())

    async def _build_prompt(self, user_text: str) -> tuple[str, str]:
        """Build the turn's prompt as (stable head, volatile runtime block).

        The head is the soul (per-agent identity), the shared search protocol
        and the tools' own usage notes — byte-identical between turns, so a
        backend can cache it. The runtime block is the clock, the owner profile
        and the memories recalled for THIS message.

        They are returned SEPARATELY and must not be joined here: that is what
        lets each backend place the volatile half after its cache breakpoint
        (see the module header). On the CLI backend the head travels via
        --append-system-prompt — the only system channel that reaches the model
        there, since the CLI is its own MCP client and would drop Brain's
        non-standard `usage` field — and the runtime block rides in the prompt
        body instead."""
        head = [self._soul, SEARCH_PROTOCOL]
        usage_block = await self._tool_usage_block()
        if usage_block:
            head.append(usage_block)

        runtime = ["## Runtime context"]
        runtime.append(self._now_line())

        profile = await self._profile_line()
        if profile:
            runtime.append(profile)

        if self._recall_enabled:
            try:
                recall_text = await self._brain.recall(user_text)
                if recall_text and recall_text.strip() != EMPTY_RECALL_MARKER:
                    runtime.append(recall_text)
            except Exception:
                # Memory must never take down a reply — degrade to no recall.
                logger.exception("Recall failed; replying without memory")

        return "\n\n".join(head), "\n\n".join(runtime)

    async def _tool_usage_block(self) -> str:
        """The tools' own usage notes, rendered for THIS agent's visible tool
        set. Cached inside the Brain client, so this costs one round trip per
        session. Non-fatal: if Brain is unreachable we simply omit the block —
        a missing hint must never cost the owner a reply."""
        try:
            notes = await self._brain.usage_notes()
        except Exception:
            logger.exception("Tool usage notes unavailable; omitting the block")
            return ""
        return render_tool_usage(notes)

    async def _profile_line(self) -> str | None:
        """The owner's profile (timezone/home) from Brain, cached ~5 min.
        Non-fatal: any failure just omits the line."""
        cached = getattr(self, "_profile_cache", None)
        now = time.monotonic()
        if cached is not None and (now - cached[0]) < self._PROFILE_TTL_S:
            return cached[1]
        try:
            text, is_error = await self._brain.call_tool("get_profile", {})
        except Exception:
            logger.exception("Profile fetch failed; omitting from runtime context")
            return cached[1] if cached else None
        line = None if (is_error or text.startswith("No profile")) else text
        self._profile_cache = (now, line)
        return line

    def _now_line(self) -> str:
        """Current time in the agent's timezone, minute precision."""
        try:
            tzinfo = ZoneInfo(self._timezone)
            tz = self._timezone
        except Exception:
            tzinfo, tz = ZoneInfo("UTC"), "UTC"
        now = datetime.datetime.now(tzinfo).strftime("%A, %Y-%m-%d %H:%M")
        return CURRENT_TIME_TEMPLATE.format(now=now, tz=tz)
