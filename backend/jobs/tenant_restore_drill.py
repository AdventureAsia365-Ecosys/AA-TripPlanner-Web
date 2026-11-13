"""AA-1093 part 3 — job kind ``tp_tenant_restore_drill`` (jobs Lambda op ``restore_drill``): prove that a
``tp_tenant_backup`` can be restored, without writing a live table.

Payload: ``{"tenant_id" | "tenant_slug", "backup_id": "<job id>"}``; without ``backup_id`` the tenant's latest
succeeded ``tp_tenant_backup`` job is used: an operator backup (``result.tenant_id``) or a daily pilot backup (one
entry of ``result.backups`` per tenant). S3 refusing a read fails the drill with ``s3_access_denied``
(:mod:`backend.jobs.tenant_store`).

1. Files. ``SHA256SUMS`` is read and its digest compared with the backup job's result (when the job row is still
   there); ``manifest.json`` and every table file must match their line in it and the manifest's own sha256.
2. Load. One transaction that is ALWAYS rolled back: per table ``CREATE TEMP TABLE pg_temp.rd_<table> (LIKE
   tripplanner.<table>) ON COMMIT DROP`` (the live column types, no constraint) and ``INSERT … SELECT * FROM
   jsonb_populate_recordset(…)`` of the file's lines. Its statements read live tables and write only its own
   ``pg_temp`` tables; nothing survives the rollback.
3. Compare, per table: manifest rows, restored rows, the digest of the restored rows written back the backup's way
   (``to_jsonb(row)`` per line, key order; it must equal the file's sha256, so every value survived the round trip),
   live rows of the tenant now, restored keys found live, restored rows identical to the live row.

Pass = the files check out, and restored = manifest rows and the digest matches for every table. Live numbers are
reported, not judged (a tenant may have written since, or been erased), as in the CRM drill. A failed drill asks for
attention. The result holds counts and digests only (DM-6.3).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from backend.jobs import tenant_backup, tenant_store, tenant_target
from backend.jobs.queue import Job
from backend.jobs.runner import PermanentError
from backend.shared import observability, tenant

KIND = "tp_tenant_restore_drill"
MAX_ATTEMPTS = 1
PAYLOAD_KEYS = frozenset({"tenant_id", "tenant_slug", "backup_id"})
TABLES = tenant_backup.TABLES

LATEST_BACKUP_SQL = """
    SELECT id::text AS id, result::text AS result FROM tripplanner.job
     WHERE kind = $1 AND status = 'succeeded'
       AND (result ->> 'tenant_id' = $2
            OR result -> 'backups' @> jsonb_build_array(jsonb_build_object('tenant_id', $2::text)))
     ORDER BY finished_at DESC, id LIMIT 1
"""
BACKUP_JOB_SQL = """
    SELECT id::text AS id, result::text AS result FROM tripplanner.job
     WHERE kind = $1 AND status = 'succeeded' AND id = $2::uuid
"""
# The temp tables are this drill's own (pg_temp.rd_<table>); the live statements are spelled out per table.
CREATE_SQL = "CREATE TEMP TABLE rd_{table} (LIKE tripplanner.{table}) ON COMMIT DROP"
LOAD_SQL = "INSERT INTO pg_temp.rd_{table} SELECT * FROM jsonb_populate_recordset(NULL::pg_temp.rd_{table}, $1::jsonb)"
DIGEST_SQL = """
    SELECT count(*) AS restored,
           encode(sha256(convert_to(coalesce(string_agg(to_jsonb(r)::text || E'\\n', '' ORDER BY r.id), ''), 'UTF8')),
                  'hex') AS digest
      FROM pg_temp.rd_{table} r
"""
LIVE_SQL = {
    "sessions": """
        SELECT (SELECT count(*) FROM tripplanner.sessions l WHERE l.tenant_id = $1::uuid) AS live_rows,
               (SELECT count(*) FROM pg_temp.rd_sessions r JOIN tripplanner.sessions l ON l.id = r.id) AS live_found,
               (SELECT count(*) FROM pg_temp.rd_sessions r JOIN tripplanner.sessions l
                    ON l.id = r.id AND to_jsonb(l) = to_jsonb(r)) AS identical""",
    "trip_drafts": """
        SELECT (SELECT count(*) FROM tripplanner.trip_drafts l WHERE l.tenant_id = $1::uuid) AS live_rows,
               (SELECT count(*) FROM pg_temp.rd_trip_drafts r JOIN tripplanner.trip_drafts l ON l.id = r.id)
                   AS live_found,
               (SELECT count(*) FROM pg_temp.rd_trip_drafts r JOIN tripplanner.trip_drafts l
                    ON l.id = r.id AND to_jsonb(l) = to_jsonb(r)) AS identical""",
    "trip_events": """
        SELECT (SELECT count(*) FROM tripplanner.trip_events l WHERE l.tenant_id = $1::uuid) AS live_rows,
               (SELECT count(*) FROM pg_temp.rd_trip_events r JOIN tripplanner.trip_events l ON l.id = r.id)
                   AS live_found,
               (SELECT count(*) FROM pg_temp.rd_trip_events r JOIN tripplanner.trip_events l
                    ON l.id = r.id AND to_jsonb(l) = to_jsonb(r)) AS identical""",
    "outbox": """
        SELECT (SELECT count(*) FROM tripplanner.outbox l WHERE l.tenant_id = $1::uuid) AS live_rows,
               (SELECT count(*) FROM pg_temp.rd_outbox r JOIN tripplanner.outbox l ON l.id = r.id) AS live_found,
               (SELECT count(*) FROM pg_temp.rd_outbox r JOIN tripplanner.outbox l
                    ON l.id = r.id AND to_jsonb(l) = to_jsonb(r)) AS identical""",
}


class _Rollback(Exception):
    """Ends the drill transaction: the drill never commits."""


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PermanentError("the payload must be an object")
    unknown = set(payload) - PAYLOAD_KEYS
    if unknown:
        raise PermanentError(f"unknown payload keys (allowed: {sorted(PAYLOAD_KEYS)})")
    if payload.get("backup_id") is not None:
        try:
            tenant.canonical(payload["backup_id"])
        except tenant.InvalidTenant:
            raise PermanentError("backup_id must be a job id (uuid)") from None
    return payload


def entry_of(result: dict[str, Any], tenant_id: str) -> Optional[dict[str, Any]]:
    """The backup entry of one tenant in a backup job's result: the result itself (an operator backup) or its item in
    ``backups`` (a daily pilot backup). None when the job did not back up that tenant."""
    if result.get("tenant_id") == tenant_id:
        return result
    for item in result.get("backups") or []:
        if isinstance(item, dict) and item.get("tenant_id") == tenant_id:
            return item
    return None


async def _backup_job(conn: Any, tenant_id: str, backup_id: Optional[str]) -> tuple[str, Optional[dict[str, Any]]]:
    if backup_id:
        row = await conn.fetchrow(BACKUP_JOB_SQL, tenant_backup.KIND, tenant.canonical(backup_id))
        if row is None:
            return tenant.canonical(backup_id), None     # the job row may be gone; the files still say enough
        entry = entry_of(json.loads(row["result"] or "{}"), tenant_id)
        if entry is None:
            raise PermanentError("that backup belongs to another tenant")
        return row["id"], entry
    row = await conn.fetchrow(LATEST_BACKUP_SQL, tenant_backup.KIND, tenant_id)
    if row is None:
        raise PermanentError("no succeeded tp_tenant_backup job for this tenant: pass backup_id")
    return row["id"], entry_of(json.loads(row["result"] or "{}"), tenant_id)


Lines = dict[str, list[str]]


async def check_files(store: tenant_store.ObjectStore, tenant_id: str, backup_id: str,
                      job_result: Optional[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], Lines]:
    """(checks, manifest, lines per table). Raises PermanentError when the backup is gone."""
    prefix = tenant_store.prefix(tenant_id, backup_id)
    try:
        sums_raw = await store.get_bytes(prefix + tenant_backup.SUMS)
        manifest_raw = await store.get_bytes(prefix + tenant_backup.MANIFEST)
    except tenant_store.ObjectMissing:
        raise PermanentError("the backup files are gone (the 7-day bucket rule) or were never written") from None
    except tenant_store.AccessDenied as e:
        raise PermanentError(tenant_store.denied(e)) from None
    sums = tenant_backup.parse_sums(sums_raw)
    manifest = json.loads(manifest_raw)
    checks = {
        "sha256sums_matches_job": (None if not job_result
                                   else tenant_backup.sha256(sums_raw) == job_result.get("sha256sums_sha256")),
        "manifest_matches_sums": sums.get(tenant_backup.MANIFEST) == tenant_backup.sha256(manifest_raw),
        "manifest_tenant_matches": manifest.get("tenant_id") == tenant_id and manifest.get("backup_id") == backup_id,
        "files": {},
    }
    lines: dict[str, list[str]] = {}
    for table in TABLES:
        key = tenant_backup.table_key(table)
        entry = (manifest.get("tables") or {}).get(f"tripplanner.{table}") or {}
        try:
            data = await store.get_bytes(prefix + key)
        except tenant_store.ObjectMissing:
            checks["files"][table] = False
            lines[table] = []
            continue
        except tenant_store.AccessDenied as e:
            raise PermanentError(tenant_store.denied(e)) from None
        digest = tenant_backup.sha256(data)
        checks["files"][table] = digest == sums.get(key) == entry.get("sha256")
        lines[table] = data.decode("utf-8").splitlines()
    return checks, manifest, lines


async def load_and_compare(conn: Any, tenant_id: str, manifest: dict[str, Any],
                           lines: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """Load every table into pg_temp and compare, in a transaction that is always rolled back. ``conn`` is
    system-scoped (the live comparisons read FORCE row-level security tables)."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with conn.transaction():
            for table in TABLES:
                await conn.execute(CREATE_SQL.format(table=table))
                await conn.execute(LOAD_SQL.format(table=table), "[" + ",".join(lines[table]) + "]")
                restored = await conn.fetchrow(DIGEST_SQL.format(table=table))
                live = await conn.fetchrow(LIVE_SQL[table], tenant_id)
                entry = (manifest.get("tables") or {}).get(f"tripplanner.{table}") or {}
                out[table] = {"manifest_rows": entry.get("rows"), "restored_rows": restored["restored"],
                              "digest_ok": restored["digest"] == entry.get("sha256"),
                              "live_rows": live["live_rows"], "live_found": live["live_found"],
                              "identical": live["identical"]}
            raise _Rollback
    except _Rollback:
        pass
    return out


def passed(checks: dict[str, Any], tables: dict[str, dict[str, Any]]) -> bool:
    files_ok = (checks["sha256sums_matches_job"] is not False and checks["manifest_matches_sums"]
                and checks["manifest_tenant_matches"] and all(checks["files"].values()))
    return bool(files_ok and tables and all(t["digest_ok"] and t["restored_rows"] == t["manifest_rows"]
                                            for t in tables.values()))


async def run(pool: Any, job: Job, *, store: Optional[tenant_store.ObjectStore] = None) -> dict[str, Any]:
    payload = validate_payload(job.payload)
    try:
        store = store or tenant_store.S3Store()
    except tenant_store.BucketNotConfigured as e:
        raise PermanentError(str(e)) from None
    async with pool.acquire() as conn, tenant.system_scope(conn, op=KIND):
        tenant_id = await tenant_target.resolve(conn, payload)
        backup_id, job_result = await _backup_job(conn, tenant_id, payload.get("backup_id"))
    checks, manifest, lines = await check_files(store, tenant_id, backup_id, job_result)
    async with pool.acquire() as conn, tenant.system_scope(conn, op=KIND):
        tables = await load_and_compare(conn, tenant_id, manifest, lines)
    ok = passed(checks, tables)
    observability.emit("tenant_restore_drill", tenant_id=tenant_id, backup_id=backup_id, passed=ok)
    return {"mode": "restore_drill", "tenant_id": tenant_id, "backup_id": backup_id, "passed": ok,
            "checks": checks, "tables": tables, "attention": not ok}
