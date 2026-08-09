# {{PROJECT}} — project conventions

{{PURPOSE}}

Reports to the owner are written in {{OWNER_LANGUAGE}}.

## Zones

| key | label | paths | rulebook | verify | owner |
|---|---|---|---|---|---|
{{ZONE_TABLE}}

{{ZONE_OWNERSHIP_NOTE}}

## Branches

`{{MAIN_BRANCH}}` is the owner's alone. Work lands on `{{INTEGRATION_BRANCH}}`
through a PR. Autonomy: {{AUTONOMY_LEVEL}}.

## Hard rules

- **Never read or print any `.env` file** (there are two: the app's at the
  root, the Warden's at `deploy/warden/.env`). Use the matching `.env.example`.
  (Inside the fleet's container `/repo/.env` and `/repo/deploy/warden/.env`
  are both templates anyway — but the rule holds on the host too, where they
  are not.)
{{SECRETS_RULE}}- Verify with **your zone's own command** from the table above, not the whole
  repo (`{{VERIFY_ALL_CMD}}`), unless you changed more than one zone.
- Every behaviour-changing decision gets one line in `docs/decisions.md` — read
  it before you start, append to it before you finish.
- The tracker tree is fixed at `{{TRACKER_ROOT}}`: the Warden reads exactly that
  layout, and handoffs written anywhere else are invisible to it.

## How work arrives

Through the Kaizen tracker (`deploy/warden/`) or a slash command. The fleet, the
phases and the review loop are described in `.claude/workflow.md`; git rules are
in `.claude/git-workflow.md`.

## Fleet

| slug | name | role | area | model |
|---|---|---|---|---|
{{AGENT_REGISTRY}}
