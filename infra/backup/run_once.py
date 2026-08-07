# =============================================================================
# One-shot backup — backup/run_once.py
# =============================================================================
# WHAT: Runs a single backup now and prints the S3 key. Used by scripts/backup.sh
#       (`docker compose exec backup python -m backup.run_once`) for a manual
#       backup without going through the API.
#
# WHY a separate CLI (not just the API): a manual/one-shot backup shouldn't need
#       the HTTP API, the auth token, or Brain to be up — this runs the same
#       core.create_backup() in-process and exits non-zero on failure, so it
#       composes cleanly with shell scripts and `docker compose exec`.
#
# HOW: `python -m backup.run_once` (inside the running backup container).
# =============================================================================

import logging
import sys

from infra.backup import core

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main() -> None:
    try:
        key = core.create_backup()
    except Exception as e:  # surface a clear failure + non-zero exit for the script
        print(f"backup failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(key)


if __name__ == "__main__":
    main()
