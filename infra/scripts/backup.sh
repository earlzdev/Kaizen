#!/usr/bin/env bash
# =============================================================================
# Manual backup — scripts/backup.sh
# =============================================================================
# WHAT: Triggers ONE backup now via the running `backup` service (pg_dumpall of
#       the whole cluster → gzip → age-encrypt → Yandex S3). Prints the S3 key.
#
# WHY through the service (not pg_dump here): the backup image carries the
#       version-matched pg_dumpall + age + boto3 and the S3/age config; the host
#       needs none of it. (The service also runs scheduled backups on its own.)
#
# HOW:
#   ./scripts/backup.sh
# Restore a backup with: ./scripts/restore.sh <key> <age-identity-file>
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "Running a backup via the backup service…"
docker compose --env-file .env -f deploy/docker-compose.yml exec -T backup python3 -m infra.backup.run_once
