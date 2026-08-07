# =============================================================================
# Enrollment CLI — brain/enroll_cli.py
# =============================================================================
# WHAT: A tiny CLI run INSIDE the brain container by scripts/approve.sh so the
#       terminal can list/approve/reject pending agent connect-requests. It talks
#       to the DB directly (it's in the brain container), so no HTTP/token dance.
#
# HOW:
#   python -m brain.enroll_cli list             # prints "<id>\t<slug>" per pending
#   python -m brain.enroll_cli approve <id>
#   python -m brain.enroll_cli reject <id>
# =============================================================================

import asyncio
import sys

from sqlalchemy.exc import OperationalError, ProgrammingError

from brain.agents import AgentStore
from brain.enroll import EnrollmentStore

# Exit code meaning "Brain isn't ready yet (DB/table missing)" — the approve
# script keeps waiting on this instead of reporting an empty list.
NOT_READY = 3


async def _run(argv: list[str]) -> int:
    store = EnrollmentStore(AgentStore())
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        try:
            pending = await store.list_pending()
        except (ProgrammingError, OperationalError, OSError):
            # Table not migrated yet / DB not up: Brain is still booting. Signal
            # "not ready" (distinct from "no pending") so the caller waits.
            print("brain-not-ready", file=sys.stderr)
            return NOT_READY
        for e in pending:
            print(f"{e.id}\t{e.slug}")
        return 0
    if cmd in ("approve", "reject") and len(argv) == 2 and argv[1].isdigit():
        ok = await (store.approve if cmd == "approve" else store.reject)(int(argv[1]))
        print("ok" if ok else "not found")
        return 0 if ok else 1
    print("usage: python -m brain.enroll_cli [list|approve <id>|reject <id>]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(sys.argv[1:])))
