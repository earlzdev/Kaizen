# =============================================================================
# Unit tests — brain/tools.py remind_myself (the agent's note to itself)
# =============================================================================
# WHAT: the tool an agent calls to schedule its OWN wake-up: it must store
#       audience="agent" (add_reminder stays owner-only), reuse the shared time
#       resolution, and phrase its result as a note to self rather than as a
#       confirmation the owner asked for. Plus: list_reminders marks self-notes
#       so the agent can find and cancel them.
# WHY: audience is what decides, one hop later, whether the sweeper relays text
#       to the owner or wakes the agent — mixing them up either loses the
#       feature or leaks internal notes into Telegram.
# HOW: a duck-typed store records the calls; no database (the DB round trip is
#       covered by the integration tests).
# =============================================================================

from brain.db.models import AUDIENCE_AGENT, AUDIENCE_OWNER
from brain.registry import ToolRegistry
from brain.tools import build_tools

DUE = "2026-08-01T18:00:00+04:00"
NOTE = "Владелец прилетел в Тбилиси — спроси, как долетел"


class FakeStore:
    """The slice of MemoryStore the reminder tools use."""

    def __init__(self, reminders=()):
        self.added = []
        self._reminders = list(reminders)

    async def get_profile(self):
        return None

    async def add_reminder(self, text, due_at, recurrence="none", tz=None, audience=AUDIENCE_OWNER):
        self.added.append({"text": text, "due_at": due_at, "tz": tz, "audience": audience})
        return f"Reminder set for {due_at.isoformat()}: '{text}'"

    async def list_reminders(self):
        return self._reminders


class FakeReminder:
    def __init__(self, rid, text, audience, due_at):
        self.id, self.text, self.audience, self.due_at = rid, text, audience, due_at


def _registry(store) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_all(build_tools(store, episodes=None))
    return reg


async def test_remind_myself_stores_an_agent_audience():
    store = FakeStore()
    result = await _registry(store).execute(
        "remind_myself", {"note": NOTE, "due_at": DUE}
    )
    assert not result.is_error
    assert store.added[0]["audience"] == AUDIENCE_AGENT
    assert store.added[0]["text"] == NOTE


async def test_add_reminder_stays_owner_only():
    """The two tools are separate on purpose — add_reminder has no way to
    schedule a wake-up."""
    store = FakeStore()
    await _registry(store).execute(
        "add_reminder", {"text": "позвонить маме", "due_at": DUE}
    )
    assert store.added[0]["audience"] == AUDIENCE_OWNER
    schema = {t.name: t.input_schema for t in build_tools(store, episodes=None)}
    assert "audience" not in schema["add_reminder"]["properties"]


async def test_result_is_worded_as_a_note_to_self():
    """The model must not parrot «Reminder set for ...» back at the owner as if
    it were a confirmation that was asked for."""
    result = await _registry(FakeStore()).execute(
        "remind_myself", {"note": NOTE, "due_at": DUE}
    )
    assert result.text.startswith("Noted to self for")
    assert "Reminder set" not in result.text


async def test_bad_time_is_reported_not_stored():
    store = FakeStore()
    result = await _registry(store).execute(
        "remind_myself", {"note": NOTE, "due_at": "завтра вечером"}
    )
    assert result.text.startswith("Error:") and store.added == []


async def test_list_reminders_marks_self_notes():
    import datetime

    when = datetime.datetime(2026, 8, 1, 18, 0, tzinfo=datetime.timezone.utc)
    store = FakeStore([
        FakeReminder(1, "позвонить маме", AUDIENCE_OWNER, when),
        FakeReminder(2, NOTE, AUDIENCE_AGENT, when),
    ])
    text = (await _registry(store).execute("list_reminders", {})).text
    owner_line, self_line = [ln for ln in text.splitlines() if ln.startswith("[")]
    assert "(note to self)" not in owner_line
    assert self_line.startswith("[2]") and self_line.endswith("(note to self)")


def test_the_tool_teaches_the_owners_flight_example_and_the_silence_rule():
    """Owner's requirement: the usage note carries the flight example AND the
    ban on narrating the mechanics."""
    tools = {t.name: t for t in build_tools(FakeStore(), episodes=None)}
    usage = tools["remind_myself"].usage
    assert "14:20" in usage and "Tbilisi" in usage
    assert "I set myself a reminder" in usage
    assert "I'll check in when you land" in usage
