# =============================================================================
# Integration tests — brain/memory.py against a SCRATCH database
# =============================================================================
# WHAT: MemoryStore over a real Postgres+pgvector: facts (recall ordering and
#       the in-SQL distance cut, near-duplicate refresh) and reminders (due →
#       delivered → recurrence advance). The first test of the kind Step 3 of
#       ARCHITECTURE_REVIEW.md was done FOR: a store handed an explicit
#       sessionmaker pointing at a throwaway database.
#
# WHY a scratch DB per test run: these tests must never touch the real `brain`
#       database; each run CREATEs kaizen_test_<random>, builds the schema via
#       metadata.create_all (same path as boot) and DROPs it afterwards.
#
# WHY a fake embedder: sentence-transformers is heavy and non-deterministic
#       across versions; hand-built 384-dim unit vectors give EXACT cosine
#       distances, which is what the ordering/threshold assertions need.
#
# HOW: the scratch-DB sessionmaker comes from tests/integration/conftest.py
#       (skips when Postgres is unreachable; credentials via TEST_POSTGRES_*
#       env vars, never .env).
# =============================================================================

import datetime
import math

import pytest

from brain.db.models import EMBED_DIM
from brain.memory import MemoryStore


def _vector(cosine_to_query: float) -> list[float]:
    """A unit vector at an exact cosine similarity to the query vector e0."""
    s = cosine_to_query
    v = [0.0] * EMBED_DIM
    v[0] = s
    v[1] = math.sqrt(max(0.0, 1.0 - s * s))
    return v


class FakeEmbedder:
    """Deterministic embeddings: exact cosine distances instead of a model.
    async to match the real Embedder's Step-6 contract (encode off-loop)."""

    VECTORS = {
        "query": _vector(1.0),
        "close fact": _vector(0.9),   # distance 0.1  — kept, ranked first
        "mid fact": _vector(0.5),     # distance 0.5  — kept, ranked second
        "far fact": _vector(0.0),     # distance 1.0  — cut by RECALL_MAX_DISTANCE
    }

    async def embed(self, text_: str) -> list[float]:
        if text_ in self.VECTORS:
            return self.VECTORS[text_]
        # Any other text: a stable arbitrary unit vector far from the query.
        return _vector(0.05)


@pytest.fixture
def store(scratch_sessions) -> MemoryStore:
    # duplicate_threshold pinned so the test doesn't drift with the setting.
    return MemoryStore(
        FakeEmbedder(), session_factory=scratch_sessions, duplicate_threshold=0.05
    )


async def test_recall_orders_by_distance_and_cuts_in_sql(store):
    """The Step 1 recall fix, finally verified against real pgvector SQL:
    results come back nearest-first and the >0.75 row never appears."""
    await store.remember("far fact")
    await store.remember("mid fact")
    await store.remember("close fact")
    assert await store.recall("query") == ["close fact", "mid fact"]


async def test_remember_refreshes_a_near_duplicate(store):
    """distance < DUPLICATE_THRESHOLD refreshes the row instead of piling up."""
    await store.remember("close fact")
    result = await store.remember("close fact")  # identical vector → distance 0
    assert result.startswith("Updated existing memory")
    facts = await store.list_facts()
    assert len(facts) == 1


async def test_distinct_facts_both_survive(store):
    await store.remember("close fact")   # cos 0.9 vs mid's 0.5 → distance ~0.48
    await store.remember("mid fact")
    assert len(await store.list_facts()) == 2


async def test_similar_but_distinct_facts_survive_the_tight_threshold(store):
    """The Step 9 regression test: distance 0.1 sits between the old loose
    threshold (0.15 — would OVERWRITE) and the new tight one (0.05 — keeps
    both). «паша любит кофе» must not eat «маша любит кофе»."""
    await store.remember("query")        # cos 1.0
    await store.remember("close fact")   # cos 0.9 → distance 0.1 from "query"
    facts = await store.list_facts()
    assert sorted(f.content for f in facts) == ["close fact", "query"]


async def test_reminder_lifecycle_one_off(store):
    due = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    await store.add_reminder("пей воду", due)
    now = datetime.datetime.now(datetime.timezone.utc)
    due_list = await store.due_reminders(now)
    assert [r.text for r in due_list] == ["пей воду"]

    await store.mark_reminder_delivered(due_list[0].id)
    assert await store.due_reminders(now) == []
    done = await store.list_reminders(include_done=True)
    assert done[0].is_done is True


async def test_recurring_reminder_advances_into_the_future(store):
    due = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)
    await store.add_reminder("зарядка", due, recurrence="daily", tz="Europe/Moscow")
    now = datetime.datetime.now(datetime.timezone.utc)
    reminder = (await store.due_reminders(now))[0]

    await store.mark_reminder_delivered(reminder.id)
    # Not done (it recurs), and the next occurrence is strictly in the future —
    # the 3 missed days are caught up in ONE jump, not a stale flurry.
    pending = await store.list_reminders()
    assert len(pending) == 1
    assert pending[0].is_done is False
    assert pending[0].due_at > now


async def test_reminder_dedup_window(store):
    due = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    first = await store.add_reminder("позвонить маме", due)
    second = await store.add_reminder(
        "позвонить маме", due + datetime.timedelta(seconds=30)
    )
    assert first.startswith("Reminder set")
    assert second.startswith("Reminder already set")
    assert len(await store.list_reminders()) == 1
