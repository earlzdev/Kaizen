# =============================================================================
# {{PROJECT}} Warden — deploy/warden/warden.py
# =============================================================================
# WHAT: The project side of the Kaizen tracker. It accepts Directives from the
#       Hub, turns each one into a `claude` CLI run, and reports the outcome.
#       Everything fiddly — enrollment, heartbeats, capacity, cancellation, the
#       status relay, the CLI itself — lives in `infra.wardenkit`.
#
# WHY this file is rendered from the kit and not written per project: the first
#       project that wrote it by hand shipped six defects in this one file
#       (a failing run reporting `done`, failure reasons discarded, the answer
#       thrown away on success, a prompt starting with `-` killing the run, a
#       spent subscription looking like a crash, and GH_TOKEN readable by every
#       agent). They are fixed in the kit now; do not re-derive them here.
#
# HOW to change it: edit `infra/agentkit/templates/` in KAIZEN and re-render.
#       Editing this copy makes this project quietly diverge from every other
#       one, and nobody can tell later which projects predate a fix.
# =============================================================================

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path

from infra.wardenkit import (
    ClaudeRunner,
    ConversationLog,
    DirectiveJob,
    HubClient,
    JobResult,
    WardenServicer,
    make_manifest,
    run_conversation,
    serve,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("{{PROJECT}}-warden")

HUB_ADDR = os.environ.get("HUB_ADDR", "tracker:9104")
NAME = os.environ.get("PROJECT_NAME", "{{PROJECT}}")
GRPC_ADDR = os.environ.get("GRPC_ADDR", "{{GRPC_ADDR}}")
BIND_ADDR = os.environ.get("BIND_ADDR", "0.0.0.0:9200")
REPO_ROOT = os.environ.get("REPO_ROOT", "/repo")
STATE = os.environ.get("STATE_DIR", "/state")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "{{MAX_CONCURRENT}}"))

# Reasoning effort, declared rather than inherited — and LOW by default.
# The caveat belongs right here, where it is set: low is right while the fleet
# writes plans, briefs and decisions, and WRONG once it produces code. Effort
# not spent writing is effort a reviewer pays back with interest. Raise it for
# the code-producing kinds below, not globally.
EFFORT = os.environ.get("REASONING_EFFORT", "low")
CODE_EFFORT = os.environ.get("CODE_REASONING_EFFORT", "medium")

# Which slash command each kind runs. The Hub's vocabulary is fixed and small,
# so anything not in here is refused BY NAME rather than routed to something
# adjacent — a planning ask answered by the code-review pipeline produces a
# confident review of somebody else's commit.
KIND_COMMANDS = {{KIND_COMMANDS}}

# `ask` is the exception, and deliberately so: it maps to a PERSONA, not a
# command. Every slash command runs a pipeline, creates directories and reports
# paths, and "what is the status of the project?" must cost one persona-turn.
ASK_PERSONA = "{{ASK_PERSONA}}"

# Where each kind's real deliverable lands. Globbed for the report, so the owner
# is told the name of the thing that was produced. The tracker tree alone is not
# enough: /research writes to docs/research/<id>/, and a report that listed only
# the briefs never named the actual report.
ARTIFACT_GLOBS = {{ARTIFACT_GLOBS}}

ROSTER = {{ROSTER}}

# The kinds this fleet can actually run. Declaring one you cannot run means the
# Hub sends work nobody handles.
KINDS = {{KINDS}}


def _runner(*, effort: str, agent: str = "") -> ClaudeRunner:
    return ClaudeRunner(
        cwd=REPO_ROOT,
        model=os.environ.get("CLAUDE_MODEL", "sonnet") if agent else os.environ.get("CLAUDE_MODEL", "opus"),
        effort=effort,
        config_dir=os.environ.get("CLAUDE_CONFIG_DIR", f"{STATE}/claude"),
        # Without a mode, `claude -p` AUTO-DENIES every Write/Edit and still
        # exits 0 — so a /develop "succeeds" having produced nothing but a
        # polite apology, and run.ok reads it as done. Bypass is the honest
        # mode for this container: it runs non-root, /repo/.env is the mounted
        # template, and GH_TOKEN is already stripped from the environment.
        permission_mode="bypassPermissions",
    )


def _tree_fingerprint() -> str:
    """`git status --porcelain`, or "" when this is not a checkout.

    Used to DISCLOSE what a run changed rather than assume it changed nothing.
    A consulted persona that is allowed to act is more useful than a read-only
    one — a read-only mode cannot even run `gh pr list`, and answers questions
    about live state by reconstructing it from the tree, which is how one PR was
    reported open and another absent when the truth was the reverse. Allowed to
    act, obliged to disclose.
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _artifacts(job: DirectiveJob) -> list[dict]:
    """Everything this Directive's command actually wrote, as report artifacts."""
    found: list[dict] = []
    for pattern in ARTIFACT_GLOBS.get(job.kind, []) + ARTIFACT_GLOBS.get("*", []):
        for path in sorted(Path(REPO_ROOT).glob(pattern.replace("{task_id}", job.task_id))):
            if path.is_file():
                found.append({"type": "file", "url": str(path.relative_to(REPO_ROOT))})
    return found


async def _fail(job: DirectiveJob, agent: str, run, *, phase: str) -> JobResult:
    """One terminal failure path, used by every kind.

    Quota is reported TERMINALLY and never as `blocked`: `blocked` is a leased
    state meaning "an agent asked the owner a question and is holding the job
    open". A Warden that returns it and stops heartbeating leaves the lease to
    expire, the sweeper requeues, the quota is still spent, and the Directive
    loops every two and a half minutes for the whole outage — notifying the
    owner every turn.
    """
    return await job.finish(
        agent,
        JobResult(state="failed", summary=run.failure_summary(), error=run.tail[-2000:]),
        phase=phase, progress=run.failure_summary()[:200], usage=run.usage,
    )


async def run_ask(job: DirectiveJob) -> JobResult:
    """A conversation: the owner's question, answered in TEXT.

    No pipeline, no directories, no PR. The summary the owner reads in Telegram
    IS the deliverable — a report that says "see docs/tracker/…/plan.md" has
    moved the work of reading to the person who asked.
    """
    log = ConversationLog(f"{STATE}/conversation.jsonl")
    await job.status(ASK_PERSONA, "in_progress", role="consultant", phase="ask",
                     progress="читаю вопрос")

    # `===` and never `---`: a prompt whose first character is a dash is parsed
    # as an option name. The kit guards it anyway, but a template that cannot
    # trigger the bug is better than one that relies on the guard.
    history = log.render()
    prompt = (
        (history + "\n\n" if history else "")
        + "=== Вопрос владельца ===\n"
        + job.intent
        + "\n\n=== Как отвечать ===\n"
        + "Ответь ТЕКСТОМ, на языке вопроса. Не запускай пайплайн, не создавай "
        + "директории, не открывай PR. Читай репозиторий и живое состояние "
        + "(gh pr list, git log) — не реконструируй его по файлам. Мелкие правки "
        + "под docs/ разрешены; если что-то поменял — скажи об этом прямо.\n"
        + "Прочитай docs/decisions.md первым делом; если по ходу принято решение, "
        + "которое следующий запуск обязан знать, допиши туда одну строку."
    )
    before = _tree_fingerprint()
    run = await _runner(effort=EFFORT, agent=ASK_PERSONA).run(prompt, agent=ASK_PERSONA)
    if not run.ok:
        return await _fail(job, ASK_PERSONA, run, phase="ask")

    log.append(job.intent, run.answer)
    changed = _tree_fingerprint() != before
    # `review` and not `done` when it touched the tree: the owner is told there
    # is something to look at, in the one place they are already looking.
    return await job.finish(
        ASK_PERSONA,
        JobResult(
            state="review" if changed else "done",
            summary=run.answer + ("\n\n⚠️ Файлы в репозитории изменились — посмотри diff." if changed else ""),
            artifacts=_artifacts(job) if changed else [],
        ),
        phase="ask", progress="ответил", usage=run.usage,
    )


async def run_converse(job: DirectiveJob) -> JobResult:
    """A live conversation ("позови альфреда") — the owner talks to this
    persona directly, turn by turn, instead of one-shot AskOwner questions.

    Shares `ask`'s ConversationLog (same file, same window) and never returns
    on its own — see infra.wardenkit.conversemode for why. It ends only when
    the Directive is Cancelled (the owner said "выход")."""
    log = ConversationLog(f"{STATE}/conversation.jsonl")
    return await run_conversation(
        job, _runner(effort=EFFORT, agent=ASK_PERSONA), ASK_PERSONA, log,
        repo_root=REPO_ROOT, role="consultant",
    )


async def run_epic(job: DirectiveJob) -> JobResult:
    """Decompose, and return the pieces — the Hub queues them, we never do.

    The children are parsed from a FILE the pipeline writes, never from its
    prose: the Hub queues whatever we return, so a formatting slip in markdown
    would become real queued work.
    """
    result = await run_command(job, effort=EFFORT)
    if result.state == "failed":
        return result
    path = Path(REPO_ROOT) / "docs" / "tracker" / job.task_id / "children.json"
    if not path.exists():
        result.summary += "\n\n⚠️ children.json не появился — в очередь ничего не поставлено."
        return result
    try:
        children = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        result.summary += f"\n\n⚠️ children.json нечитаем ({e}) — в очередь ничего не поставлено."
        return result
    result.children = [
        {"title": c.get("title", ""), "intent": c.get("intent", ""),
         "kind": c.get("kind", "develop")}
        for c in children if isinstance(c, dict) and c.get("title")
    ]
    result.summary += f"\n\nРазбил на {len(result.children)} задач — они уже в очереди."
    return result


async def run_command(job: DirectiveJob, *, effort: str = "") -> JobResult:
    """The default shape: run this kind's slash command over the repository."""
    command = KIND_COMMANDS.get(job.kind)
    # The SLUG, not the handle: status pushes join on the roster's slug, and a
    # row pushed as "xavier" matches nothing — the panel would show the
    # architect idle for the whole run.
    agent = "{{ARCHITECT_SLUG}}"
    if command is None:
        return await job.finish(
            agent,
            JobResult(state="failed",
                      summary=f"Проект не умеет '{job.kind}'. Умеет: {', '.join(sorted(KIND_COMMANDS))}, ask."),
            phase=job.kind, progress="kind не поддержан",
        )
    await job.status(agent, "in_progress", role="architect", phase=job.kind,
                     progress=f"запускаю {command}")

    prompt = f"{command} {job.intent}\n\n=== Контекст ===\ntask_id: {job.task_id}"
    run = await _runner(effort=effort or CODE_EFFORT).run(prompt)
    if not run.ok:
        return await _fail(job, agent, run, phase=job.kind)

    # The model's own closing text, on SUCCESS. For a kind whose deliverable is
    # prose this IS the deliverable; for one that writes code it is the summary
    # the owner reads on a phone. Either way it is not a path.
    return await job.finish(
        agent,
        JobResult(state="review", summary=run.answer or f"{command} завершился.",
                  artifacts=_artifacts(job)),
        phase=job.kind, progress=f"{command} завершился", usage=run.usage,
    )


async def run_pipeline(job: DirectiveJob) -> JobResult:
    logger.info("=== #%s (%s) task '%s': %s", job.id, job.kind, job.task_id, job.intent)
    if job.kind == "ask":
        return await run_ask(job)
    if job.kind == "converse":
        return await run_converse(job)
    if job.kind == "epic":
        return await run_epic(job)
    return await run_command(job)


async def main() -> None:
    hub = HubClient(HUB_ADDR, token_path=f"{STATE}/hub_token")
    manifest = make_manifest(
        NAME,
        purpose="{{PURPOSE}}",
        # This text is what the caller reads when CHOOSING a kind, so the map
        # from kind to behaviour lives here and not only in the code.
        description=(
            "ask — вопрос владельца, ответ ТЕКСТОМ (пайплайн не запускается). "
            "converse — живой разговор с владельцем, длится пока владелец не "
            "скажет «выход» (пайплайн не запускается). "
            "brainstorm — планирование, пишет файлы. "
            "research — вопрос, требующий источников и письменного отчёта. "
            + ", ".join(sorted(KIND_COMMANDS)) + " — работа по коду."
        ),
        kinds=KINDS,
        roster=ROSTER,
        max_concurrent=MAX_CONCURRENT,
        repo_url="{{REPO_URL}}",
        default_branch="{{INTEGRATION_BRANCH}}",
        grpc_addr=GRPC_ADDR,
    )
    # Serve BEFORE enrolling: the Hub may dispatch the moment the owner
    # approves, and a project registered but not listening looks exactly like
    # one that is down.
    server = await serve(
        WardenServicer(manifest, run_pipeline, hub=hub, repo_root=REPO_ROOT), BIND_ADDR
    )
    logger.info("Warden on %s, enrolling with %s", BIND_ADDR, HUB_ADDR)
    await hub.enroll(manifest, poll_seconds=5)
    logger.info("Enrolled as '%s'. Waiting for Directives.", NAME)
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(main())
