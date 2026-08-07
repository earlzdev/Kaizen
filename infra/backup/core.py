# =============================================================================
# Backup core — backup/core.py
# =============================================================================
# WHAT: The pipeline logic: create (pg_dumpall → gzip → age → S3), list, prune,
#       and restore. No scheduling/API here — that's main.py; keeping the logic
#       separate makes it callable from the service, the run-once script, and the
#       restore script.
#
# WHY a subprocess pipeline (pg_dumpall | gzip | age): streams the dump without
#       ever writing plaintext SQL to disk — only the encrypted blob is written
#       (to a temp file) before upload. age encrypts to the PUBLIC recipient, so
#       this process cannot decrypt what it produced.
#
# WHY restore is here but gated behind an identity file: restore is destructive
#       (recreates databases) and needs the OFFLINE age private key, which the
#       caller passes as a file path — it is never stored in the image/env.
#
# HOW: `create_backup()` -> key; `list_backups()`; `restore_backup(key, id)`.
# =============================================================================

import datetime
import logging
import os
import subprocess
import tempfile

from infra.backup.config import settings

logger = logging.getLogger(__name__)


def _s3():
    """Boto3 S3 client for Yandex Object Storage (lazy import — keeps this module
    importable without boto3 installed)."""
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.backup_s3_endpoint,
        region_name=settings.backup_s3_region,
        aws_access_key_id=settings.backup_s3_access_key,
        aws_secret_access_key=settings.backup_s3_secret_key,
    )


def _pg_env() -> dict:
    return {**os.environ, "PGPASSWORD": settings.postgres_password}


def create_backup() -> str:
    """Dump the whole cluster, encrypt, upload to S3, prune old. Returns the S3
    key. Raises on any pipeline/upload failure (the caller logs/surfaces it)."""
    if not settings.configured:
        raise RuntimeError("backup not configured (need S3 bucket/keys + age recipient)")

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    key = f"{settings.backup_s3_prefix}/kaizen_{stamp}.sql.gz.age"

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sql.gz.age")
    os.close(tmp_fd)
    # pg_dumpall's stderr goes to a FILE, not a PIPE: a PIPE we don't drain until
    # after the downstream finishes can fill its ~64 KB buffer (e.g. many
    # warnings), block pg_dumpall, and deadlock the whole chain. A file never
    # blocks. `age` is the terminal process, so its stderr via communicate() is
    # safe to read at the end.
    dump_err = tempfile.TemporaryFile()
    try:
        # pg_dumpall | gzip | age -r <recipient> -o tmp_path
        dump = subprocess.Popen(
            # --clean --if-exists: the dump DROPs each DB (IF EXISTS) before
            # recreating it, so a restore cleanly overwrites whatever is there —
            # no "database already exists" conflicts, idempotent on any cluster.
            ["pg_dumpall", "--clean", "--if-exists",
             "-h", settings.postgres_host, "-p", str(settings.postgres_port),
             "-U", settings.postgres_user],
            stdout=subprocess.PIPE, stderr=dump_err, env=_pg_env(),
        )
        gz = subprocess.Popen(["gzip", "-c"], stdin=dump.stdout, stdout=subprocess.PIPE)
        dump.stdout.close()  # let gzip get EOF/SIGPIPE if dump dies
        age = subprocess.Popen(
            ["age", "-r", settings.backup_age_recipient, "-o", tmp_path],
            stdin=gz.stdout, stderr=subprocess.PIPE,
        )
        gz.stdout.close()

        age_err = age.communicate()[1]
        gz_rc = gz.wait()
        dump_rc = dump.wait()
        dump_err.seek(0)
        dump_err_text = dump_err.read().decode(errors="replace")
        if dump_rc != 0:
            raise RuntimeError(f"pg_dumpall failed ({dump_rc}): {dump_err_text[:500]}")
        if gz_rc != 0:
            raise RuntimeError(f"gzip failed ({gz_rc})")
        if age.returncode != 0:
            raise RuntimeError(f"age failed: {age_err.decode(errors='replace')[:500]}")

        size = os.path.getsize(tmp_path)
        if size == 0:
            raise RuntimeError("backup produced an empty file — aborting")
        _s3().upload_file(tmp_path, settings.backup_s3_bucket, key)
        logger.info("Backup uploaded: s3://%s/%s (%d bytes)", settings.backup_s3_bucket, key, size)
    finally:
        dump_err.close()
        os.path.exists(tmp_path) and os.remove(tmp_path)

    prune()
    return key


def list_backups() -> list[dict]:
    """All backups under the prefix, newest first."""
    s3 = _s3()
    resp = s3.list_objects_v2(
        Bucket=settings.backup_s3_bucket, Prefix=settings.backup_s3_prefix + "/"
    )
    items = [
        {"key": o["Key"], "size": o["Size"],
         "last_modified": o["LastModified"].isoformat()}
        for o in resp.get("Contents", [])
        if o["Key"].endswith(".sql.gz.age")
    ]
    items.sort(key=lambda x: x["last_modified"], reverse=True)
    return items


def prune() -> int:
    """Delete backups beyond the newest `backup_keep`. Returns how many removed."""
    items = list_backups()
    stale = items[settings.backup_keep :]
    if not stale:
        return 0
    s3 = _s3()
    s3.delete_objects(
        Bucket=settings.backup_s3_bucket,
        Delete={"Objects": [{"Key": i["key"]} for i in stale]},
    )
    logger.info("Pruned %d old backup(s)", len(stale))
    return len(stale)


def restore_backup(key: str, identity_path: str) -> None:
    """DESTRUCTIVE. Download `key`, decrypt with the age identity file, and
    restore the whole cluster via psql. The caller must have stopped the app
    services first (databases can't be recreated while connected)."""
    if not os.path.isfile(identity_path):
        raise FileNotFoundError(f"age identity not found: {identity_path}")

    with tempfile.TemporaryDirectory() as d:
        enc = os.path.join(d, "dump.sql.gz.age")
        sql = os.path.join(d, "dump.sql")
        _s3().download_file(settings.backup_s3_bucket, key, enc)

        # age -d -i <identity> enc | gunzip > sql
        # age's stderr → a FILE (not a PIPE): same deadlock avoidance as the
        # backup pipeline — an undrained PIPE could block age and hang gunzip.
        dec_err = tempfile.TemporaryFile()
        try:
            with open(sql, "wb") as out:
                dec = subprocess.Popen(
                    ["age", "-d", "-i", identity_path, enc],
                    stdout=subprocess.PIPE, stderr=dec_err,
                )
                gz = subprocess.Popen(["gunzip", "-c"], stdin=dec.stdout, stdout=out)
                dec.stdout.close()
                gz_rc = gz.wait()
                dec_rc = dec.wait()
            dec_err.seek(0)
            dec_err_text = dec_err.read().decode(errors="replace")
            if dec_rc != 0:
                raise RuntimeError(f"age decrypt failed: {dec_err_text[:500]}")
            if gz_rc != 0:
                raise RuntimeError("gunzip failed during restore")
        finally:
            dec_err.close()

        # Restore into the cluster (pg_dumpall output recreates DBs via \connect;
        # connect to the always-present 'postgres' maintenance DB).
        rc = subprocess.run(
            ["psql", "-h", settings.postgres_host, "-p", str(settings.postgres_port),
             "-U", settings.postgres_user, "-d", "postgres", "-f", sql],
            env=_pg_env(),
        ).returncode
        if rc != 0:
            raise RuntimeError(f"psql restore failed ({rc})")
    logger.info("Restore complete from %s", key)
