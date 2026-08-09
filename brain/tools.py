# =============================================================================
# Brain built-in tools — brain/tools.py
# =============================================================================
# WHAT: Builds Brain's built-in memory/profile/reminder/notes tools and
#       assembles the ToolRegistry the MCP server serves. In Phase 2 these are
#       the ONLY tools (no modules yet); module tools discovered over gRPC
#       join the same registry from Phase 4.
#
# WHY the tools live here and not in the store: the store (brain/memory.py) is
#       pure persistence; the tools are the LLM-facing surface — their
#       descriptions tell the agent's model WHEN to call them. Keeping the two
#       apart means the same store can back several tool shapes later.
#
# WHY module=None on every tool: these are Brain built-ins, not module tools;
#       the access-list keys on (module=None, name). recall is exposed as a tool
#       here (unlike the in-process bot, where retrieval is automatic) because a
#       remote agent has no other way to pull memory before replying.
#
# HOW: `build_registry(embedder, episodes, notes)` -> ToolRegistry, handed to
#      the MCP server (episodes = brain/episodes.py, notes = brain/notes.py).
# =============================================================================

import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from brain.config import settings
from brain.db.models import AUDIENCE_AGENT, AUDIENCE_OWNER
from brain.embedder import Embedder
from brain.episodes import EpisodeStore
from brain.memory import MemoryStore
from brain.notes import NoteStore
from brain.registry import Tool, ToolRegistry


def build_tools(
    store: MemoryStore, episodes: EpisodeStore | None, notes: NoteStore | None = None
) -> list[Tool]:
    """The built-in memory/profile/reminder/archive/notes tools, bound to stores."""

    async def remember_fact(fact: str) -> str:
        return await store.remember(fact)

    async def recall_memory(query: str) -> str:
        facts = await store.recall(query)
        if not facts:
            return "No relevant memories found."
        return "Relevant memories:\n" + "\n".join(f"- {f}" for f in facts)

    async def list_memories() -> str:
        facts = await store.list_facts()
        if not facts:
            return "No memories stored yet."
        return "Stored memories (id in brackets):\n" + "\n".join(
            f"[{f.id}] {f.content}" for f in facts
        )

    async def forget_memory(memory_id: int) -> str:
        deleted = await store.forget(memory_id)
        return f"Memory {memory_id} deleted." if deleted else f"No memory with id {memory_id}."

    async def set_profile(timezone: str | None = None, home_location: str | None = None) -> str:
        if timezone is None and home_location is None:
            return "Error: provide at least one of timezone or home_location."
        return await store.set_profile(timezone=timezone, home_location=home_location)

    async def get_profile() -> str:
        profile = await store.get_profile()
        if profile is None:
            return "No profile set yet."
        return f"Profile: timezone={profile.timezone}, home_location={profile.home_location}."

    async def _resolve_when(
        due_at: str, recurrence: str, tz: str | None
    ) -> tuple[datetime.datetime, str | None] | str:
        """Shared time resolution for both reminder tools. Returns
        (aware_datetime, zone_name) or an "Error: ..." string for the model.

        Resolve the zone the reminder is set in, so it fires at the right LOCAL
        time. Priority: explicit tz arg -> the offset already in due_at -> the
        owner's profile timezone -> the configured default. A naive due_at is
        ANCHORED to the resolved zone (never silently treated as UTC)."""
        try:
            when = datetime.datetime.fromisoformat(due_at)
        except ValueError:
            return f"Error: due_at '{due_at}' is not ISO-8601 (e.g. 2026-07-25T09:00:00+03:00)."
        if recurrence not in ("none", "daily", "weekly"):
            return f"Error: recurrence must be none|daily|weekly, got '{recurrence}'."

        zone_name = tz
        if zone_name is None and when.tzinfo is None:
            profile = await store.get_profile()
            zone_name = (profile.timezone if profile else None) or settings.default_timezone

        if when.tzinfo is None:
            try:
                when = when.replace(tzinfo=ZoneInfo(zone_name))
            except (ZoneInfoNotFoundError, ValueError):
                return f"Error: unknown timezone '{zone_name}'. Use an IANA name like Europe/Moscow."
        elif tz is not None:
            # due_at already had an offset AND an explicit tz was given: honor tz
            # as the recorded zone, re-anchoring the wall-clock to it.
            try:
                when = when.replace(tzinfo=ZoneInfo(tz))
            except (ZoneInfoNotFoundError, ValueError):
                return f"Error: unknown timezone '{tz}'. Use an IANA name like Europe/Moscow."
        return when, zone_name

    async def add_reminder(
        text: str, due_at: str, recurrence: str = "none", tz: str | None = None
    ) -> str:
        resolved = await _resolve_when(due_at, recurrence, tz)
        if isinstance(resolved, str):
            return resolved
        when, zone_name = resolved
        return await store.add_reminder(
            text, when, recurrence, tz=zone_name, audience=AUDIENCE_OWNER
        )

    async def remind_myself(
        note: str, due_at: str, recurrence: str = "none", tz: str | None = None
    ) -> str:
        """A note the AGENT leaves itself; firing wakes it for a real turn."""
        # A blank note would be schema-valid but undeliverable: the agent's
        # receiver rejects an empty event, so the row would never be marked
        # done and the sweeper would retry it at the backoff cap forever.
        if not note.strip():
            return "Error: note must say what future-you should do (it is all you will see)."
        resolved = await _resolve_when(due_at, recurrence, tz)
        if isinstance(resolved, str):
            return resolved
        when, zone_name = resolved
        result = await store.add_reminder(
            note, when, recurrence, tz=zone_name, audience=AUDIENCE_AGENT
        )
        # Reword the store's owner-facing phrasing so the model doesn't parrot
        # "Reminder set for ..." back at the owner as if it were a confirmation
        # it was asked for.
        return result.replace("Reminder set for", "Noted to self for", 1).replace(
            "Reminder already set for", "Already noted to self for", 1
        )

    async def save_note(content: str, category: str | None = None, tags: list[str] | None = None) -> str:
        return await notes.save_note(content, category=category, tags=tags)

    # Notes accumulate long-form owner content (unlike short facts), so an
    # unfiltered listing is capped — the model should narrow by category/tag
    # or use search_notes instead of dumping the whole table into its context.
    LIST_NOTES_CAP = 50

    async def list_notes(category: str | None = None, tag: str | None = None) -> str:
        rows = await notes.list_notes(category=category, tag=tag)
        if not rows:
            return "No notes found."
        shown = rows[:LIST_NOTES_CAP]
        text = "Notes (id in brackets):\n" + "\n".join(
            f"[{n.id}] {n.content} (category={n.category}, tags={n.tags})" for n in shown
        )
        if len(rows) > LIST_NOTES_CAP:
            text += (
                f"\n… {len(rows) - LIST_NOTES_CAP} more not shown — narrow by "
                "category/tag or use search_notes."
            )
        return text

    async def search_notes(query: str) -> str:
        rows = await notes.search_notes(query)
        if not rows:
            return "No relevant notes found."
        return "Relevant notes:\n" + "\n".join(
            f"[{n.id}] {n.content} (category={n.category}, tags={n.tags})" for n in rows
        )

    async def list_note_categories() -> str:
        categories = await notes.list_categories()
        tags = await notes.list_tags()
        return (
            "Categories: " + (", ".join(categories) if categories else "none") + "\n"
            "Tags: " + (", ".join(tags) if tags else "none")
        )

    async def forget_note(note_id: int) -> str:
        deleted = await notes.forget_note(note_id)
        return f"Note {note_id} deleted." if deleted else f"No note with id {note_id}."

    async def log_conversation(owner_message: str, agent_reply: str) -> str:
        return await episodes.log(owner_message, agent_reply)

    async def search_conversations(query: str, scope: str = "mine") -> str:
        return await episodes.search(query, scope)

    async def list_reminders() -> str:
        reminders = await store.list_reminders()
        if not reminders:
            return "No pending reminders."
        return "Pending reminders:\n" + "\n".join(
            # Self-notes are marked so the agent can tell its OWN plans apart
            # from the owner's reminders (and cancel the right one).
            f"[{r.id}] {r.due_at.isoformat()} — {r.text}"
            + (" (note to self)" if r.audience == AUDIENCE_AGENT else "")
            for r in reminders
        )

    async def cancel_reminder(reminder_id: int) -> str:
        cancelled = await store.delete_reminder(reminder_id)
        return (
            f"Reminder {reminder_id} cancelled."
            if cancelled
            else f"No reminder with id {reminder_id}."
        )

    return [
        Tool(
            name="remember_fact",
            description=(
                "Save one durable fact about the owner to shared long-term memory. "
                "Call this when the owner shares something worth remembering across "
                "conversations (profession, preferences, goals, life context). Write "
                "the fact in third person, short and self-contained. Do NOT save small "
                "talk or temporary states."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "One sentence, third person"}
                },
                "required": ["fact"],
            },
            handler=remember_fact,
        ),
        Tool(
            name="recall_memory",
            description=(
                "Search shared long-term memory for facts relevant to a query. Call "
                "this before replying when you need what is known about the owner "
                "(their context, preferences, past statements)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look up"}
                },
                "required": ["query"],
            },
            handler=recall_memory,
        ),
        Tool(
            name="list_memories",
            description=(
                "List every fact in shared memory, with ids. Call when the owner asks "
                "what you remember, or before deleting a memory."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=list_memories,
        ),
        Tool(
            name="forget_memory",
            description=(
                "Delete one memory by id. Call when the owner asks you to forget "
                "something. Use list_memories first to find the id."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "Id from list_memories"}
                },
                "required": ["memory_id"],
            },
            handler=forget_memory,
            usage='Id comes from list_memories, never from guessing: {"memory_id": 12}.',
        ),
        Tool(
            name="set_profile",
            description=(
                "Set the owner's timezone (IANA, e.g. Europe/Moscow) and/or home "
                "location. These are exact, always-available fields (not fuzzy "
                "memories) — the timezone decides when reminders fire."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "IANA timezone"},
                    "home_location": {"type": "string", "description": "Home city, free text"},
                },
            },
            handler=set_profile,
            usage=(
                'Pass only the field that changed — {"timezone": "Europe/Moscow"} '
                "leaves home_location untouched. The timezone must be an IANA "
                'name ("Europe/Moscow"), never an offset or a city in Russian.'
            ),
        ),
        Tool(
            name="get_profile",
            description="Get the owner's stored timezone and home location.",
            input_schema={"type": "object", "properties": {}},
            handler=get_profile,
        ),
        Tool(
            name="add_reminder",
            description=(
                "Schedule a reminder for the owner at a specific local time. STRONGLY "
                "prefer giving due_at as ISO-8601 WITH the owner's timezone offset "
                "(e.g. 2026-07-25T09:00:00+03:00) — you know the current time and zone "
                "from your context, so compute the offset. If you pass a naive time "
                "(no offset), it is interpreted in the owner's timezone (their profile, "
                "or the `tz` you pass), NOT UTC. recurrence is none|daily|weekly."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "What to remind about"},
                    "due_at": {"type": "string", "description": "ISO-8601, ideally with offset, e.g. 2026-07-25T09:00:00+03:00"},
                    "recurrence": {"type": "string", "enum": ["none", "daily", "weekly"]},
                    "tz": {"type": "string", "description": "IANA zone the time is in, e.g. Europe/Moscow (optional; used to anchor a naive due_at)"},
                },
                "required": ["text", "due_at"],
            },
            handler=add_reminder,
            usage=(
                'Always send due_at with the owner\'s UTC offset — you know the '
                'date, time and zone from your runtime context, so compute it: '
                '{"text": "call with Pasha", "due_at": "2026-08-01T11:00:00+03:00"}. '
                'A naive time is guessed at, an offset is exact.'
            ),
        ),
        Tool(
            name="remind_myself",
            description=(
                "Leave YOURSELF a note in the future. When it fires it does NOT "
                "get sent to the owner — it wakes you up, and you then decide "
                "what (if anything) to write. This is how you follow through on "
                "something later: check in after an event you know about, come "
                "back to a question that couldn't be answered yet, or continue "
                "something the owner mentioned in passing. Use it freely and on "
                "your own initiative — you don't need permission, and you don't "
                "need to be asked. Write the note TO YOURSELF and make it "
                "SELF-CONTAINED: what to do, and the situation it came from. By "
                "the time it fires the conversation may have scrolled out of "
                "your recent messages, so \"ask how it's going\" is a bad note and "
                "\"the owner was flying to Tbilisi on a 14:20 flight, ask how the "
                "landing and settling in went\" is a good one."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "Instruction to future-you: what to do and why",
                    },
                    "due_at": {"type": "string", "description": "ISO-8601, ideally with offset"},
                    "recurrence": {"type": "string", "enum": ["none", "daily", "weekly"]},
                    "tz": {"type": "string", "description": "IANA zone (optional)"},
                },
                "required": ["note", "due_at"],
            },
            handler=remind_myself,
            usage=(
                'The owner says he flies to Tbilisi at 14:20 and it is a 3h '
                'flight — work the landing time out yourself and schedule the '
                'follow-up: {"note": "The owner landed in Tbilisi — ask how the '
                'flight and settling in went", "due_at": "2026-08-01T18:00:00+04:00"} '
                "(landing local time, plus a little). "
                "Do NOT report the mechanics back: never \"I set myself a "
                "reminder\". Either say the human thing (\"I'll check in when "
                "you land\") or say nothing about it at all — the owner does "
                "not need to know how you remember."
            ),
        ),
        Tool(
            name="list_reminders",
            description=(
                "List pending reminders, soonest first — both the owner's and "
                "your own notes to self (marked as such). Call before cancelling."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=list_reminders,
        ),
        Tool(
            name="cancel_reminder",
            description=(
                "Cancel one pending reminder by id. Call when the owner says a "
                "reminder is no longer needed (\"cancel the reminder about...\", "
                "\"don't wake me at 7\"). Use list_reminders FIRST to find the id — "
                "never guess it. Cancelling is permanent."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "integer",
                        "description": "Id from list_reminders",
                    }
                },
                "required": ["reminder_id"],
            },
            handler=cancel_reminder,
            usage=(
                'Id comes from list_reminders: {"reminder_id": 7}. Match it by '
                "text before cancelling — cancelling the wrong one is silent."
            ),
        ),
        Tool(
            name="save_note",
            description=(
                "Save an explicit note to the owner's note list. Call this ONLY "
                "when the owner explicitly asks you to write something down "
                "(\"note this\", \"add to notes\", \"запиши в заметки\") — never "
                "infer a note the way you infer memory facts. If the owner didn't "
                "state a category or tags, infer them yourself from the content "
                "before calling this — never leave them empty and never ask a "
                "clarifying question just to fill them in. Check list_note_categories "
                "first if unsure, and reuse an existing close category instead of "
                "minting a near-duplicate."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The note text"},
                    "category": {
                        "type": "string",
                        "description": "One bucket, inferred from content if not stated (e.g. 'business')",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Finer-grained labels, inferred from content if not stated",
                    },
                },
                "required": ["content"],
            },
            handler=save_note,
            usage=(
                'Owner: "запиши идею для бизнеса - продажа лодок из Китая" -> '
                '{"content": "Idea: sell boats imported from China", '
                '"category": "business", "tags": ["ideas", "sales"]}.'
            ),
        ),
        Tool(
            name="list_notes",
            description=(
                "List notes, newest first, with ids. Optionally filter by an "
                "exact category or a tag. Call when the owner asks what notes "
                "they have (overall or in a category/tag), or before forgetting one."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Exact category to filter by"},
                    "tag": {"type": "string", "description": "Tag the note must have"},
                },
            },
            handler=list_notes,
        ),
        Tool(
            name="search_notes",
            description=(
                "Semantic search over notes for a query. Call when the owner "
                "asks about a note by topic rather than by category/tag "
                "(\"what did I note about...\")."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for"}
                },
                "required": ["query"],
            },
            handler=search_notes,
        ),
        Tool(
            name="list_note_categories",
            description=(
                "List every category and tag currently in use across notes. "
                "Call before assigning a category/tag to a new note when unsure, "
                "so you reuse an existing one instead of fragmenting the taxonomy "
                "with a near-duplicate — or when the owner asks what categories/"
                "tags exist."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=list_note_categories,
        ),
        Tool(
            name="forget_note",
            description=(
                "Delete one note by id. Call when the owner asks to remove a "
                "note. Use list_notes or search_notes FIRST to find the id — "
                "never guess it."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "integer", "description": "Id from list_notes/search_notes"}
                },
                "required": ["note_id"],
            },
            handler=forget_note,
            usage='Id comes from list_notes/search_notes: {"note_id": 5}.',
        ),
        Tool(
            name="log_conversation",
            description=(
                "SYSTEM TOOL — the agent harness calls this automatically after "
                "every exchange to archive it. Never call it yourself."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "owner_message": {"type": "string"},
                    "agent_reply": {"type": "string"},
                },
                "required": ["owner_message", "agent_reply"],
            },
            handler=log_conversation,
        ),
        Tool(
            name="search_conversations",
            description=(
                "Semantic search over the archive of past conversations with the "
                "owner (up to a year back). Call when the owner refers to an "
                "earlier dialogue (\"what did we decide about...\", \"you told me "
                "about...\") "
                "and the fact isn't in shared memory. scope='mine' (default) "
                "searches ONLY your own dialogs; pass 'all' or another agent's "
                "slug ONLY when the owner explicitly asks to search conversations "
                "with other agents."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for"},
                    "scope": {
                        "type": "string",
                        "description": "'mine' (default) | 'all' | an agent slug",
                    },
                },
                "required": ["query"],
            },
            handler=search_conversations,
            usage=(
                'Leave scope alone unless the owner asks about ANOTHER agent\'s '
                'dialogs: {"query": "vacation in Georgia"} searches only your own. '
                'scope="all" is for "what did I discuss with Kuzya" — not a way to '
                "widen a search that came back empty."
            ),
        ),
    ]


def build_registry(
    embedder: Embedder,
    episodes: EpisodeStore | None = None,
    notes: NoteStore | None = None,
) -> ToolRegistry:
    """Assemble Brain's built-in tool registry (memory + conversation archive + notes)."""
    store = MemoryStore(embedder)
    registry = ToolRegistry()
    registry.register_all(
        build_tools(store, episodes or EpisodeStore(embedder), notes or NoteStore(embedder))
    )
    return registry
