# Brain — changelog

Short log of behavior-affecting changes to Brain (auth, access-list, shared
memory, tool router, reminder sweeper, admin panel). One line per change:
what + why. Newest first. Update it in the same commit that makes the
change. Internal-only plumbing (query tuning, retries, logging) belongs in
the commit message, not here — this file is for what changed about what
Brain does or how agents/modules experience it.

Created 2026-08-07; history before that is in `git log -- brain/`.

## 2026-08-09

- New `save_note`/`list_notes`/`search_notes`/`list_note_categories`/
  `forget_note` tools and a `notes` table (migration
  `f9945d1a49c6_add_notes_table`) — explicit "note this down" requests,
  distinct from Fact/Memory: never auto-saved, organized by an
  agent-assigned category + tags (inferred from content when the owner
  doesn't state them) instead of only semantic recall.

## 2026-08-08

- New `POST /tunnel/message` — logs one turn of a live direct owner<->agent
  conversation happening through a module (both directions). New
  `tunnel_messages` table (migration
  `601863742ef2_add_tunnel_messages_table`); separate from `Episode` on
  purpose — a tunnel is many small turns per session, not one durable
  embedded exchange, so it isn't searched or summarised, just kept so the
  owner's primary agent doesn't lose the thread. Same shared secret as
  `/event` (`MODULE_EVENT_TOKEN`).
- New read-only mobile dashboard at `GET /admin/tracker` (plus proxy routes
  `/admin/tracker/{overview,projects,tasks,activity}`) — lets the owner check
  tracker project/directive status and live agent activity from a phone.
  Brain proxies tracker's HTTP API so the phone only ever holds Brain's own
  admin token; tracker's admin token stays server-side
  (`TRACKER_ADMIN_TOKEN`/`TRACKER_HTTP_URL` in Brain's config).

## 2026-08-07

- New `python -m brain.seed_profile <file>` — bulk-loads facts and profile
  fields (timezone, home_location) from a plain-text file straight into
  shared memory, bypassing the model entirely. For giving a fresh install
  a paragraph of context in one shot instead of narrating it to an agent
  turn by turn. Works with no acting agent set (writes land with
  `agent_id=None`, same as any other system write).
