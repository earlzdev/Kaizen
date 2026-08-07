# =============================================================================
# Unit tests — agents/core/history.py
# =============================================================================
# WHAT: trim_to_user_start (the API-validity invariant) and InMemoryHistory's
#       window behavior.
# WHY: the Anthropic API rejects a history that doesn't start with a 'user'
#       message; this trim is shared by every History implementation.
# HOW: pure in-memory, no I/O.
# =============================================================================

from agents.core.history import InMemoryHistory, trim_to_user_start


def test_trim_drops_leading_assistant_turns():
    messages = [
        {"role": "assistant", "content": "a1"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a3"},
    ]
    assert trim_to_user_start(messages) == [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a3"},
    ]


def test_trim_keeps_user_start_untouched():
    messages = [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]
    assert trim_to_user_start(messages) == messages


def test_trim_all_assistant_gives_empty():
    assert trim_to_user_start([{"role": "assistant", "content": "a"}]) == []


def test_trim_does_not_mutate_input():
    messages = [{"role": "assistant", "content": "a"}, {"role": "user", "content": "u"}]
    trim_to_user_start(messages)
    assert len(messages) == 2


async def test_inmemory_window_and_trim():
    history = InMemoryHistory(window=3)
    await history.append("user", "u1")
    await history.append("assistant", "a1")
    await history.append("user", "u2")
    await history.append("assistant", "a2")
    # Window of 3 newest = [a1, u2, a2]; the leading assistant turn is trimmed.
    assert await history.load() == [
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]


async def test_inmemory_clear_reports_count():
    history = InMemoryHistory()
    await history.append("user", "u")
    await history.append("assistant", "a")
    assert await history.clear() == 2
    assert await history.load() == []
