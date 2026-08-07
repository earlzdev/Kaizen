# =============================================================================
# Backup service — backup/
# =============================================================================
# WHAT: Backs up the WHOLE Postgres cluster (all service DBs + roles) on a
#       schedule and on demand: pg_dumpall → gzip → age-encrypt → Yandex S3,
#       with retention. Exposes a tiny internal API the admin panel drives.
#
# WHY one service, whole-cluster: the system is one Postgres instance with a DB
#       per service (brain/kaya/mentor/tracker/therapist). `pg_dumpall` captures
#       them all + roles in a single file, so one backup = the entire system.
#
# WHY age with an offline private key: the service holds only the age PUBLIC key
#       (recipient) — it can encrypt but NOT decrypt. The private key lives
#       offline, so even a fully compromised server can't read the backups in S3.
#       Restore (which needs the private key) is a deliberate, host-run script.
#
# HOW it runs: `python -m backup.main` (the `backup` service in docker-compose),
#       using its own image (Dockerfile.backup) that carries pg_dumpall + age +
#       boto3. Restore: `scripts/restore.sh <key>`.
# =============================================================================
