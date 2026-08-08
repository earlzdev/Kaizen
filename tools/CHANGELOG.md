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

## 2026-08-08

- `weather`, `traffic_score`, `route_time`, `cheapest_flights`,
  `youtube_transcript`: all five now take an optional `language` argument
  (`en`/`ru`, default `en`) and actually honor it — closing the gap the
  2026-08-07 entry below flagged as known-but-not-fixed. `weather` picks
  the WMO condition-word table and the geocoding locale; `traffic_score`
  picks the congestion-label table; `route_time` reformats Yandex's
  always-Russian duration text ("1 ч 42 мин") into the requested language
  instead of passing it through; `cheapest_flights` switches locale AND
  currency (USD for en, RUB for ru) together; `youtube_transcript` reorders
  which caption track it tries first. None of these change behavior for a
  caller that doesn't pass `language` — same as before, just explicit now.
  Underlying data that's inherently Russian (Yandex's city-name lookup
  keys, its scraped page format) is unaffected; only the tools' own output
  text is bilingual.
- `weather`: fixed a real bug found while adding the above — WMO code 0
  ("clear sky") was treated as falsy (`code or -1`) and silently returned
  an empty condition string instead of "clear"/"ясно". Existed before this
  change too; just never triggered a report because a perfectly clear
  reading is the case most likely to go unnoticed as "missing text".

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
