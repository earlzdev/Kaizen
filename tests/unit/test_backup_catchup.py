# =============================================================================
# Unit tests — infra/backup/main.py (boot catch-up + health state)
# =============================================================================
# WHAT: the Step-8 catch-up decision: back up at boot when the newest backup is
#       overdue or absent, skip when fresh, never crash when S3 is unreachable;
#       plus the /health last_success/last_error state.
# WHY: a container restarting more often than the 24h schedule used to
#       silently NEVER back up — the catch-up is the fix, so it gets a test.
# HOW: core.list_backups / core.create_backup are monkeypatched — no boto3,
#       no S3, no Postgres.
# =============================================================================

import datetime

import pytest

import infra.backup.main as backup_main
from infra.backup import core


def _iso_hours_ago(hours: float) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    ).isoformat()


@pytest.fixture(autouse=True)
def _reset_state():
    backup_main._state.update({"last_success": None, "last_error": None})
    yield


@pytest.fixture
def created(monkeypatch):
    """Record create_backup calls; return the recording list."""
    calls = []

    def fake_create():
        calls.append(True)
        return "kaizen/fake.sql.gz.age"

    monkeypatch.setattr(core, "create_backup", fake_create)
    return calls


async def test_fresh_backup_skips_catch_up(monkeypatch, created):
    monkeypatch.setattr(
        core, "list_backups", lambda: [{"key": "k", "size": 1, "last_modified": _iso_hours_ago(2)}]
    )
    await backup_main._catch_up_if_overdue()
    assert created == []


async def test_overdue_backup_triggers_catch_up(monkeypatch, created):
    monkeypatch.setattr(
        core, "list_backups", lambda: [{"key": "k", "size": 1, "last_modified": _iso_hours_ago(48)}]
    )
    await backup_main._catch_up_if_overdue()
    assert created == [True]
    assert backup_main._state["last_success"] is not None


async def test_no_backups_at_all_triggers_catch_up(monkeypatch, created):
    monkeypatch.setattr(core, "list_backups", lambda: [])
    await backup_main._catch_up_if_overdue()
    assert created == [True]


async def test_s3_failure_skips_catch_up_without_crashing(monkeypatch, created):
    def boom():
        raise ConnectionError("s3 down")

    monkeypatch.setattr(core, "list_backups", boom)
    await backup_main._catch_up_if_overdue()  # must not raise
    assert created == []


async def test_failed_backup_records_last_error(monkeypatch):
    def boom():
        raise RuntimeError("pg_dumpall exploded")

    monkeypatch.setattr(core, "create_backup", boom)
    monkeypatch.setattr(core, "list_backups", lambda: [])
    await backup_main._catch_up_if_overdue()  # swallowed + logged
    assert backup_main._state["last_success"] is None
    assert "pg_dumpall exploded" in backup_main._state["last_error"]
