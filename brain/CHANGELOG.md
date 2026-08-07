# Brain — changelog

Short log of behavior-affecting changes to Brain (auth, access-list, shared
memory, tool router, reminder sweeper, admin panel). One line per change:
what + why. Newest first. Update it in the same commit that makes the
change. Internal-only plumbing (query tuning, retries, logging) belongs in
the commit message, not here — this file is for what changed about what
Brain does or how agents/modules experience it.

Created 2026-08-07; history before that is in `git log -- brain/`.

## 2026-08-07

- New `python -m brain.seed_profile <file>` — bulk-loads facts and profile
  fields (timezone, home_location) from a plain-text file straight into
  shared memory, bypassing the model entirely. For giving a fresh install
  a paragraph of context in one shot instead of narrating it to an agent
  turn by turn. Works with no acting agent set (writes land with
  `agent_id=None`, same as any other system write).
