"""AA-1093 part 3 — job kind ``tp_tenant_backup``: one tenant's TripPlanner rows to the gold bucket (plan decision D11;
the restore half is :mod:`backend.jobs.tenant_restore_drill`).

TripPlanner had only whole-instance PITR, which restores all four apps at once (S278 system audit, M27). This copies
the CRM model (``crm_pilot_backup`` + its restore drill) for the four tenant tables: ``tripplanner.sessions``,
``trip_drafts``, ``trip_events``, ``outbox``.

Payload: ``{"tenant_id": "<uuid>"}`` or ``{"tenant_slug": "<slug>"}`` (resolved through
``catalog.v_tenant_tripplanner_v1``). The backup id is the job id; a retry writes the same keys again.

Objects (prefix ``exports/tenants/<tenant>/tripplanner/<backup id>/``, :mod:`backend.jobs.tenant_store`):

* ``tables/tripplanner.<table>.jsonl``: one ``to_jsonb(row)`` per line in primary-key order, each line ended by a
  newline (Postgres writes the JSON, so every type comes back through ``jsonb_populate_recordset``);
* ``manifest.json``: format ``tp-tenant-backup/1``, tenant, backup id, the highest registered ``tp-`` migration when
  readable, per table rows, bytes, sha256 and columns, the restore order, and what is left out and why;
* ``SHA256SUMS`` (``sha256sum`` format) over the table files and the manifest, written last; its own digest is in the
  job result, which the drill checks first.

All four tables are read in one repeatable-read, read-only transaction under the jobs Lambda's system scope (the
tables are FORCE row-level security, tp-028), so parents and children agree. Nothing is written to the database but
the job result (ids, counts, digests; DM-6.3).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from backend.jobs import tenant_store, tenant_target
from backend.jobs.queue import Job
from backend.jobs.runner import PermanentError
from backend.shared import observability, tenant

KIND = "tp_tenant_backup"
MAX_ATTEMPTS = 2          # the same job id writes the same keys again: a retry is safe
FORMAT = "tp-tenant-backup/1"
TABLES = ("sessions", "trip_drafts", "trip_events", "outbox")     # restore order: parents first (no FK links them)
PAYLOAD_KEYS = frozenset({"tenant_id", "tenant_slug"})
EXCLUDED = {
    "tripplanner.ai_spend_month": "cost history per stage and month; an erasure merges the tenant rows into the app "
                                  "scope, a restore does not bring spend back",
    "tripplanner.customers": "frozen global traveller records (no tenant column); outside the tenant scope",
    "tripplanner.traveller_accounts": "global traveller sign-in records (no tenant column); outside the tenant scope",
}
SUMS = "SHA256SUMS"
MANIFEST = "manifest.json"

# One statement per table, spelled out (never a name from a payload; backend/tests/test_aa1038_force_rls.py sees each
# tenant table this module reads and checks the system scope around it).
ROWS_SQL = {
    "sessions": "SELECT to_jsonb(t)::text AS j FROM tripplanner.sessions t WHERE t.tenant_id = $1::uuid ORDER BY t.id",
    "trip_drafts": "SELECT to_jsonb(t)::text AS j FROM tripplanner.trip_drafts t WHERE t.tenant_id = $1::uuid "
                   "ORDER BY t.id",
    "trip_events": "SELECT to_jsonb(t)::text AS j FROM tripplanner.trip_events t WHERE t.tenant_id = $1::uuid "
                   "ORDER BY t.id",
    "outbox": "SELECT to_jsonb(t)::text AS j FROM tripplanner.outbox t WHERE t.tenant_id = $1::uuid ORDER BY t.id",
}
COLUMNS_SQL = """
    SELECT a.attname::text AS name FROM pg_attribute a
     WHERE a.attrelid = ('tripplanner.' || $1)::regclass AND a.attnum > 0 AND NOT a.attisdropped
     ORDER BY a.attnum
"""
# Two statements: Postgres checks the table privilege of a subquery even inside a CASE that never runs it.
SCHEMA_VERSION_READABLE_SQL = "SELECT has_table_privilege('shared.schema_versions', 'SELECT') AS ok"
SCHEMA_VERSION_SQL = "SELECT max(version) AS v FROM shared.schema_versions WHERE version LIKE 'tp-%'"


def table_key(table: str) -> str:
    return f"tables/tripplanner.{table}.jsonl"


def jsonl(lines: list[str]) -> bytes:
    """The file bytes of one table: each line ended by a newline; no rows = an empty file."""
    return "".join(f"{line}\n" for line in lines).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sums_file(digests: dict[str, str]) -> bytes:
    """``sha256sum`` format, in key order."""
    return "".join(f"{digests[k]}  {k}\n" for k in sorted(digests)).encode("utf-8")


def parse_sums(data: bytes) -> dict[str, str]:
    out = {}
    for line in data.decode("utf-8").splitlines():
        if line.strip():
            digest, _, key = line.partition("  ")
            out[key] = digest
    return out


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PermanentError("the payload must be an object")
    unknown = set(payload) - PAYLOAD_KEYS
    if unknown:
        raise PermanentError(f"unknown payload keys (allowed: {sorted(PAYLOAD_KEYS)})")
    return payload


async def read_tenant(conn: Any, tenant_id: str) -> tuple[dict[str, list[str]], dict[str, list[str]], Optional[str]]:
    """(lines per table, columns per table, schema version) of one tenant, one snapshot. ``conn`` is system-scoped."""
    lines: dict[str, list[str]] = {}
    cols: dict[str, list[str]] = {}
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        version = (await conn.fetchval(SCHEMA_VERSION_SQL)
                   if await conn.fetchval(SCHEMA_VERSION_READABLE_SQL) else None)
        for table in TABLES:
            lines[table] = [r["j"] for r in await conn.fetch(ROWS_SQL[table], tenant_id)]
            cols[table] = [r["name"] for r in await conn.fetch(COLUMNS_SQL, table)]
    return lines, cols, version


def build(tenant_id: str, backup_id: str, lines: dict[str, list[str]], cols: dict[str, list[str]],
          version: Optional[str], created_at: str) -> dict[str, bytes]:
    """Every object of one backup, by key relative to its prefix (pure)."""
    files = {table_key(t): jsonl(lines[t]) for t in TABLES}
    manifest = {
        "format": FORMAT, "tenant_id": tenant_id, "backup_id": backup_id, "created_at": created_at,
        "schema_version": version,
        "tables": {f"tripplanner.{t}": {"file": table_key(t), "rows": len(lines[t]),
                                        "bytes": len(files[table_key(t)]), "sha256": sha256(files[table_key(t)]),
                                        "columns": cols[t], "key": "id"} for t in TABLES},
        "restore_order": [f"tripplanner.{t}" for t in TABLES],
        "excluded": EXCLUDED,
    }
    files[MANIFEST] = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    files[SUMS] = sums_file({k: sha256(v) for k, v in files.items()})
    return files


async def run(pool: Any, job: Job, *, store: Optional[tenant_store.ObjectStore] = None,
              now: Optional[datetime] = None) -> dict[str, Any]:
    payload = validate_payload(job.payload)
    try:
        store = store or tenant_store.S3Store()
    except tenant_store.BucketNotConfigured as e:
        raise PermanentError(str(e)) from None
    async with pool.acquire() as conn, tenant.system_scope(conn, op=KIND):
        tenant_id = await tenant_target.resolve(conn, payload)
        lines, cols, version = await read_tenant(conn, tenant_id)
    created = (now or datetime.now(timezone.utc)).isoformat()
    files = build(tenant_id, job.id, lines, cols, version, created)
    prefix = tenant_store.prefix(tenant_id, job.id)
    for key in sorted(k for k in files if k != SUMS):
        await store.put_bytes(prefix + key, files[key],
                              "application/json" if key == MANIFEST else "application/x-ndjson")
    await store.put_bytes(prefix + SUMS, files[SUMS], "text/plain")
    rows = {f"tripplanner.{t}": len(lines[t]) for t in TABLES}
    observability.emit("tenant_backup", tenant_id=tenant_id, backup_id=job.id, rows=sum(rows.values()))
    return {"mode": "backup", "tenant_id": tenant_id, "backup_id": job.id, "prefix": prefix, "rows": rows,
            "schema_version": version, "sha256sums_sha256": sha256(files[SUMS])}
