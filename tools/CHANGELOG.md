# tools/ — changelog

Short log of behavior-affecting changes across the stateless utility tools
(one dir per tool, auto-discovered by `loader.py`) — new tools, removed
tools, or a behavior change to an existing one (new arguments, a changed
result shape, a changed cost/latency profile the owner would notice). One
line per change: what + why. Newest first. Update it in the same commit
that makes the change. Internal-only plumbing (scraping internals, retry
tuning) belongs in the commit message, not here.

Starts empty on 2026-08-07 (the file itself is new); history before that is
in `git log -- tools/`.

## 2026-08-07

- `find_online`, `weather`, `route_time`: the Russian-only example values in
  each tool's schema description/usage were made bilingual (English example
  first), ahead of `KAYA_LANGUAGE` defaulting to English — these are
  model-facing hints for query phrasing, not owner-facing output, so this
  only nudges which language example the model leans on, not what any tool
  actually returns. Known gap, not fixed here: `weather`, `traffic_score`,
  `route_time`, `cheapest_flights`, and `youtube_transcript` still all
  return or prefer Russian-language *output* regardless of the calling
  agent's language — see `agents/README.md`'s "Multi-language support"
  section.
