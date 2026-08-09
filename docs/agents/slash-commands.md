# Which slash command do I use, and when?

<!--
WHAT: The command map — what each pipeline command is for, what it costs, and
      which gate it represents.
WHY:  the commands overlap enough to be confusing (/agentic-loop and /e2e both
      "check the work"), and picking the wrong one either wastes a lot of tokens
      or skips the gate that would have caught the bug.
STATUS: STUB — outline only.
-->

## To fill in

| Command | For | Gate type |
|---|---|---|
| `/develop` | new feature, full pipeline (architect → leads → devs → reviewers) | — |
| `/fix` | a bug, lighter pipeline | — |
| `/refactor` | behaviour must not change; safety net first | — |
| `/product` | business / R&D / devrel / a new project's charter — the only command that enters at the Product Owner | — |
| `/epic` | decompose a big ask into an ordered task queue | — |
| `/design` | UI mockups in the design tool, reviewed against the design system; no code | — |
| `/doc` | doc comments on existing code; never changes behaviour | — |
| `/analyze` | living specification of behaviour (analyst) | — |
| `/research`, `/brainstorm`, `/next`, `/abort` | (describe each in one line) | — |
| `/agentic-loop` | do the work, then loop independent reviewers until no critical/high issues | **static** — agents read the diff |
| `/e2e` | plan, write, run and prove an end-to-end scenario | **behavioural** — agents run the thing |

- **They are complementary, not alternatives.** The loop catches convention and
  logic drift by reading; e2e catches wiring by running. A feature is done when
  both are green.
- **Ordering**: `/develop` → `/agentic-loop` → `/e2e`.
- **Cost note** — which of these fan out into many subagents, so the owner can
  choose deliberately.
- The full pipeline command definitions live in
  [`infra/agentkit/commands/`](../../infra/agentkit/commands/).

- **A project only has the commands its fleet supports.** `/new-project` installs
  a command only when every persona it spawns exists there — no designer, no
  `/design`; no PO, no `/product`. See
  [`infra/agentkit/MANIFEST.md`](../../infra/agentkit/MANIFEST.md) §2b.

## Open

- `/e2e` is designed ([`docs/e2e/`](../e2e/README.md)) but not written yet.
