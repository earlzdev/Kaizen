# /epic — Epic Decomposition Pipeline ({{PROJECT}})

{{SOLO_NOTE}}
**You are the pipeline ORCHESTRATOR.** Do NOT decompose anything yourself.
Spawn each agent using the Agent tool. `subagent_type` = filename in `.claude/agents/` without `.md`.

All inter-agent communication happens via files only:
- `{{TRACKER_ROOT}}/{task-id}/status/`
- `{{TRACKER_ROOT}}/{task-id}/tasks/`

---

## Before You Start

1. Derive a short `task-id` slug from the epic (e.g., `epic-admin-panel`, `epic-feature-b-stats`).
2. Create tracker directories:
```bash
mkdir -p {{TRACKER_ROOT}}/{task-id}/status {{TRACKER_ROOT}}/{task-id}/tasks
```

3. Create a brief `README.md` at the task root:
```bash
cat > {{TRACKER_ROOT}}/{task-id}/README.md <<'EOF'
# {task-id}

**Type:** epic
**Goal:** {1-2 sentence summary of the epic}
EOF
```

4. Register the task in the SQLite tracker:
```bash
TASK_RECORD=$({{TRACKER_CMD}} task:create \
  "epic: {epic title}" \
  "{epic description from arguments}" \
  "epic" \
  "Pipeline: /epic")
TRACKER_ID=$(echo "$TASK_RECORD" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
{{TRACKER_CMD}} task:status "$TRACKER_ID" in_progress
```

Remember `$TRACKER_ID` — use it for all `{{TRACKER_CMD}} task:*` calls throughout this pipeline.

---

## Phase 1 — Clarifying Questions (Xavier)

Spawn sub-agent `architect-xavier` with:
```
Epic request from the project owner: <full description>
task-id: <task-id>

You are in EPIC DECOMPOSER mode. Before decomposing, you must understand the full scope.

Read project context:
{{CONTEXT_DOCS}}
- {{RULEBOOK_CORE}} (if exists)

Think like a senior architect:
- What is the full scope? What is explicitly out of scope?
- What are the dependencies between parts?
- What corner cases could block execution?
- Are there security, data privacy, or architectural risks?
- What does "done" look like for the full epic?

Form 3-6 targeted clarifying questions. Focus on things that would change the decomposition.
Send questions via: {{NOTIFY_CMD}} "Xavier — Epic Questions: <title>\n\n<numbered questions>\n\nReply when ready."

Write questions to: {{TRACKER_ROOT}}/<task-id>/tasks/xavier-epic-questions-<task-id>.md
Update: {{TRACKER_ROOT}}/<task-id>/status/architect-xavier.yml
```

**After Xavier returns: STOP the pipeline.**
```bash
{{NOTIFY_CMD}} "Xavier asked clarifying questions about the epic. Reply here."
```
Wait for the project owner's reply before continuing.

---

## Phase 2 — Decomposition (Xavier, after the project owner's answers)

Spawn sub-agent `architect-xavier` with:
```
Epic request: <original description>
task-id: <task-id>
the project owner's answers: <the project owner's reply>

You are in EPIC DECOMPOSER mode. Now decompose the epic into 3-7 medium tasks.

Rules for task sizing:
- Each task is independently deployable (has its own PR)
- Not too small (no "add a field" tasks) — each task should take ~2-4 hours of agent work
- Not too large (no "build the whole module" tasks)
- Tasks are ordered by dependency — task N must be completable before task N+1 starts

For each task define:
- Title — short, imperative (e.g. "Setup admin service scaffold with auth middleware")
- Description — 2-4 sentences: what to build, what NOT to build, acceptance criteria
- Type — feature, bugfix, or refactor
- Notes — scope hints, key files to touch, dependencies on previous tasks

Write the full decomposition to: {{TRACKER_ROOT}}/<task-id>/tasks/xavier-epic-decomposition-<task-id>.md
Send summary to the project owner via: {{NOTIFY_CMD}} "Epic decomposition: <title>\n\n<numbered task list>\n\nDoes this look right? Should I adjust anything?"
Update: {{TRACKER_ROOT}}/<task-id>/status/architect-xavier.yml
```

**After Xavier returns:** Show the decomposition in chat.
Ask the project owner: "Does this decomposition look right? Should I adjust anything before I load it into the queue?"

**STOP here. Wait for the project owner's confirmation.**

If the project owner requests changes → re-spawn Xavier with feedback, repeat Phase 2.

---

## Phase 3 — Load tasks into tracker

After the project owner confirms (any affirmative reply), read the decomposition file and create each task in order:

```bash
# Repeat for each task (they will be queued in creation order = FIFO priority):
{{TRACKER_CMD}} task:create \
  "<task title>" \
  "<task description>" \
  "<type>" \
  "Epic: <epic slug>. <notes>"
```

This loads them into the tracker queue. `/next` will execute them in FIFO order.

---

## Phase 4 — Show final queue

```bash
{{TRACKER_CMD}} task:list pending
```

Display the pending queue in chat so the project owner can see the order.

---

## Phase 5 — Notify & Complete

```bash
{{TRACKER_CMD}} task:status "$TRACKER_ID" done
{{NOTIFY_CMD}} "Epic loaded: <epic title>

Tasks queued: N
<numbered list: 1. title, 2. title, ...>

Run /next to start execution."
```

---

## Rules

- Do NOT start implementing anything — this command only populates the queue.
- Do NOT skip the clarifying questions phase — better to ask now than discover blockers during implementation.
- Task descriptions must be self-contained — the agent running `/next` will only see the task, not this conversation.
- If the project owner rejects the decomposition, revise and re-confirm before loading.
- Each task's notes must mention which previous task it depends on (if any).
