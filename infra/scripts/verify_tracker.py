#!/usr/bin/env python3
# =============================================================================
# Tracker verification — scripts/verify_tracker.py
# =============================================================================
# WHAT: Drives the whole tracker loop against a RUNNING tracker, end to end:
#   health → register project → delegate Directive → TWO concurrent claims (must
#   yield exactly one) → report running → report done → read back → illegal
#   transition refused → cross-project report refused.
#
# WHY the concurrent-claim step: it's the one correctness property that a
#   single-threaded test can't prove — that FOR UPDATE SKIP LOCKED stops two
#   agents grabbing the same Directive. We fire both claims at once and assert
#   exactly one wins.
#
# WHY the lease and illegal-transition steps: they are the two v2 invariants a
#   booting container cannot check for you — the poller tier must never be
#   given a lease (it never heartbeats, so the sweeper would requeue healthy
#   work), and a terminal Directive must have no way back.
#
# RUN (tracker up via `docker compose up -d tracker`):
#   TRACKER_URL=http://localhost:8770 \
#   TRACKER_ADMIN_TOKEN=<your admin token> \
#   python scripts/verify_tracker.py
#
# It registers a throwaway project named verify_<pid> so repeated runs don't
# collide. It does not clean up the row — the tracker has no delete endpoint by
# design; use a throwaway DB if you want a pristine table.
# =============================================================================

import asyncio
import os
import sys

import httpx

BASE = os.environ.get("TRACKER_URL", "http://localhost:8770").rstrip("/")
ADMIN = os.environ.get("TRACKER_ADMIN_TOKEN", "")
PROJECT = f"verify_{os.getpid()}"


def _admin() -> dict:
    return {"Authorization": f"Bearer {ADMIN}"}


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def fail(msg: str) -> None:
    print(f"  ❌ {msg}")
    sys.exit(1)


async def main() -> None:
    if not ADMIN:
        fail("Set TRACKER_ADMIN_TOKEN (and TRACKER_URL) in the environment first.")

    print(f"Verifying tracker at {BASE} (project '{PROJECT}')")
    async with httpx.AsyncClient(timeout=15) as c:
        # 1. health
        r = await c.get(f"{BASE}/health")
        if r.status_code != 200 or r.json().get("status") != "ok":
            fail(f"/health returned {r.status_code}: {r.text}")
        ok("health")

        # 2. register project
        r = await c.post(f"{BASE}/projects", headers=_admin(),
                         json={"name": PROJECT, "description": "verification run"})
        if r.status_code != 201:
            fail(f"register returned {r.status_code}: {r.text}")
        token = r.json()["token"]
        ptok = {"Authorization": f"Bearer {token}"}
        ok(f"registered, got project token ({len(token)} chars)")

        # 3. delegate one task
        r = await c.post(f"{BASE}/tasks", headers=_admin(),
                         json={"project": PROJECT, "title": "do the thing",
                               "description": "full brief"})
        if r.status_code != 201 or r.json()["status"] != "queued":
            fail(f"delegate returned {r.status_code}: {r.text}")
        task_id = r.json()["task_id"]
        ok(f"delegated task #{task_id} (queued)")

        # 4. TWO concurrent claims — exactly one must win the single queued task
        r1, r2 = await asyncio.gather(
            c.post(f"{BASE}/projects/{PROJECT}/claim", headers=ptok, json={"agent": "a1"}),
            c.post(f"{BASE}/projects/{PROJECT}/claim", headers=ptok, json={"agent": "a2"}),
        )
        codes = sorted([r1.status_code, r2.status_code])
        if codes != [200, 204]:
            fail(f"concurrent claim gave statuses {codes}, expected [200, 204] "
                 f"(atomic claim broken — both agents grabbed the task)")
        winner = r1 if r1.status_code == 200 else r2
        claimed = winner.json()["task"]
        # v2 lifecycle: a claim lands in `dispatched` — there is no `claimed`
        # state any more, whichever tier took the Directive (architecture §4).
        if claimed["id"] != task_id or claimed["status"] != "dispatched":
            fail(f"claimed task wrong: {claimed}")
        ok(f"atomic claim: exactly one agent won (#{task_id} → claimed by {claimed['claimed_by']})")
        # The poller tier never heartbeats, so it must never be given a lease —
        # otherwise the Step 4 sweeper would requeue healthy long-running work.
        if claimed["lease_expires_at"] is not None:
            fail(f"poller claim opened a lease, it must not: {claimed}")
        ok("poller claim opened no lease (never swept)")

        # 5. a second claim now finds nothing queued → 204
        r = await c.post(f"{BASE}/projects/{PROJECT}/claim", headers=ptok, json={"agent": "a1"})
        if r.status_code != 204:
            fail(f"second claim should be 204, got {r.status_code}")
        ok("empty queue returns 204")

        # 6. report running, then done with an artifact
        r = await c.post(f"{BASE}/tasks/{task_id}/report", headers=ptok,
                         json={"status": "running"})
        if r.status_code != 200 or r.json()["task"]["status"] != "running":
            fail(f"report running failed: {r.status_code} {r.text}")
        ok("reported running")

        r = await c.post(f"{BASE}/tasks/{task_id}/report", headers=ptok,
                         json={"status": "done", "summary": "built it",
                               "artifacts": [{"type": "pr", "url": "https://example/pr/1"}]})
        if r.status_code != 200 or r.json()["task"]["status"] != "done":
            fail(f"report done failed: {r.status_code} {r.text}")
        ok("reported done with artifact")

        # 7. admin reads it back
        r = await c.get(f"{BASE}/tasks/{task_id}", headers=_admin())
        task = r.json()["task"]
        if task["status"] != "done" or not task["artifacts"]:
            fail(f"read-back wrong: {task}")
        ok("read back: done, artifact present")

        # 7b. an illegal jump is refused: `done` is terminal, so nothing —
        # not even its owner — may move it back to `running` (409, not 400:
        # the payload is fine, the Directive's state is not).
        r = await c.post(f"{BASE}/tasks/{task_id}/report", headers=ptok,
                         json={"status": "running"})
        if r.status_code != 409:
            fail(f"done → running should be 409 (terminal), got {r.status_code}: {r.text}")
        ok("illegal transition rejected (done → running → 409)")

        # 8. a foreign project token must NOT report on this task
        r = await c.post(f"{BASE}/projects", headers=_admin(),
                         json={"name": f"{PROJECT}_other"})
        other = {"Authorization": f"Bearer {r.json()['token']}"}
        r = await c.post(f"{BASE}/tasks/{task_id}/report", headers=other,
                         json={"status": "cancelled"})
        if r.status_code != 404:
            fail(f"cross-project report should 404, got {r.status_code}")
        ok("cross-project report rejected")

    print("\nAll checks passed. 🎉")


if __name__ == "__main__":
    asyncio.run(main())
