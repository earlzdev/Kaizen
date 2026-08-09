# {{ZONE_LABEL}} rulebook — {{PROJECT}}

<!--
WHAT: The rules for working inside the `{{ZONE_KEY}}` zone: its stack, its
      idioms, its traps.
WHY:  this is where stack knowledge lives. Personas are stack-free on purpose so
      the same fleet works in every project — the difference between a Go service
      and a SwiftUI app is here, not in the persona.
HOW:  one file per zone, named 01-, 02-, … Pre-read by every agent that touches
      the zone. Keep it under ~200 lines; link out for anything longer.
-->

> **Scaffolder:** author this from the interview. Write only rules that would
> change what an agent does. "Write good code" is not a rule; "every handler
> returns the typed error envelope, never a raw string" is.

## Stack
{Language, framework, runtime version, package manager, the two or three
libraries an agent must know about before writing a line.}

## Layout
{Where things go in this zone, and what each directory is for. Not a file tree —
the *rule* for where a new file belongs.}

## Idioms — how code is written here
{The patterns this zone already uses: error handling, logging, configuration,
dependency wiring, concurrency, resource cleanup. An agent should be able to
write code that looks like the existing code from this section alone.}

## Interfaces this zone exposes
{HTTP endpoints / screens / CLI commands / events. How they are declared, how
access is enforced, and what must be true of every new one.}

## Testing
{How this zone is tested, what the e2e scenario looks like, what may be stubbed
and what may never be. The default rule: one e2e scenario per acceptance
criterion; unit tests only for pure branchy logic.}

## Verification
```bash
{{VERIFY_CMD}}
```
{What it runs and what a green result actually proves — and does not prove.}

## Traps
{The mistakes that have actually happened in this zone. Grows over time; this
becomes the most valuable section in the file.}
