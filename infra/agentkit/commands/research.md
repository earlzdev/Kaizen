# /research — Deep Research Pipeline ({{PROJECT}})

{{SOLO_NOTE}}
**You are the pipeline ORCHESTRATOR.** Do NOT research anything yourself.
Spawn each agent using the Agent tool. `subagent_type` = filename in `.claude/agents/` without `.md`.

All inter-agent communication happens via files only:
- `{{TRACKER_ROOT}}/{task-id}/status/`
- `{{TRACKER_ROOT}}/{task-id}/tasks/`

---

## Before You Start

1. Derive a short `task-id` slug from the topic (e.g., `research-monetization`, `research-competitor-analysis`).
2. Create tracker and research output directories:
```bash
mkdir -p {{TRACKER_ROOT}}/{task-id}/status {{TRACKER_ROOT}}/{task-id}/tasks
mkdir -p {{RESEARCH_ROOT}}/{task-id}
```

3. Create a brief `README.md` at the task root:
```bash
cat > {{TRACKER_ROOT}}/{task-id}/README.md <<'EOF'
# {task-id}

**Type:** research
**Goal:** {1-2 sentence summary of the research topic}
EOF
```

4. Register the task in the SQLite tracker:
```bash
TASK_RECORD=$({{TRACKER_CMD}} task:create \
  "research: {topic}" \
  "{topic description from arguments}" \
  "research" \
  "Pipeline: /research")
TRACKER_ID=$(echo "$TASK_RECORD" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
{{TRACKER_CMD}} task:status "$TRACKER_ID" in_progress
```

5. Notify the project owner:
```bash
{{NOTIFY_CMD}} "🔬 Starting research: \"$TOPIC\"
⏱ Estimated time: 5-15 minutes."
```

---

## Phase 0 — Who owns this question

**Whoever frames the brief judges the answer.** So decide the owner before
spawning anybody:

- **Technical** — which library, which algorithm, does this API support X, what
  does it cost to run, is it feasible → **Xavier**, continue below.
- **Product** — is this worth doing, which option serves users better, what does
  the field do, what should we build next → **the Product Owner**. If
  `product-owner-ohno` is installed here, **stop and tell the owner to run
  `/product`** instead. Do not answer it through Xavier: the verdict on a product
  research question is a decision about *what*, and routing it through the
  architect quietly makes them the product owner.
- **No PO installed** → both kinds are Xavier's. That is the fallback, not the
  norm.

If the question is genuinely both, split it: the product half to `/product`, the
technical half here, each with its own brief.

---

## Phase 1 — Scope & Clarifying Questions (Xavier)

Spawn sub-agent `architect-xavier` with:
```
Research request from the project owner: <full topic description>
task-id: <task-id>

This is a /research pipeline. Analyze the topic.
If the topic is clear — skip questions, write the research task for Curie immediately.
If the topic is ambiguous — form clarifying questions and send them via {{NOTIFY_CMD}}.

Your deliverable: {{TRACKER_ROOT}}/<task-id>/tasks/xavier-to-curie-<task-id>.md
Also update: {{TRACKER_ROOT}}/<task-id>/status/architect-xavier.yml
```

**If Xavier asks clarifying questions:** STOP the pipeline.
```bash
{{NOTIFY_CMD}} "Xavier asked clarifying questions about the research. Reply here."
```
Wait for the project owner's reply, then re-spawn Xavier with answers to finalize the task file for Curie.

**If Xavier skips questions:** proceed directly to Phase 2.

---

## Phase 2 — Research (Curie)

Spawn sub-agent `researcher-curie` with:
```
task-id: <task-id>
Read your assignment: {{TRACKER_ROOT}}/<task-id>/tasks/xavier-to-curie-<task-id>.md
Conduct research and write the report to: {{RESEARCH_ROOT}}/<task-id>/research.md
Any supplementary materials (tables, diagrams, appendices) also go into {{RESEARCH_ROOT}}/<task-id>/
Update: {{TRACKER_ROOT}}/<task-id>/status/researcher-curie.yml
```

Wait for Curie to complete.

---

## Phase 3 — Quality Review (Xavier)

Spawn sub-agent `architect-xavier` with:
```
task-id: <task-id>
This is the review phase of a /research pipeline.
Read Curie's research report at {{RESEARCH_ROOT}}/<task-id>/research.md
Review quality: sources, depth, actionability, completeness.
If issues found — write feedback to {{TRACKER_ROOT}}/<task-id>/tasks/xavier-review-curie-<task-id>.md
Update: {{TRACKER_ROOT}}/<task-id>/status/architect-xavier.yml with status and verdict.
```

If Xavier finds quality issues → re-spawn Curie with feedback, then re-run Phase 3.

---

## Phase 4 — Deliver Results

If **git workflow instructions** were provided (remote mode):
- Follow the git workflow rules: create branch `research/{slug}`, push, create PR, merge/notify

If **no git workflow instructions** (local mode):
- Research report is already saved locally
- Report what was researched and where the file is

---

## Global Rules

- Curie does the research, Xavier scopes and reviews. Orchestrator coordinates.
- If any agent returns `blocked` — notify the project owner immediately and stop.
- Max 2-3 clarifying questions per research.
- Always notify the project owner at start and end.
- If something fails — notify the project owner with the error and what was saved.
