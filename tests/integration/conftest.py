# =============================================================================
# Integration-test fixtures — tests/integration/conftest.py
# =============================================================================
# WHAT: The shared scratch-database fixture: CREATE kaizen_test_<random> with
#       pgvector, build Brain's schema via metadata.create_all (the same path
#       boot uses), yield a sessionmaker bound to it, DROP it afterwards.
# WHY here: every integration test needs the same throwaway DB; one fixture in
#       conftest keeps the CREATE/DROP discipline in one place.
# HOW: credentials come from TEST_POSTGRES_* env vars with the compose defaults
#       (never from .env). Postgres unreachable -> the test SKIPs, so `pytest`
#       stays green on a machine without the dev stack running.
# =============================================================================

import os
import uuid

import pytest
from sqlalchemy import text

from brain.db.models import Base
from brain.db.session import make_engine, make_sessionmaker

HOST = os.environ.get("TEST_POSTGRES_HOST", "localhost")
PORT = int(os.environ.get("TEST_POSTGRES_PORT", "5432"))
USER = os.environ.get("TEST_POSTGRES_USER", "learnbot")
PASSWORD = os.environ.get("TEST_POSTGRES_PASSWORD", "learnbot")


@pytest.fixture
async def scratch_sessions():
    """CREATE a scratch database + Brain schema, yield a sessionmaker bound to
    it, DROP it afterwards. Skips the test when Postgres isn't reachable."""
    import asyncpg

    db_name = f"kaizen_test_{uuid.uuid4().hex[:12]}"
    try:
        conn = await asyncpg.connect(
            host=HOST, port=PORT, user=USER, password=PASSWORD, database="postgres",
            timeout=3,
        )
    except Exception as e:
        pytest.skip(f"Postgres not reachable at {HOST}:{PORT} — {e}")
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()

    url = f"postgresql+asyncpg://{USER}:{PASSWORD}@{HOST}:{PORT}/{db_name}"
    engine = make_engine(url)
    try:
        async with engine.begin() as c:
            await c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await c.run_sync(Base.metadata.create_all)
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()
        conn = await asyncpg.connect(
            host=HOST, port=PORT, user=USER, password=PASSWORD, database="postgres",
        )
        try:
            await conn.execute(f'DROP DATABASE "{db_name}"')
        finally:
            await conn.close()
