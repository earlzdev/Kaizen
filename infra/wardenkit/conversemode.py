# =============================================================================
# Conversation mode — infra/wardenkit/conversemode.py
# =============================================================================
# WHAT: `run_conversation()` — the handler for a `converse`-kind Directive
#       (the "позови альфреда" tunnel): a LOOP, not a one-shot turn like every
#       other kind. It waits for the owner's next message, runs one `claude`
#       CLI turn, pushes the reply back to the Hub, and waits again — forever,
#       until the Directive is Cancelled (the owner said "выход").
#
# WHY this never returns a JobResult on its own: every other kind has a
#       moment where the work is simply done. A conversation doesn't — there
#       is no reply that means "the dialogue is over" the way a PR being
#       opened means a /develop run is. Ending it is the OWNER's decision
#       (Cancel), which WardenServicer already handles for every kind by
#       cancelling this coroutine — conversemode needs no special-case for
#       that, same as it needs none for any other kind's cancellation.
#
# WHY it reuses ConversationLog and repocontext instead of its own history:
#       a tunnel turn and an `ask` turn are the same shape — the owner's
#       words in, a persona's words out — so they share the one durable
#       record of "what has this project already told the owner". Splitting
#       them would mean the persona forgetting an `ask` answer mid-tunnel and
#       vice versa, for no reason but which RPC carried the words.
#
# HOW: a project's warden.py routes job.kind == "converse" here:
#       return await run_conversation(job, runner, PERSONA, log, repo_root=REPO_ROOT)
# =============================================================================

from __future__ import annotations

import asyncio

from infra.wardenkit.clirunner import ClaudeRunner
from infra.wardenkit.conversation import ConversationLog
from infra.wardenkit.repocontext import build_context
from infra.wardenkit.servicer import DirectiveJob, JobResult

# A project's capacity is usually 1 (MAX_CONCURRENT) — an open conversation
# holds that one Warden slot for as long as it stays open. Forgetting to say
# "выход" must not freeze the project's whole queue indefinitely, so an idle
# tunnel closes itself and frees the slot; the owner can reopen it any time.
_DEFAULT_IDLE_SECONDS = 30 * 60


async def run_conversation(
    job: DirectiveJob,
    runner: ClaudeRunner,
    slug: str,
    log: ConversationLog,
    *,
    repo_root: str = ".",
    role: str = "consultant",
    idle_seconds: int = _DEFAULT_IDLE_SECONDS,
) -> JobResult:
    """Loop: wait for a message, answer it, push the reply, repeat.

    Two ways out: the owner ends it (Cancel -> asyncio.CancelledError from
    `job.next_message()`, propagating exactly like a cancellation through any
    other kind's handler), or the tunnel sits idle past `idle_seconds`, in
    which case this returns a normal JobResult(state="done") — same terminal
    path as any other kind finishing on its own.
    """
    await job.status(slug, "in_progress", role=role, phase="converse", progress="слушаю")
    while True:
        try:
            text = await asyncio.wait_for(job.next_message(), timeout=idle_seconds)
        except asyncio.TimeoutError:
            closing = "Разговор закрыт по неактивности — начни заново, если нужно."
            if job.hub is not None:
                await job.hub.push_chat_message(job.id, closing, agent_slug=slug, closed=True)
            return await job.finish(
                slug, JobResult(state="done", summary=closing),
                role=role, phase="converse", progress="закрыт по неактивности",
            )
        history = log.render()
        context = build_context(repo_root)
        prompt = (
            (context + "\n\n" if context else "")
            + (history + "\n\n" if history else "")
            + "=== Сообщение владельца ===\n"
            + text
            + "\n\n=== Как отвечать ===\n"
            + "Это ЖИВОЙ разговор, не разовый вопрос — дальше будут ещё "
            + "сообщения от владельца в этой же сессии. Ответь ТЕКСТОМ, на "
            + "языке сообщения. Не запускай пайплайн, не открывай PR."
        )
        run = await runner.run(prompt, agent=slug)
        reply = run.answer if run.ok else run.failure_summary()
        await job.status(
            slug, "done" if run.ok else "failed", role=role, phase="converse",
            progress=reply[:200], usage=run.usage,
        )
        log.append(text, reply)
        if job.hub is not None:
            await job.hub.push_chat_message(job.id, reply, agent_slug=slug)


__all__ = ["run_conversation"]
