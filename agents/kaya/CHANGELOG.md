# Кая — changelog

Short log of behavior-affecting changes to Кая (agent code, soul, her tools).
One line per change: what + why. Newest first. Update it in the same commit
that makes the change. Internal-only plumbing (caching, timeouts, retries,
validation that never reaches the owner) belongs in the commit message, not
here — this file is for what changed about how she acts or what she can do.

> On 2026-08-07 the pre-existing history (everything dated 2026-07-28
> through 2026-08-03) was translated to English and pruned down to this
> policy — internal plumbing entries that predated it were removed rather
> than translated. If you need the untranslated, unpruned original, it's in
> this repo's git history before that date.

## 2026-08-07

- Кая can now run in English, not just Russian (new `KAYA_LANGUAGE`
  setting) — real multi-language support, not a one-off translation. Ahead of
  open-sourcing the Kaizen core, everything Russian-only that a public,
  possibly-English-speaking owner would hit moved into per-language files
  under `locales/<lang>/`: her persona (`agents/kaya/locales/{ru,en}/
  soul.md`), the self-check cliché map (`agents/core/locales/{ru,en}/
  cliches.json` — genuinely different English AI-slop patterns, not a
  literal translation of the Russian ones), and every literal string the
  connector/delivery code sends the owner directly, plus the CLI backend's
  quota-exhausted message. New `agents/core/locale.py` runs a boot-time
  completeness check — if `KAYA_LANGUAGE` names a language missing any
  required file, she refuses to start and logs the exact missing paths,
  rather than degrading file-by-file into a persona that's part-French,
  part-Russian mid-conversation. New `agents/README.md` and
  `agents/kaya/README.md` document the architecture and how to add a
  language.
- `KAYA_LANGUAGE` defaults to `"en"`, not `"ru"` — chosen for a public repo's
  out-of-the-box experience. This DOES change behavior for an existing
  deployment with no `.env` override: add `KAYA_LANGUAGE=ru` to your `.env`
  to keep her Russian.
- soul.md's register-example section (the illustrative owner/Kaya exchanges
  used only for tone calibration) had its hotel-booking example reworded to
  a less specific trip — ahead of open-sourcing, no reason for an example
  meant to teach tone to also read like a real trip of the owner's.
- Voice transcription now follows `KAYA_LANGUAGE` (Yandex SpeechKit's `lang`
  param: `ru-RU`/`en-US`) instead of always being `ru-RU`. Missed in the
  first pass of the language work — with `en` now the default, an
  out-of-the-box deployment was transcribing English voice notes with a
  Russian speech model.

## 2026-08-03

- CLI backend: on a spent subscription, Кая now replies "⏳ Claude
  subscription is exhausted, resets at HH:MM" instead of "assistant backend
  failed" — the owner used to go hunting for a bug where there was only a
  clock.

## 2026-08-01

- A `docker compose up` that recreates postgres no longer leaves her
  looking dead: the DB pool now pre-pings and recycles, instead of every
  Telegram update failing silently until the dead connections drained.
- A crashed or empty turn no longer breaks every following reply. Кая used
  to go completely silent until restart once one bad turn landed inside the
  history window; that turn (and any raw `Error: …` reply) is no longer
  recorded as context, though the owner still sees the error itself.

## 2026-07-30

- Кая now manages projects through tracker v2 (twelve new tools replacing
  four old ones): `send_directive` instead of `delegate_task`,
  `directive_status` instead of `task_status`, plus `describe_project`,
  `list_directives`, `cancel_directive`, `reprioritise`,
  `pending_questions`, `answer_question`, `grant_auto_merge`,
  `pending_projects`, `approve_project`, `project_activity`.
- Кая now receives tracker notifications and can ASK the owner a project
  agent's question, not just report status: the project's agent sits
  blocked until the owner answers, and the answer goes out through
  `answer_question`. If she's mid-conversation, the notification is
  forwarded as-is instead of being lost — a "PR is ready" doesn't get a
  second try.
- soul.md: new section "The owner's projects and their agent teams" — what a
  directive is, why the owner's request shouldn't be rewritten into a spec,
  and how to handle a question from a project's agent.
- The tracker now speaks the v2 vocabulary: the task Кая delegates is now a
  "Directive", and the statuses she reports to the owner became
  `queued|dispatched|running|blocked|review|done|failed|cancelled` — two new
  states v1 had no way to express: `blocked` (an agent is waiting on the
  owner) and `review` (a PR is open, the owner decides).

## 2026-07-29

- Кая can now plan for HERSELF (new `remind_myself` tool + agent_wake
  delivery): she leaves herself a dated note, and when it fires it does NOT
  reach the owner — it wakes her for a real turn and she decides what to
  say, or stays silent. Example: the owner mentions a flight, and instead of
  being told to set a reminder, she works out the landing time herself and
  checks in afterwards on her own. She's told never to narrate the
  mechanics: "I'll write when you land" or nothing at all, never "I set
  myself a reminder".
- A reply Telegram refuses to send (over the 4096-char limit, flood limit)
  no longer aborts the per-chat drain loop: anything the owner sent during
  that turn used to stay queued with no worker and was silently never
  answered.
- New `cancel_reminder` tool: Кая can now take a reminder BACK ("cancel the
  reminder about...") — the store could always delete one, but no tool
  exposed it, so she could only ever add.
- The "🧐 Double-checking facts…" status line now only shows on real
  research (owner: it was firing at the wrong moments) — style-only checks
  (long answer, no search) now run completely silently. The checks
  themselves are unchanged, only what she announces.
- Memory keeps more: the fact-dedup threshold tightened, so similar-but-
  distinct facts ("Pasha likes coffee" vs "Masha likes coffee") now BOTH
  survive instead of one silently overwriting the other; only true
  rephrasings still merge.
- Module tools no longer vanish until a Brain restart: a module that boots
  slower than Brain is retried in the background and its tools appear on
  Кая's next session — she stops "losing" mentor/tracker/tools after
  unlucky restarts.
- Memory recall fixed: previously top-K irrelevant rows could crowd out a
  relevant fact/episode and Кая recalled nothing; she now finds memories she
  used to miss.
- soul v2.8 + shared cliché map: the cliché→live-phrasing map is now
  structured data (reusable by every agent — Кузя gets it for free),
  enforced by both the main model and the self-check. Sections: openers,
  preference-references, bookish connectives, closers, meta-talk, sales
  punches; any one filler word un-banned — sameness is the tell, not the
  word.
- soul v2.7: "Repetition and connectives" — no signature opener (the same
  "so, here's what I found on X..." on every reply was taught by our own
  examples), bookish connectives mapped to human ones, varied confirmations.
- Voice notes up to 60s (was 30s — Yandex sync STT hard cap per request):
  longer notes are split into chunks and recognized sequentially.
- A live research turn no longer times out mid-work just for taking longer
  than a flat limit — a turn actively calling tools now runs until it's
  actually stuck, not until an arbitrary clock runs out.
- Self-check leak sealed: checker commentary ("Facts checked out... removing
  the last paragraph") reached the owner once; a rewrite that doesn't follow
  the required format now can't reach him.
- Statuses: at most ONE progress line per turn (then typing indicator only)
  — a line per search/read step read as spam.
- New `route_time` tool: travel time A→B via Yandex Maps (car with live
  traffic / transit / walking). "How long to get to..." no longer goes
  through web search.
- New `weather` tool (Open-Meteo, free/no key): current weather + 1-7 day
  forecast by place name, instead of the agent reading it off news sites —
  slow, stale, and dragged status lines/fact-checking into a trivial
  question.
- soul v2.6: flattery-closer ban ("now you know more than half the people
  who...") — compliment endings and smug put-downs of abstract others
  slipped past the meta-talk ban; the answer ends when the content ends.
- soul v2.5: the "no fluff" ban widened to the whole class — meta-talk about
  own honesty/effort ("to be fair", "what I dug up", "what actually checked
  out") and performed feelings; warmth redefined as attention (memory,
  precision, short reactions), not exclamations.
- A photo turn could crash the whole reply ("Oops, something broke") — the
  base64 image overflowed the CLI runner's line buffer. Fixed; oversized
  lines beyond even the new limit are now skipped, not fatal.
- Conversation archive (new `search_conversations` tool): every exchange is
  auto-logged with an embedding, so Кая can find old dialogs ("what did we
  decide about...") after they leave the 30-message window — previously
  they were unreachable.
- soul v2.4 + final gate: banned announcing the answer's own qualities ("no
  fluff" and its relatives); self-check now also strips voice violations,
  and long no-search replies get a style-only gate.
- soul v2.3 + connector markdown stripper: plain text only — Telegram used
  to render markdown literally, so "**bold**" and [text](url) arrived as
  literal symbols; sources are now bare URLs in the text.
- soul v2.2: "Long answers" subsection + a long-form register example — the
  short examples fixed short replies, but long answers still came out as
  essays with sales punchlines and service endings.

## 2026-07-28

- soul v2.1: "Voice" section — hard bans on AI-slop patterns (evaluating
  openers, service endings, mirror structure, lists in chat) + register
  examples. Reason: replies read as obvious LLM output.
- Self-verification pass: a turn that searched the web fact-checks its own
  draft against actually-opened sources before replying — unverified claims
  no longer reach the owner.
- Web research protocol: search with several query phrasings, read pages
  (snippets are leads, not facts), cite sources.
- Connector: per-chat queue — messages sent while a turn is running are
  buffered and enter the model merged as one user turn, instead of each
  message spawning a concurrent racing reply.
- Connector: progress lines in chat ("🔎 Searching…", "📖 Reading…", "🧐
  Double-checking…") once a turn runs longer than ~10s — research turns take
  a minute+, silence looked like a hang.
