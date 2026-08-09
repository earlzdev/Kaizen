# Agent handbook — answers to the questions that come up every time

<!--
WHAT: An index of short, standalone answers to the questions an agent (or a
      human) asks at the start of a piece of work: how do I create a new
      project, how does it run, how do I test it end to end, what may I decide
      alone, when do I stop and ask.
WHY:  these answers currently live in three places — CLAUDE.md, the pipeline
      export in docs/reference/, and my head. An agent starting fresh in a NEW
      repo has none of them. Each file here is written to be copy-pasteable into
      another project, not Kaizen-specific, unless the file says otherwise.
HOW to read it: find your question in the table, open that one file. Do not read
      the whole directory — each answer is self-contained on purpose.
STATUS: most files are stubs (outline only). Fill them one at a time, when the
      question actually comes up — a stub that names the right question is more
      useful than a guessed answer.
-->

| Question | File | Status |
|---|---|---|
| How does an idea become a deployed project with agents building it? | [project-factory.md](project-factory.md) | **written** |
| How do I start a new project from zero? | [new-project.md](new-project.md) | **written** — run `/new-project` |
| How do I add the agent fleet to a project that already exists? | [adopt-the-kit.md](adopt-the-kit.md) | **written** — hand it to an agent in that repo |
| How does the project run, and how do I prove my change works? | [run-and-verify.md](run-and-verify.md) | stub |
| How do I set up e2e testing in a new project? | [e2e-setup.md](e2e-setup.md) | stub → see [`docs/e2e/`](../e2e/README.md) |
| Where do secrets live and what may I read? | [secrets-and-env.md](secrets-and-env.md) | stub |
| How should the code look — comments, headers, changelogs? | [code-conventions.md](code-conventions.md) | stub |
| How do I use git here — branches, commits, PRs? | [git-and-prs.md](git-and-prs.md) | stub |
| Which slash command do I use, and when? | [slash-commands.md](slash-commands.md) | stub |
| What may I decide alone, and when must I ask the owner? | [asking-the-owner.md](asking-the-owner.md) | stub |
| How does a project join the Kaizen tracker? | [join-the-tracker.md](join-the-tracker.md) | stub → see [`docs/tracker-integration.md`](../tracker-integration.md) |

## Rules for this directory

- **One question per file.** If a file needs a "see also", the split was wrong.
- **Portable by default.** Write the answer so it works in any of the owner's
  projects. Kaizen-specific details go in a clearly marked section at the bottom,
  never in the main body.
- **Commands, not prose.** An agent executes; give it the exact command and the
  exact expected output, not a description of what to do.
- **A stub is a valid state.** Leaving the outline and filling it later is better
  than inventing an answer nobody has decided yet.

## Related, already written

- [`docs/e2e/README.md`](../e2e/README.md) — the e2e design: method / profile /
  secrets, the environment, the tech-stack policy.
- [`infra/agentkit/`](../../infra/agentkit/MANIFEST.md) — the reusable agent kit:
  stack-free personas, slash commands, phases, rulebook templates, and the
  rendering contract the `new-project` skill follows.
- [`docs/reference/agent-pipeline/`](../reference/agent-pipeline/README.md) — the
  Docker runtime the kit assumes (Telegram bot, trigger server, tracker service).
- `CLAUDE.md` (repo root) — Kaizen's own conventions; the source for anything in
  this directory marked Kaizen-specific.
