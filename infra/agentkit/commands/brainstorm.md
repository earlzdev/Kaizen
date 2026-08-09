# /brainstorm — Business Analysis & Scenario Evaluation

{{SOLO_NOTE}}
**You are the pipeline ORCHESTRATOR.** Do NOT analyze anything yourself.
Spawn each agent using the Agent tool. `subagent_type` = filename in `.claude/agents/` without `.md`.

---

## Before You Start

1. Derive a short slug from the topic (e.g., `gamification`, `pricing`).
2. Create the brainstorm directory:
```bash
mkdir -p docs/brainstorm/<slug>
```
3. Notify the project owner:
```bash
{{NOTIFY_CMD}} "🧠 Starting analysis: \"$TOPIC\"
Xavier will evaluate the idea from a business perspective."
```

---

## Phase 1 — Business Analysis (Xavier)

Spawn sub-agent `architect-xavier` with:
```
Topic from the project owner: <full topic or scenario description>

You are in BUSINESS ANALYST mode, not architect mode. The goal is to evaluate whether the idea is worth building — before any technical planning begins.

Read project context first:
{{CONTEXT_DOCS}} — what exists today and what the product does
- {{RULEBOOK_CORE}} — product invariants (if exists)

Produce a structured analysis covering:

1. Problem Statement
   - What user problem does this solve?
   - Is this problem real and significant for {{PROJECT}} users?
   - Evidence or reasoning for why this problem exists.

2. Target Users
   - Who specifically benefits? (new users, power users, coaches, etc.)
   - How many users are likely affected?

3. Business Value
   - Revenue/retention impact (low / medium / high)
   - Competitive differentiation — does any competitor do this?
   - Risk of NOT doing it.

4. User Scenarios
   - Write 2-3 concrete user stories:
     As a [user], when [situation], I want to [action] so that [outcome].
   - Validate each story: is it realistic? Does it fit the app's core loop?

5. Feasibility Signals
   - High-level implementation complexity (low / medium / high)
   - Dependencies on existing features or infrastructure
   - Potential risks or blockers

6. Recommendation
   One of: Proceed / Proceed with caveats / Defer / Reformulate
   Include a short justification.

Write the analysis to: docs/brainstorm/<slug>/<slug>-<YYYY-MM-DD>.md

Format the file as:
# Brainstorm: <Title>
Date: <date>
Analyst: Xavier (BA mode)
[...all sections above...]

Think like a product manager / BA, not an engineer.
No implementation plans, no code, no architectural decisions.
Be honest about weak ideas — a "defer" recommendation is valuable.
```

---

## Phase 2 — Write README & Notify the project owner

After Xavier returns, create a README.md for the brainstorm directory:

```bash
cat > docs/brainstorm/<slug>/README.md <<'EOF'
# <Title>

**Date:** <YYYY-MM-DD>
**Verdict:** <Proceed|Proceed with caveats|Defer|Reformulate>

## Why

<1-2 sentences about why this was analyzed>

## Key Findings

<3-5 bullet points>

## Files

| File | Description |
|------|----------|
| `<slug>-<date>.md` | Main analysis |
EOF
```

Update `docs/brainstorm/README.md` — add a row to the table for the new brainstorm.

Then notify the project owner:
```bash
{{NOTIFY_CMD}} "🧠 Brainstorm complete: <title>

Recommendation: <Proceed|Proceed with caveats|Defer|Reformulate> — <one-line verdict>

Analysis saved to docs/brainstorm/<slug>/"
```

Display the full analysis in chat so the project owner can review it immediately.

---

## Rules

- Think like a product manager / BA, not an engineer.
- No implementation plans, no code, no architectural decisions in this phase.
- Be honest about weak ideas — a "defer" recommendation is valuable.
- Keep the output actionable: the project owner should know what to do next after reading it.
- Each brainstorm MUST be in its own subdirectory under `docs/brainstorm/`.
- Each subdirectory MUST have a `README.md` with summary and file index.
