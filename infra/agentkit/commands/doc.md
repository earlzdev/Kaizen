# /doc — Doc-Comment Writer ({{PROJECT}})

Writes and edits doc comments on existing code, in this project's own convention
({{DOC_CONVENTION}}), in {{OWNER_LANGUAGE}}.

This command documents what the code **does**. It never changes what it does — a
doc pass that "fixes a small thing along the way" makes a documentation diff
unreviewable.

## When to use
- "document these classes / this module / this package"
- a file or symbol is named and the ask is documentation, not behaviour

## Input
One or more of: file paths, symbol names (search the codebase to locate them),
directory or glob patterns. Ambiguous input → ask which symbols, once.

## Workflow
1. **Read** every target file in full. Documenting from a signature produces
   confident, wrong prose — the parameter that "obviously" means one thing
   usually means another.
2. **Analyse** each symbol: purpose, parameters, return, errors/exceptions,
   invariants, threading/lifetime expectations, side effects.
3. **Write** doc comments in {{DOC_CONVENTION}}. Standard tags only — no invented
   ones, no house formatting.
4. **Validate** against the checklist below.

## Rules
- **Say what is not obvious from the signature.** `/** Returns the user id */`
  above `fun userId(): String` is noise. Document units, ranges, nullability
  meaning, ownership, failure behaviour, and *why* it exists.
- **Document every parameter, return value and error path**, or none — a
  half-documented symbol reads as a complete one and hides the gaps.
- **State side effects and preconditions.** What must be true before the call,
  what changes after it, what it may block on.
- **Never document a behaviour you did not verify in the code.** If it is
  unclear, say so in the comment rather than inventing a certainty.
- **Never change code.** No renames, no reordering, no "obvious" fixes. Behaviour
  changes belong to `/fix` or `/develop`. Note what you found and move on.
- **No `@since`, no changelog prose, no author tags** unless the project already
  uses them.

## Checklist before finishing
- [ ] every public symbol in scope has a doc comment
- [ ] every parameter, return and error path documented
- [ ] units, ranges and nullability meaning stated where they exist
- [ ] preconditions and side effects stated
- [ ] no code changed — the diff contains comments only
- [ ] the convention matches the rest of the file, not a different style
- [ ] text is in {{OWNER_LANGUAGE}}

## Report
List which files were touched, which symbols were documented, and anything the
code does that surprised you — the last part is often worth more than the docs.
