# =============================================================================
# Tracker module tools — modules/tracker/tools.py
# =============================================================================
# WHAT: The tool surface Кая sees (docs/tracker-architecture.md §6), served over
#       the Module gRPC contract and reaching her as MCP tools through Brain.
#       There is no separate MCP server to build — this list IS the MCP surface.
#
#       Reading the estate:  list_projects, describe_project, list_directives,
#                            directive_status, project_activity
#       Directing work:      send_directive, cancel_directive, reprioritise,
#                            grant_auto_merge
#       The live tunnel:     open_conversation, send_chat_message (the
#                            "позови альфреда" mode — cancel_directive ends it)
#       Answering the fleet: pending_questions, answer_question
#       Enrollment:          pending_projects, approve_project
#
# WHY `send_directive` takes INTENT and not a structured task: interpretation is
#       the project overseer's job, and that is the point of having one — Кая
#       must not need to know each project's pipeline vocabulary. She passes the
#       owner's actual words through.
#
# WHY the v1 names are gone rather than aliased (`delegate_task` →
#       `send_directive`, `task_status` → `directive_status`): this is pre-prod,
#       and a stale tool name costs more than it saves. It travels in every
#       prompt, and a model that can call both will sometimes call the wrong one.
#
# WHY results are formatted TEXT and not JSON: these go straight into an LLM's
#       context. A compact human-readable line costs fewer tokens than the same
#       facts in JSON braces, and the model reads it just as reliably.
#
# WHY errors become "Error:" strings: the shared servicer marks is_error when a
#       result starts with "Error:", which Brain forwards as an MCP error — so
#       an unknown project reaches the agent as a recoverable error it can fix,
#       not a crash.
#
# HOW: `build_tools(dispatcher)` -> list[ToolDef]; the shared infra.modkit
#       servicer turns them into RegisterTools/CallTool. The dispatcher is
#       passed in because `cancel_directive` has to reach the project's Warden.
# =============================================================================

from infra.modkit import ToolDef

from modules.tracker import store
from modules.tracker.models import DIRECTIVE_KINDS, DIRECTIVE_STATUSES

# The tracker's tool shape IS the shared ToolDef; the local name is kept so
# build_tools' signature reads naturally.
TrackerTool = ToolDef

# Protobuf's int32 is the ceiling on anything that rides to a Warden in a
# Directive message. Validated at the door instead of at dispatch time: a
# number the model invented should be refused where the model can still fix it.
_INT32_MAX = 2**31 - 1


def _fmt_directive(d: dict) -> str:
    line = f"[{d.get('id')}] {d.get('title')} — {d.get('status')} ({d.get('kind')})"
    if d.get("task_id"):
        line += f" · task {d['task_id']}"
    if d.get("claimed_by"):
        line += f" · by {d['claimed_by']}"
    if d.get("parent_id"):
        line += f" · part of #{d['parent_id']}"
    if d.get("summary"):
        line += f"\n  summary: {d['summary']}"
    for a in (d.get("artifacts") or []):
        line += f"\n  artifact: {a.get('type', 'link')} {a.get('url', '')}".rstrip()
    if d.get("error"):
        line += f"\n  error: {d['error']}"
    return line


def _fmt_project(p) -> str:
    tier = "warden" if p.grpc_addr else "poller"
    kinds = ", ".join((p.manifest or {}).get("kinds") or []) or "any"
    line = f"- {p.name} [{p.state}, {tier}]"
    if p.purpose:
        line += f": {p.purpose}"
    line += f"\n  kinds: {kinds}; runs up to {p.max_concurrent} at once"
    return line


async def _resolve(project: str):
    """Project by name, or None. Callers turn None into an 'Error:' string."""
    return await store.get_project_by_name((project or "").strip())


def build_tools(dispatcher=None) -> list[TrackerTool]:
    """The tracker module's tool set, backed by the store.

    `dispatcher` is optional so the tools can be built without one (the module
    still serves reads); without it, cancelling only updates the Hub's row and
    says so rather than pretending the project was told.
    """

    # -- reading the estate ------------------------------------------------
    async def list_projects() -> str:
        projects = await store.list_projects()
        if not projects:
            return "No projects registered."
        return "Projects:\n" + "\n".join(_fmt_project(p) for p in projects)

    async def describe_project(project: str) -> str:
        proj = await _resolve(project)
        if proj is None:
            return f"Error: no project named '{project}'."
        manifest = proj.manifest or {}
        roster = await store.list_agents(proj.id)
        queued = len(await store.list_directives(project_id=proj.id, status="queued"))
        in_flight = await store.count_in_flight(proj.id)

        lines = [_fmt_project(proj)]
        if manifest.get("repo_url"):
            lines.append(f"  repo: {manifest['repo_url']} ({manifest.get('default_branch', 'main')})")
        lines.append(f"  queue: {queued} waiting, {in_flight} in flight")
        if proj.last_seen_at:
            lines.append(f"  last seen: {proj.last_seen_at.isoformat()}")
        if roster:
            lines.append("  fleet:")
            lines += [
                f"    {a.display_name or a.name}" + (f" — {a.role}" if a.role else "")
                for a in roster
            ]
        return "\n".join(lines)

    async def list_directives(project: str | None = None, state: str | None = None) -> str:
        project_id = None
        if project:
            proj = await _resolve(project)
            if proj is None:
                return f"Error: no project named '{project}'."
            project_id = proj.id
        if state and state not in DIRECTIVE_STATUSES:
            return f"Error: state must be one of {list(DIRECTIVE_STATUSES)}."
        directives = await store.list_directives(project_id=project_id, status=state)
        if not directives:
            return "No directives match."
        return "Directives:\n" + "\n".join(_fmt_directive(d.to_dict()) for d in directives)

    async def directive_status(directive_id: int) -> str:
        directive = await store.get_directive(directive_id)
        if directive is None:
            return f"Error: no directive with id {directive_id}."
        lines = [_fmt_directive(directive.to_dict())]

        statuses = await store.list_agent_status(directive.id)
        if statuses:
            roster = await store.list_agents(directive.project_id)
            names = {a.name: a.display_name or a.name for a in roster}
            lines.append("  fleet:")
            for s in statuses:
                bit = f"    {names.get(s.agent_slug, s.agent_slug)} — {s.state}"
                if s.phase:
                    bit += f" ({s.phase})"
                if s.progress:
                    bit += f": {s.progress}"
                if s.blockers:
                    bit += f"\n      blocked by: {s.blockers}"
                lines.append(bit)

        children = await store.list_children(directive.id)
        if children:
            lines.append("  broken down into:")
            lines += [f"    #{c.id} {c.title} — {c.status}" for c in children]
        return "\n".join(lines)

    async def project_activity(project: str | None = None) -> str:
        """Who is doing what right now, across every project at once."""
        project_id = None
        if project:
            proj = await _resolve(project)
            if proj is None:
                return f"Error: no project named '{project}'."
            project_id = proj.id
        rows = await store.live_agent_status(project_id)
        if not rows:
            return "Nothing is running right now."

        roster_names: dict[int, dict[str, str]] = {}
        by_directive: dict[int, list] = {}
        header: dict[int, str] = {}
        for status, directive, proj in rows:
            header[directive.id] = (
                f"{proj.name} #{directive.id} «{directive.title}» — {directive.status}"
            )
            if proj.id not in roster_names:
                roster_names[proj.id] = {
                    a.name: a.display_name or a.name for a in await store.list_agents(proj.id)
                }
            by_directive.setdefault(directive.id, []).append((status, proj.id))

        out = []
        for directive_id, entries in by_directive.items():
            out.append(header[directive_id])
            for s, proj_id in entries:
                name = roster_names[proj_id].get(s.agent_slug, s.agent_slug)
                bit = f"  {name} — {s.state}"
                if s.phase:
                    bit += f" ({s.phase})"
                if s.progress:
                    bit += f": {s.progress}"
                if s.blockers:
                    bit += f" ⚠ {s.blockers}"
                out.append(bit)
        return "\n".join(out)

    # -- directing work ----------------------------------------------------
    async def send_directive(
        project: str,
        intent: str,
        kind: str = "develop",
        priority: int = 100,
        task_id: str | None = None,
    ) -> str:
        proj = await _resolve(project)
        if proj is None:
            return f"Error: no project named '{project}'."
        if proj.state != "active":
            return (
                f"Error: project '{proj.name}' is {proj.state} — it cannot be given "
                "work until it is approved and enrolled."
            )
        if kind not in DIRECTIVE_KINDS:
            return f"Error: kind must be one of {list(DIRECTIVE_KINDS)}."
        # The project's own manifest is the authority on what it can do. Checking
        # HERE means the owner is told "this project doesn't do research" in the
        # conversation, instead of a Directive being queued, dispatched, and
        # failed minutes later for the same reason.
        supported = (proj.manifest or {}).get("kinds") or []
        if supported and kind not in supported:
            return (
                f"Error: '{proj.name}' does not support '{kind}' Directives. "
                f"It supports: {', '.join(supported)}."
            )
        if not (0 <= priority <= _INT32_MAX):
            return f"Error: priority must be between 0 and {_INT32_MAX} (lower runs first)."
        intent = (intent or "").strip()
        if not intent:
            return "Error: intent is required — the owner's own words for what to do."

        # The title is a short label for lists and logs; the intent is the real
        # payload the overseer interprets, so it is kept whole in `description`.
        title = intent.splitlines()[0][:200]
        directive = await store.create_directive(
            proj.id, title, intent, kind=kind, priority=priority, task_id=task_id
        )
        return (
            f"Directive #{directive.id} queued for '{proj.name}' ({kind}). "
            "It will be handed to the project within seconds; use directive_status "
            f"({directive.id}) to follow it."
        )

    async def restart_project(project: str, reason: str = "",
                              scope: str = "jobs") -> str:
        """Ask a wedged project to pick itself up. The project may refuse."""
        row = await store.get_project_by_name(project)
        if row is None:
            return f"Error: no project called '{project}'."
        if dispatcher is None:
            return "Error: the dispatcher is not running, so nothing can be asked."
        result = await dispatcher.restart(
            row, reason=reason, scope=scope, requested_by="owner via Кая",
        )
        if not result.get("accepted"):
            return f"'{project}' did not restart: {result.get('reason') or 'refused'}"
        dropped = result.get("dropped") or []
        # Requeue rather than fail what it dropped: the owner asked for a
        # restart, not for the work to be abandoned, and every artifact those
        # directives produced is still in the project's repo.
        requeued = []
        for did in dropped:
            try:
                await store.set_status(did, "queued", summary=f"requeued after restart: {reason}")
                requeued.append(did)
            except store.TransitionError:
                pass
        parts = [f"'{project}' is restarting"]
        if result.get("restarting_in_sec"):
            parts.append(f"(process exits in {result['restarting_in_sec']}s, its "
                         f"container brings it back)")
        if requeued:
            parts.append("Requeued: " + ", ".join(f"#{d}" for d in requeued))
        elif dropped:
            parts.append(f"It dropped {len(dropped)} directive(s) that could not be requeued.")
        else:
            parts.append("Nothing was running.")
        return " ".join(parts)

    async def cancel_directive(directive_id: int, reason: str = "") -> str:
        directive = await store.get_directive(directive_id)
        if directive is None:
            return f"Error: no directive with id {directive_id}."
        if directive.status in ("done", "failed", "cancelled"):
            return f"Directive #{directive_id} is already {directive.status} — nothing to cancel."

        project = await store.get_project(directive.project_id)
        told = False
        if dispatcher is not None and project is not None:
            told = await dispatcher.cancel(project, directive, reason)
        try:
            await store.set_status(
                directive_id, "cancelled", summary=reason or "cancelled by the owner"
            )
        except store.TransitionError as e:
            return f"Error: {e}"
        return (
            f"Directive #{directive_id} cancelled"
            + (" and the project stopped working on it." if told else
               " in the tracker (the project was not reachable, or had not started it).")
        )

    # -- the live tunnel ("позови альфреда") --------------------------------
    async def open_conversation(project: str, agent_slug: str = "") -> str:
        """Open a continuous conversation with a project's Warden — the
        "позови альфреда" mode. Unlike send_directive(kind='ask'), which is
        one question and one answer, this stays open: send further owner
        messages with send_chat_message, and end it with cancel_directive."""
        proj = await _resolve(project)
        if proj is None:
            return f"Error: no project named '{project}'."
        if proj.state != "active":
            return (
                f"Error: project '{proj.name}' is {proj.state} — it must be approved "
                "and enrolled before it can hold a conversation."
            )
        if not proj.grpc_addr:
            return (
                f"Error: '{proj.name}' has no Warden — a live conversation needs a "
                "running agent process, not the poller tier."
            )
        # Same reasoning as send_directive's own manifest check: telling the
        # owner HERE beats "Opened conversation #N" followed, seconds later,
        # by the Warden rejecting it with unsupported_kind because it predates
        # the kit's converse support.
        supported = (proj.manifest or {}).get("kinds") or []
        if supported and "converse" not in supported:
            return (
                f"Error: '{proj.name}' does not support live conversations yet — "
                "its Warden needs re-rendering from the current agentkit template."
            )
        # A project's capacity is usually 1 (MAX_CONCURRENT) — an open
        # conversation holds that one slot for as long as it stays open, so a
        # second one would silently starve every other directive of a Warden
        # to run on. One open conversation per project at a time.
        existing = [
            d for d in await store.list_directives(project_id=proj.id)
            if d.kind == "converse" and d.status not in store.TERMINAL_STATUSES
        ]
        if existing:
            return (
                f"Error: '{proj.name}' already has an open conversation "
                f"(#{existing[0].id}) — end it with cancel_directive first."
            )
        title = f"Разговор с {agent_slug or proj.name}"[:200]
        directive = await store.create_directive(proj.id, title, kind="converse", priority=0)
        return (
            f"Opened conversation #{directive.id} with '{proj.name}'. Relay the owner's "
            f"messages one at a time with send_chat_message(directive_id={directive.id}, "
            "text=...). End it with cancel_directive when the owner says they're done."
        )

    async def send_chat_message(directive_id: int, text: str) -> str:
        """Relay one owner message into an open conversation. The reply comes
        back later as a tracker news event, the same way an agent's questions
        already reach Кая — it is not returned by this call."""
        directive = await store.get_directive(directive_id)
        if directive is None:
            return f"Error: no directive with id {directive_id}."
        if directive.kind != "converse":
            return f"Error: #{directive_id} is not a conversation."
        if directive.status not in ("dispatched", "running"):
            return (
                f"Error: #{directive_id} is {directive.status} — the conversation is "
                "not open."
            )
        if dispatcher is None:
            return "Error: the dispatcher is not running, so nothing can be delivered."
        project = await store.get_project(directive.project_id)
        if project is None:
            return f"Error: directive #{directive_id}'s project no longer exists."
        ok = await dispatcher.deliver_message(project, directive_id, text)
        if not ok:
            return f"Error: could not reach '{project.name}' — it may be offline."
        return "Delivered."

    async def reprioritise(project: str, ordered_ids: list) -> str:
        proj = await _resolve(project)
        if proj is None:
            return f"Error: no project named '{project}'."
        try:
            ids = [int(i) for i in ordered_ids]
        except (TypeError, ValueError):
            return "Error: ordered_ids must be a list of directive ids."
        moved = await store.reprioritise(proj.id, ids)
        if not moved:
            return (
                "Nothing was reordered — reprioritise only moves directives that are "
                "still queued for this project."
            )
        return f"Reordered {moved} queued directive(s) for '{proj.name}'."

    async def grant_auto_merge(directive_id: int) -> str:
        """Per-Directive merge permission (architecture §7 case 12)."""
        directive = await store.get_directive(directive_id)
        if directive is None:
            return f"Error: no directive with id {directive_id}."
        await store.set_auto_merge(directive.id, True)
        # Case 12: granting on a Directive that is waiting in `review` also
        # queues the follow-up that does the merging — otherwise the permission
        # would sit there with nobody acting on it.
        if directive.status == "review":
            # ONCE, though. The owner saying "yes, merge it" a second time — or
            # a retried tool call — must not start a second merge pipeline on a
            # PR the first one is already merging. A still-running merge child
            # IS the grant, so finding one is the answer, not a reason to queue
            # another.
            live = [
                c
                for c in await store.list_children(directive.id)
                if c.kind == "review" and c.status not in store.TERMINAL_STATUSES
            ]
            if live:
                return (
                    f"Auto-merge is already granted for #{directive.id} — "
                    f"#{live[0].id} is reviewing and merging it ({live[0].status})."
                )
            child = await store.create_directive(
                directive.project_id,
                f"Merge: {directive.title}"[:200],
                f"The owner granted auto-merge for directive #{directive.id}. "
                f"Self-review the diff and merge it.",
                kind="review",
                priority=max(0, directive.priority - 1),
                task_id=directive.task_id,
                parent_id=directive.id,
                auto_merge=True,
            )
            return (
                f"Auto-merge granted for #{directive.id}; queued #{child.id} to review "
                "and merge it."
            )
        return (
            f"Auto-merge granted for #{directive.id}. It will apply when the project "
            "reaches a mergeable state."
        )

    # -- answering the fleet ------------------------------------------------
    async def pending_questions() -> str:
        rows = await store.pending_questions()
        if not rows:
            return "No questions are waiting for you."
        out = []
        for question, directive, project in rows:
            bit = (
                f"[{question.id}] {project.name} #{directive.id} «{directive.title}»\n"
                f"  {question.agent_slug} asks: {question.text}"
            )
            if question.suggested:
                bit += "\n  options: " + " | ".join(question.suggested)
            out.append(bit)
        return "Questions waiting for you:\n" + "\n".join(out)

    async def answer_question(question_id: int, answer: str) -> str:
        question = await store.answer_question(question_id, answer)
        if question is None:
            return (
                f"Error: question {question_id} is unknown or already answered "
                "(the agent may have stopped waiting)."
            )
        return f"Answered question {question_id}; the agent resumes within seconds."

    # -- enrollment ---------------------------------------------------------
    async def pending_projects() -> str:
        waiting = await store.list_projects(state="pending")
        if not waiting:
            return "No projects are waiting for approval."
        return "Projects asking to enroll:\n" + "\n".join(
            f"- {p.name}" + (f": {p.purpose}" if p.purpose else "") for p in waiting
        )

    async def approve_project(project: str) -> str:
        proj = await store.approve_project((project or "").strip())
        if proj is None:
            return f"Error: no project named '{project}' is waiting for approval."
        return (
            f"Approved '{proj.name}'. Its Warden picks up a token on its next check-in "
            "(within seconds) and can then be given work."
        )

    return [
        TrackerTool(
            "list_projects",
            "List the projects registered with the tracker: name, what each is for, "
            "which kinds of work it takes, and whether it runs a full agent fleet "
            "(warden) or a simple poller.",
            {"type": "object", "properties": {}},
            list_projects,
        ),
        TrackerTool(
            "describe_project",
            "Everything known about one project: its manifest, its agent fleet, how "
            "much work is queued and running. Use before sending a directive if you "
            "are unsure what the project can do.",
            {"type": "object",
             "properties": {"project": {"type": "string", "description": "Project name"}},
             "required": ["project"]},
            describe_project,
        ),
        TrackerTool(
            "send_directive",
            "Hand a unit of work to a project's agent team. Call when the owner wants "
            "something built, fixed or investigated in one of their projects. Pass the "
            "owner's OWN words as `intent` — the project's overseer interprets them, "
            "so do not rewrite the request into a specification.",
            {"type": "object",
             "properties": {
                 "project": {"type": "string", "description": "Target project name"},
                 "intent": {"type": "string",
                            "description": "What the owner actually wants, in their words"},
                 # This text is the ONLY thing the agent has when choosing a
                 # kind, so the three that are easy to confuse are spelled out
                 # by their OUTPUT — text, files, or a report — rather than by
                 # their name. Everything else is listed by name and is obvious.
                 "kind": {"type": "string",
                          "description":
                              "ask — a conversation: the owner's question, answered in "
                              "TEXT. No pipeline runs, no files are written, no PR. Use "
                              "it whenever the owner is asking rather than commissioning: "
                              "\"ask Ohno what status the project is in\", \"what's next\", "
                              "\"is this done yet?\". "
                              "brainstorm — PLANNING: the product owner produces a plan or "
                              "slices the backlog, and WRITES FILES. "
                              "research — a question that needs sources and a written "
                              "REPORT. "
                              "deploy — ship the project's current main branch to prod: "
                              "opens a PR into its deploy branch, and only merges it if "
                              "told to explicitly. Only works if the project advertises "
                              "'deploy' in list_projects' kinds. "
                              "develop | fix | refactor | review | epic | analyze — real "
                              "work on the codebase. Default develop."},
                 "priority": {"type": "integer",
                              "description": "Lower runs first; default 100"},
                 "task_id": {"type": "string",
                             "description": "Optional: continue an existing task id"},
             },
             "required": ["project", "intent"]},
            send_directive,
            usage=(
                "send_directive(project='acme', intent='add key rotation', kind='develop'). "
                "Use kind='epic' for something big enough to need breaking up — the project "
                "splits it into pieces and queues them itself. "
                "send_directive(project='demo', intent='what status is the project in?', kind='ask') "
                "— the owner is ASKING, so the answer comes back as text; sending that as "
                "'research' or 'brainstorm' spends minutes of fleet time and answers with a "
                "list of file paths."
            ),
        ),
        TrackerTool(
            "directive_status",
            "The full state of one directive: where it is, which agent is doing what, "
            "what is blocking it, what it produced (PR links and other artifacts).",
            {"type": "object",
             "properties": {"directive_id": {"type": "integer"}},
             "required": ["directive_id"]},
            directive_status,
        ),
        TrackerTool(
            "list_directives",
            "List directives, optionally filtered by project and/or state "
            "(queued|dispatched|running|blocked|review|done|failed|cancelled).",
            {"type": "object",
             "properties": {"project": {"type": "string"}, "state": {"type": "string"}}},
            list_directives,
        ),
        TrackerTool(
            "cancel_directive",
            "Abort a directive. The project stops its pipeline and leaves the work it "
            "had done in place for inspection; nothing is reverted.",
            {"type": "object",
             "properties": {
                 "directive_id": {"type": "integer"},
                 "reason": {"type": "string", "description": "Why, for the record"},
             },
             "required": ["directive_id"]},
            cancel_directive,
        ),
        TrackerTool(
            "open_conversation",
            "Open a LIVE, continuous conversation with a project's agent — the "
            "\"позови альфреда\" mode. Use this when the owner wants a real back-and-"
            "forth (planning, going deep on something) rather than one question. Once "
            "open, relay every further owner message with send_chat_message instead of "
            "answering yourself, until the owner says they're done — then cancel_directive.",
            {"type": "object",
             "properties": {
                 "project": {"type": "string", "description": "Target project name"},
                 "agent_slug": {"type": "string",
                                "description": "Optional: who the owner asked for, e.g. 'alfred'"},
             },
             "required": ["project"]},
            open_conversation,
        ),
        TrackerTool(
            "send_chat_message",
            "Relay one owner message into an already-open conversation "
            "(open_conversation's directive_id). The reply arrives later as a tracker "
            "news event, not as this call's result.",
            {"type": "object",
             "properties": {
                 "directive_id": {"type": "integer"},
                 "text": {"type": "string", "description": "The owner's message, verbatim"},
             },
             "required": ["directive_id", "text"]},
            send_chat_message,
        ),
        TrackerTool(
            "reprioritise",
            "Reorder a project's queue. Pass the queued directive ids in the order they "
            "should run; the first one runs next.",
            {"type": "object",
             "properties": {
                 "project": {"type": "string"},
                 "ordered_ids": {"type": "array", "description": "Directive ids, best first"},
             },
             "required": ["project", "ordered_ids"]},
            reprioritise,
        ),
        TrackerTool(
            "pending_questions",
            "Questions a project's agents are waiting on the owner to answer. Each one "
            "has an agent blocked until it is answered.",
            {"type": "object", "properties": {}},
            pending_questions,
        ),
        TrackerTool(
            "answer_question",
            "Answer a waiting question by its id; the blocked agent resumes immediately.",
            {"type": "object",
             "properties": {
                 "question_id": {"type": "integer"},
                 "answer": {"type": "string", "description": "The owner's decision"},
             },
             "required": ["question_id", "answer"]},
            answer_question,
        ),
        TrackerTool(
            "grant_auto_merge",
            "Let a project merge one directive's pull request without the owner "
            "reviewing it. Per-directive and never global — ask the owner first.",
            {"type": "object",
             "properties": {"directive_id": {"type": "integer"}},
             "required": ["directive_id"]},
            grant_auto_merge,
        ),
        TrackerTool(
            "pending_projects",
            "Projects that have asked to connect to the tracker and are waiting for the "
            "owner's approval.",
            {"type": "object", "properties": {}},
            pending_projects,
        ),
        TrackerTool(
            "approve_project",
            "Approve a project that asked to enroll, so it can be given work. Only after "
            "the owner has explicitly said yes.",
            {"type": "object",
             "properties": {"project": {"type": "string"}},
             "required": ["project"]},
            approve_project,
        ),
        TrackerTool(
            "restart_project",
            "Ask a project to restart itself when its fleet is wedged — agents showing "
            "no progress, statuses frozen, nothing moving. Drops whatever it is running "
            "and requeues it; the work is not lost, since the artifacts are files in the "
            "project's repo. Use scope='jobs' (default) first; scope='self' also takes "
            "the process down so its container starts a fresh one. Never use it as a "
            "reflex — say what looked stuck, in `reason`.",
            {"type": "object",
             "properties": {
                 "project": {"type": "string"},
                 "reason": {"type": "string",
                            "description": "What looked stuck, for the record"},
                 "scope": {"type": "string",
                           "description": "jobs (default) | self"},
             },
             "required": ["project"]},
            restart_project,
        ),
        TrackerTool(
            "project_activity",
            "Who is doing what right now — every project's live agents, or one project's "
            "if named. Answers 'what is everyone working on?' in a single call.",
            {"type": "object", "properties": {"project": {"type": "string"}}},
            project_activity,
        ),
    ]
