# How should the code look — comments, headers, changelogs?

<!--
WHAT: The house style: what every new module must carry, how much to comment,
      and which changes require a changelog line.
WHY:  the owner reads this codebase to learn from it, so comments are a feature,
      not overhead. Agents left to their own defaults either strip comments or
      write restatements of the code.
STATUS: STUB — outline only.
-->

## To fill in

- **Every new module gets a WHAT / WHY / HOW header.** WHAT it does, WHY it
  exists (especially: what it replaced, or what breaks without it), HOW to use or
  read it. The WHY is the part that cannot be recovered from the code later.
- **Comment the non-obvious decision, not the syntax.** No `# increment i`.
  Explain the choice a future reader would otherwise second-guess.
- **Match the surrounding file** — comment density, naming, idiom. A file that is
  terse stays terse.
- **Changelog discipline**: which components require a one-line entry per
  behaviour-affecting change, and the format (what + why, newest first).
- **Prompts are data, not code** — where prompt templates and persona files live,
  and the rule that they never get inlined into logic.
- **No stubs, no TODO/FIXME left behind** in delivered work; if something is
  genuinely out of scope, say so in the report instead of leaving a marker.

## Kaizen-specific

- Prompts live in the owning service's own prompts module
  (`agents/core/prompts.py`); personas are `soul.md` data files.
- Every behaviour-affecting change to Кая gets a line in
  `agents/kaya/CHANGELOG.md`.
- Strict stack (aiogram 3, Anthropic SDK, PostgreSQL+pgvector, SQLAlchemy async,
  grpcio; no LangChain), service isolation, DB-per-service, no migrations.
