#!/usr/bin/env bash
# =============================================================================
# Restore from a backup — scripts/restore.sh
# =============================================================================
# WHAT: DESTRUCTIVE. Restores the WHOLE Postgres cluster from an S3 backup,
#       overwriting every database. Needs your OFFLINE age identity (private key)
#       to decrypt.
#
# WHY a host script (not the panel): restore drops+recreates live databases, so
#       the app services must be stopped first. This orchestrates that safely:
#       stop agents/modules → decrypt+restore in an ephemeral backup container →
#       restart. Postgres + the backup image stay up; nothing else is connected
#       while databases are recreated (pg_dumpall was taken with --clean
#       --if-exists, so it overwrites cleanly).
#
# HOW:
#   ./infra/scripts/restore.sh <s3-key> <path-to-age-identity>
#   ./infra/scripts/restore.sh latest   <path-to-age-identity>   # newest backup
#   e.g. ./infra/scripts/restore.sh latest ~/keys/kaizen-age.txt
# List keys in the admin panel (Backups card) or via the backup API.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."

KEY="${1:-latest}"; IDENTITY="${2:-}"
# Allow `restore.sh <identity>` — one arg means "latest + this key file".
if [ -z "$IDENTITY" ] && [ -f "$KEY" ]; then
  IDENTITY="$KEY"; KEY="latest"
fi
if [ -z "$IDENTITY" ]; then
  echo "usage: infra/scripts/restore.sh [<s3-key>|latest] <age-identity-file>" >&2
  exit 2
fi
if [ ! -f "$IDENTITY" ]; then
  echo "age identity file not found: $IDENTITY" >&2
  exit 1
fi

# "latest": ask the backup service (it has the S3 creds) for the newest key.
if [ "$KEY" = "latest" ]; then
  echo "Resolving the newest backup in S3…"
  KEY="$(docker compose --env-file .env -f deploy/docker-compose.yml exec -T backup python3 -m infra.backup.latest)"
  [ -n "$KEY" ] || { echo "no backups found in S3" >&2; exit 1; }
  echo "Newest backup: $KEY"
fi

echo "!! DESTRUCTIVE — this OVERWRITES every database from the backup:"
echo "   key: $KEY"
read -r -p "Type 'yes' to proceed: " ok
[ "$ok" = "yes" ] || { echo "aborted"; exit 1; }

# Absolute path to the identity (macOS-safe; no realpath dependency).
ID_ABS="$(cd "$(dirname "$IDENTITY")" && pwd)/$(basename "$IDENTITY")"

echo "Stopping app services (postgres + backup stay up)…"
docker compose --env-file .env -f deploy/docker-compose.yml stop kaya brain tools

echo "Restoring from $KEY …"
docker compose --env-file .env -f deploy/docker-compose.yml run --rm -v "${ID_ABS}:/tmp/age_id:ro" \
  backup python3 -m infra.backup.restore "$KEY" /tmp/age_id

echo "Restarting services…"
docker compose --env-file .env -f deploy/docker-compose.yml up -d
echo "Restore complete."
