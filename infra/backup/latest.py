# =============================================================================
# Latest-backup lookup — infra/backup/latest.py
# =============================================================================
# WHAT: Prints the S3 key of the NEWEST backup (nothing else), exit 1 if none.
#
# WHY a tiny CLI: scripts/restore.sh supports `restore.sh latest <key-file>` —
#       it needs the newest key resolved INSIDE the backup container (which has
#       the S3 credentials/config); this is that resolver.
#
# HOW: `python3 -m infra.backup.latest` (inside the backup container).
# =============================================================================

import sys

from infra.backup import core


def main() -> None:
    try:
        backups = core.list_backups()
    except Exception as e:
        print(f"cannot list backups: {e}", file=sys.stderr)
        sys.exit(1)
    if not backups:
        print("no backups found", file=sys.stderr)
        sys.exit(1)
    print(backups[0]["key"])  # list_backups sorts newest-first


if __name__ == "__main__":
    main()
