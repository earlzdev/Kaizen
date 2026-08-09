# =============================================================================
# Integration tests — Brain's reminder tools against a SCRATCH database
# =============================================================================
# WHAT: the built-in reminder tools an agent actually calls over MCP —
#       add_reminder / list_reminders / cancel_reminder — through the real
#       ToolRegistry (schema validation included), on a throwaway database.
# WHY: cancel_reminder is new (owner's ask, 2026-07-29): the store could
#       always delete a reminder, but no TOOL exposed it, so agents could
#       create reminders and never take one back.
# HOW: build_tools(store, episodes, notes) with a fake embedder; the scratch-DB
#       sessionmaker comes from conftest.py (skips without Postgres).
# =============================================================================

import pytest

from brain.episodes import EpisodeStore
from brain.memory import MemoryStore
from brain.notes import NoteStore
from brain.registry import ToolRegistry
from brain.tools import build_tools


class FakeEmbedder:
    """No model needed: reminders don't embed, and facts aren't exercised here."""

    async def embed(self, text: str) -> list[float]:
        return [0.0] * 384


@pytest.fixture
def registry(scratch_sessions) -> ToolRegistry:
    embedder = FakeEmbedder()
    store = MemoryStore(embedder, session_factory=scratch_sessions)
    episodes = EpisodeStore(embedder, session_factory=scratch_sessions)
    notes = NoteStore(embedder, session_factory=scratch_sessions)
    reg = ToolRegistry()
    reg.register_all(build_tools(store, episodes, notes))
    return reg


def _first_id(listing: str) -> int:
    """Pull the id out of a '[12] 2026-... — text' listing line."""
    return int(listing.split("[", 1)[1].split("]", 1)[0])


async def test_add_list_cancel_roundtrip(registry):
    added = await registry.execute(
        "add_reminder",
        {"text": "позвонить маме", "due_at": "2026-08-01T09:00:00+03:00"},
    )
    assert not added.is_error

    listing = await registry.execute("list_reminders", {})
    assert "позвонить маме" in listing.text
    reminder_id = _first_id(listing.text)

    cancelled = await registry.execute("cancel_reminder", {"reminder_id": reminder_id})
    assert not cancelled.is_error
    assert f"Reminder {reminder_id} cancelled." == cancelled.text

    assert (await registry.execute("list_reminders", {})).text == "No pending reminders."


async def test_cancel_unknown_id_is_not_an_error_result(registry):
    """A miss is information for the model, not a failure — it should say so
    plainly so the model re-reads list_reminders instead of apologising."""
    result = await registry.execute("cancel_reminder", {"reminder_id": 4242})
    assert not result.is_error
    assert result.text == "No reminder with id 4242."


async def test_cancel_is_idempotent(registry):
    await registry.execute(
        "add_reminder", {"text": "зарядка", "due_at": "2026-08-02T07:00:00+03:00"}
    )
    rid = _first_id((await registry.execute("list_reminders", {})).text)
    assert (await registry.execute("cancel_reminder", {"reminder_id": rid})).text.endswith(
        "cancelled."
    )
    again = await registry.execute("cancel_reminder", {"reminder_id": rid})
    assert again.text == f"No reminder with id {rid}."


async def test_cancel_validates_its_argument(registry):
    missing = await registry.execute("cancel_reminder", {})
    assert missing.is_error and "missing required" in missing.text

    wrong_type = await registry.execute("cancel_reminder", {"reminder_id": "12"})
    assert wrong_type.is_error and "must be a integer" in wrong_type.text


async def test_cancel_only_removes_the_named_reminder(registry):
    await registry.execute(
        "add_reminder", {"text": "первое", "due_at": "2026-08-03T09:00:00+03:00"}
    )
    await registry.execute(
        "add_reminder", {"text": "второе", "due_at": "2026-08-04T09:00:00+03:00"}
    )
    listing = (await registry.execute("list_reminders", {})).text
    first_id = _first_id(listing)

    await registry.execute("cancel_reminder", {"reminder_id": first_id})
    remaining = (await registry.execute("list_reminders", {})).text
    assert "первое" not in remaining
    assert "второе" in remaining
