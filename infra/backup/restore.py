# =============================================================================
# Restore CLI — backup/restore.py
# =============================================================================
# WHAT: Restores the cluster from an S3 backup key using an age identity
#       (private key) file. DESTRUCTIVE — recreates databases. Invoked by
#       scripts/restore.sh, which stops the app services first and mounts the
#       offline identity into the container.
#
# WHY a CLI (and not an API endpoint): restore needs the OFFLINE age private key
#       and drops+recreates live databases — it must never be a one-click panel
#       action reachable over the network. Keeping it a deliberate host-run CLI
#       (fed the identity file path) ensures a human decides, with the key in hand.
#
# HOW: `python -m backup.restore <s3-key> <path-to-age-identity>`.
# =============================================================================

import logging
import sys

from infra.backup import core

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        print("usage: python -m backup.restore <s3-key> <age-identity-file>", file=sys.stderr)
        sys.exit(2)
    key, identity = argv
    try:
        core.restore_backup(key, identity)
    except Exception as e:
        print(f"restore failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"restored from {key}")


if __name__ == "__main__":
    main(sys.argv[1:])
