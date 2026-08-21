# =============================================================================
# Agent Core prompt templates — agents/core/prompts.py
# =============================================================================
# WHAT: The service-local home for Agent Core's Claude-facing prompt strings —
#       the small framing text the lib wraps around an agent's soul and recalled
#       memories. Currently: how recalled facts are presented to the model, and
#       the current-time line.
#
# WHY a dedicated prompts module (honoring CLAUDE.md's intent): the rule is
#       "all Claude prompts live in ONE dedicated templates file, not scattered
#       through logic". In the v2 mono-repo each service owns its prompts (Agent
#       Core must not import the monolith's app/prompts/templates.py — that would
#       break service isolation), so this file is Agent Core's templates.py. The
#       agent's OWN persona is NOT here — it lives in that agent's soul.md (data,
#       loaded at runtime), so one lib serves many different-voiced agents.
#
# HOW: loop/agent code imports these constants and .format()s them — no prompt
#       text is inlined at a call site.
# =============================================================================

# Brain's recall_memory tool already returns a labeled block ("Relevant
# memories:\n- ..."), so Agent Core injects that text verbatim rather than
# re-wrapping it. This marker is what that tool returns when nothing matches;
# the agent suppresses injection in that case. It MUST stay in sync with the
# empty-result string in brain/tools.py recall_memory.
EMPTY_RECALL_MARKER = "No relevant memories found."

# The current-time line, so relative times ("tomorrow at 9") resolve correctly.
# Minute precision (not seconds) keeps the prompt byte-stable within a minute,
# which is kinder to the API prompt cache.
CURRENT_TIME_TEMPLATE = "Current date and time: {now} ({tz})"

# ---------------------------------------------------------------------------
# Agent self-reminders (wake-ups)
# ---------------------------------------------------------------------------

# Injected as the USER turn that starts a woken turn: a self-reminder fired, so
# the agent runs a normal turn seeded with the note it left itself.
#
# WHY it must say the owner can't see it: without that, the model treats the
# note as an incoming message and answers IT («okay, I'll ask!») instead of
# doing the thing. The note is an instruction to itself, not something to
# acknowledge — and definitely not something to relay verbatim.
#
# WHY it authorizes silence: sometimes the right follow-through is nothing
# (the owner already told you about the flight ten minutes ago). Forcing a
# message would turn a thoughtful feature into a nagging one.
AGENT_WAKE_TEMPLATE = """[Automatic wake-up — the owner does NOT see this message.
Earlier you left yourself this note for now: "{note}"

Act on it. Whatever you write next is sent to the owner as a message on your own
initiative, so write it as one: no greeting-from-nowhere, no explaining that a
reminder fired, no repeating the note back. Just say the thing.

Before writing, make sure you actually remember the situation this note came
from. The conversation it grew out of may have scrolled out of the messages
above — recall_memory and search_conversations reach further back. Ground the
message in what was actually said (the trip, the deadline, the person), not in
the note's bare wording; a check-in that clearly remembers the context is the
whole point, and a vague one is worse than silence.

If, knowing what you know now, there is nothing worth sending — reply with
exactly "{skip}" and nothing else, and the owner is left in peace.]"""

# The exact reply that means "don't send anything to the owner this time".
AGENT_WAKE_SKIP = "WAKE-SKIP"

# What gets PERSISTED to local history for a wake-up, instead of the long
# template above. Two different things on purpose: the model needs the full
# instructions THIS turn, but future turns only need to know that a self-note
# fired and what it said — otherwise every later prompt carries the whole
# scaffold, and the owner's next reply reads as an answer to it.
AGENT_WAKE_HISTORY = "[Your self-reminder fired: {note}]"

# ---------------------------------------------------------------------------
# Tracker events (a project's fleet has news for the owner)
# ---------------------------------------------------------------------------
# WHY this is a turn and not a relay: the event may be a QUESTION an agent is
# blocked on, and answering it is a tool call (answer_question). A relayed
# string would leave the owner replying into the void. It also lets the agent
# check the directive's real state before speaking, instead of parroting a line
# that was already out of date when it was written.
#
# WHY it says the text is NOT from the owner: without that, the model treats an
# incoming "❓ agent asks: which cipher?" as the owner asking IT a question, and
# answers the cipher question itself instead of putting it to the owner.
#
# WHY silence is allowed but discouraged here (unlike a self-note): the owner
# asked for this work, so the default is to say something. But a stream of
# child directives from one epic should not become five separate messages.
TRACKER_EVENT_TEMPLATE = """[News from the project tracker — the owner does NOT see this, and did NOT
write it. This is a machine notification from one of his projects' agent
teams:

{event}

Tell the owner in your own words — brief, like a person, not like a log.
Keep links and task numbers as-is: he clicks them.

If this is a QUESTION from a project agent — ask the owner so it can be
answered in one phrase, and remember the question number (the notification
carries it somewhere, e.g. "#7" — whatever language it's written in; if you
can't find it, pending_questions shows all waiting ones). When the owner
answers, call answer_question with that number. Don't answer the question on
his behalf.

If useful, check directive_status or project_activity before writing —
better to say what's happening right now than to just repeat the
notification.

If there's genuinely nothing to say (it's noise, or you just wrote about
this) — reply with exactly "{skip}" and send nothing.]"""

# The short marker persisted to history instead of the template above — same
# reasoning as AGENT_WAKE_HISTORY.
TRACKER_EVENT_HISTORY = "[Tracker reported: {event}]"

# ---------------------------------------------------------------------------
# Web research protocol + self-verification
# ---------------------------------------------------------------------------

# Hard budget for one research request: search + page-read operations across
# the WHOLE request, the verification pass included. Small enough that a turn
# stays a Telegram-acceptable wait; big enough for query variations + a few
# full page reads.
SEARCH_OPS_CAP = 6

# Appended to every agent's system prompt (stable text -> cache-friendly).
# WHY here and not in a soul: this is harness behavior every Agent Core agent
# should share; souls stay persona-only.
SEARCH_PROTOCOL = f"""## Web research protocol

When the owner asks you to find, look up, compare, or check something online:
- Search THOROUGHLY, not once: try several differently-phrased queries; for
  products, places, or services also check reviews and comparisons from more
  than one site.
- Snippets are leads, not facts. Open the promising results (read_page) and
  base the answer on what the pages actually say.
- Never state a specific number (price, area, distance, time, rating, count)
  unless it's a figure the owner gave you this conversation, an arithmetic
  result you can show the derivation for from sourced numbers, or a page you
  opened this turn actually shows it. Do not round, interpolate, or recall a
  number "from memory" of similar cases — a wrong number is worse than an
  honest "didn't find the exact figure."
- Budget: at most {SEARCH_OPS_CAP} search/page-read operations per request.
  Spend them where they matter, then answer with the best you have.
- In the answer, give the source URL for each key fact — as a bare URL in the
  flow of the text, matching the medium and style of the reply (no markdown
  link syntax, no "Sources:" footer) — and say plainly which points you could
  NOT verify (never present an unverified claim as fact)."""

# The self-check turns. Injected as a user message AFTER the draft answer; the
# model either blesses the draft with the exact marker or replaces it. The
# marker is checked with startswith — models sometimes append a word.
VERIFY_OK_MARKER = "VERIFIED-OK"
# When the checker rewrites the draft, the final answer must start after this
# marker; the Agent STRIPS everything before it. This is the mechanical seam
# that keeps checker commentary («removing the last paragraph...») from ever
# reaching the owner — prompt obedience alone proved insufficient.
FINAL_MARKER = "FINAL:"

# Shared by both check turns. Deletion-only on style is deliberate: allowing
# "improvements" would let the checker polish the draft back INTO the default
# AI register — the exact thing the voice rules fight.
_STYLE_CLAUSE = """Also check the draft against the voice/style rules in your system prompt —
including the cliché map appended there, if one is present: any phrase
from its left column (or a cousin of it) must be replaced per the map.
Style violations are fixed by DELETION or minimal rewording of the offending
phrase only — never expand, restructure, or "polish" the rest; keep it
verbatim."""

VERIFY_REQUEST_TEMPLATE = f"""[Automatic self-check — the owner does NOT see this message or your draft above yet.]
Re-read your draft answer. For every factual claim in it, check: did a source
you actually opened this turn support it? If some claim is unsupported, doubtful,
or based on a snippet alone, use up to {{remaining}} more search/page-read
operations to verify or correct it.

Pay special attention to every specific NUMBER in the draft — price, area,
distance, time, percentage, rating, count. A number is not "roughly right" or
"probably right" — it must be either a figure the owner gave you this
conversation, an arithmetic result you can show the derivation for from
sourced numbers, or the exact figure a page you opened this turn stated.
For every number that isn't one of those three cases, trace it right now to
the page you opened this turn. If you can't, don't round it, don't guess an
adjacent-sounding figure, and don't keep it "for illustration" — cut it and
say the range or fact you can actually support (or say plainly you don't
have the number).

{_STYLE_CLAUSE}
- If the draft passes on BOTH facts and style, reply with exactly
  "{VERIFY_OK_MARKER}" and nothing else.
- Otherwise reply with the line "{FINAL_MARKER}" followed by ONLY the
  corrected, final answer to the owner (same language as the draft). The owner
  sees your output after "{FINAL_MARKER}" VERBATIM — never include commentary
  about what you changed, why, or that a check/draft existed."""

# The style-only gate for turns that did NOT search (no facts to re-check).
# Only long drafts get it — short replies rarely slip, and gating every "ok,
# done" would double casual-chat latency for nothing.
STYLE_REQUEST_TEMPLATE = f"""[Automatic self-check — the owner does NOT see this message or your draft above yet.]
Re-read your draft answer. {_STYLE_CLAUSE}
- If the draft is clean, reply with exactly "{VERIFY_OK_MARKER}" and nothing else.
- Otherwise reply with the line "{FINAL_MARKER}" followed by ONLY the cleaned,
  final answer to the owner (same language as the draft). The owner sees your
  output after "{FINAL_MARKER}" VERBATIM — never include commentary about what
  you changed, why, or that a check/draft existed."""
