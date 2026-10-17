"""tp-013 replay (X1 plan S7, AA-852): the owner transfer on a stand-in of the live shape.

Runs only with ``pytest migrations/tests --pg-dsn postgresql://postgres@127.0.0.1:55432/postgres`` (a
SUPERUSER DSN of a throwaway LOCAL Postgres; other hosts are refused, so it can never touch RDS). The
superuser is used only for the bootstrap; the migrations run as a NOSUPERUSER stand-in of the RDS master:

  * roles (cluster-wide, created if missing): ``aa_cis_admin`` LOGIN NOSUPERUSER CREATEROLE CREATEDB,
    ``tripplanner`` and ``booking`` LOGIN, ``tp_owner`` NOLOGIN with ``GRANT tp_owner TO aa_cis_admin``
    (Infra rl-001); a fresh database owned by ``aa_cis_admin``;
  * as ``aa_cis_admin``: schema ``shared`` + a stand-in ``shared.schema_versions``, schema ``tripplanner``
    with the live default privileges ``FOR ROLE aa_cis_admin`` (tables DML, sequences USAGE/SELECT →
    tripplanner; created out of band on Dev), then tp-001…012 replayed as files (002 before 001);
  * tp-013 first WITHOUT the CIS 243 grants (guard 1 must refuse, nothing changes), then with them.

pgvector: created by the superuser when the server has it (as on RDS). Without it, the replay copy of
tp-001 drops ``CREATE EXTENSION vector`` and the HNSW index and stores the embedding as ``real[]``:
ownership does not depend on the column type, and the substitutions are asserted so a changed tp-001
fails loudly instead of being replayed half-rewritten.

After the apply the test checks owners, grants, default privileges and behaviour as each role, re-runs
tp-013 (no-op), and runs a later file the way DB Migrate will run tp-014 (``SET LOCAL ROLE tp_owner``,
XD8): its objects belong to ``tp_owner`` and ``tripplanner`` can use them through the default privileges.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

MIGRATIONS = Path(__file__).resolve().parent.parent
TP013 = MIGRATIONS / "013_x1_tp_owner.sql"
TABLES = ("customers", "itinerary_components", "llm_spend_daily", "narration_cache", "outbox", "sessions",
          "tour_day", "tour_graph_build", "tour_graph_edge", "tour_graph_node", "tour_junction", "tour_leg",
          "tour_stop", "trip_drafts", "trip_events")

BOOTSTRAP_ROLES = """
DO $$ BEGIN
  IF to_regrole('aa_cis_admin') IS NULL THEN CREATE ROLE aa_cis_admin LOGIN NOSUPERUSER CREATEROLE CREATEDB; END IF;
  IF to_regrole('tripplanner') IS NULL THEN CREATE ROLE tripplanner LOGIN; END IF;
  IF to_regrole('booking') IS NULL THEN CREATE ROLE booking LOGIN; END IF;
  IF to_regrole('tp_owner') IS NULL THEN CREATE ROLE tp_owner NOLOGIN; END IF;
END $$;
GRANT tp_owner TO aa_cis_admin;
"""

# As aa_cis_admin, before tp-001: the CIS-owned pieces and what Dev got out of band (schema USAGE for the
# runtime, default privileges FOR ROLE aa_cis_admin; schema reference of 16/10).
LIVE_STANDINS = """
CREATE SCHEMA shared;
CREATE TABLE shared.schema_versions (
    version varchar(32) PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now(), description text);
CREATE SCHEMA tripplanner;
GRANT USAGE ON SCHEMA shared TO tripplanner;
GRANT USAGE ON SCHEMA tripplanner TO tripplanner;
ALTER DEFAULT PRIVILEGES FOR ROLE aa_cis_admin IN SCHEMA tripplanner
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tripplanner;
ALTER DEFAULT PRIVILEGES FOR ROLE aa_cis_admin IN SCHEMA tripplanner
    GRANT USAGE, SELECT ON SEQUENCES TO tripplanner;
"""

CIS_243_GRANTS = """
GRANT USAGE ON SCHEMA shared TO tp_owner;
GRANT SELECT, INSERT ON shared.schema_versions TO tp_owner;
"""

# What DB Migrate does with the first tripplanner/ file after tp-013 (XD8): SET LOCAL ROLE <live owner>.
LATER_FILE = """
BEGIN;
SET LOCAL ROLE tp_owner;
CREATE TABLE IF NOT EXISTS tripplanner.x1_after_transfer (id bigserial PRIMARY KEY, note text NOT NULL);
INSERT INTO shared.schema_versions (version, applied_at, description)
VALUES ('tp-999', now(), 'test: a file after tp-013 run as tp_owner') ON CONFLICT (version) DO NOTHING;
COMMIT;
"""


def run(coro):
    return asyncio.run(coro)


def _replay_text(path: Path, vector: bool) -> str:
    sql = path.read_text(encoding="utf-8")
    if vector or not path.name.startswith("001_"):
        return sql
    subs = (
        (r"CREATE EXTENSION IF NOT EXISTS vector;", ""),
        (r"VECTOR\(1536\)", "real[]"),
        (r"CREATE INDEX itinerary_components_embedding_idx\s+ON tripplanner\.itinerary_components\s+"
         r"USING hnsw \(embedding vector_cosine_ops\);", ""),
    )
    for pattern, repl in subs:
        sql, n = re.subn(pattern, repl, sql)
        assert n == 1, f"tp-001 changed: {pattern!r} matched {n} times (update the pgvector stand-in)"
    return sql


def _replay_order() -> list:
    """tp-001…012 in the order they were applied: 002 (shared.destinations) before 001 (its FK), as
    tp-001's header says."""
    files = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if int(f.name[:3]) < 13)
    first = [f for f in files if f.name.startswith("002_")]
    return first + [f for f in files if f not in first]


def _dsn(base: str, *, user: str = None, password: str = None, database: str = None) -> str:
    p = urlparse(base)
    netloc = p.netloc
    if user is not None:
        host = netloc.rsplit("@", 1)[-1]
        netloc = f"{user}:{password}@{host}" if password else f"{user}@{host}"
    path = f"/{database}" if database is not None else p.path
    return urlunparse(p._replace(netloc=netloc, path=path))


@pytest.fixture(scope="module")
def pg_dsn(request):
    dsn = request.config.getoption("--pg-dsn")
    if not dsn:
        pytest.skip("integration: pass --pg-dsn with a superuser DSN of a throwaway local Postgres")
    host = urlparse(dsn).hostname or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip(f"integration tests only run against a local Postgres, not {host}")
    return dsn


@pytest.fixture(scope="module")
def db(pg_dsn):
    """Builds the stand-in, applies tp-013 (first without 243, then with it); yields the DSNs."""
    name = f"tp_x1s7_{uuid.uuid4().hex[:8]}"
    password = secrets.token_urlsafe(16)

    async def setup() -> dict:
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(BOOTSTRAP_ROLES)
            await su.execute(f"ALTER ROLE aa_cis_admin PASSWORD '{password}'")
            await su.execute(f'CREATE DATABASE "{name}" OWNER aa_cis_admin')
        finally:
            await su.close()
        su_db = await asyncpg.connect(_dsn(pg_dsn, database=name))
        try:
            vector = await su_db.fetchval(
                "SELECT count(*) > 0 FROM pg_available_extensions WHERE name = 'vector'")
            if vector:
                await su_db.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            await su_db.close()
        admin_dsn = _dsn(pg_dsn, user="aa_cis_admin", password=password, database=name)
        admin = await asyncpg.connect(admin_dsn)
        try:
            assert not await admin.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            await admin.execute(LIVE_STANDINS)
            for f in _replay_order():
                await admin.execute(_replay_text(f, vector))
            refused = None
            try:
                await admin.execute(TP013.read_text(encoding="utf-8"))
            except asyncpg.PostgresError as e:
                refused = str(e)
                await admin.execute("ROLLBACK")      # the file's BEGIN left an aborted transaction open
            owner_after_refusal = await admin.fetchval(
                "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'tripplanner'")
            registered_after_refusal = await admin.fetchval(
                "SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-013'")
            await admin.execute(CIS_243_GRANTS)
            return {"vector": vector, "refused": refused, "owner_after_refusal": owner_after_refusal,
                    "registered_after_refusal": registered_after_refusal, "admin_dsn": admin_dsn}
        finally:
            await admin.close()

    async def seed_and_apply(ctx: dict) -> None:
        su = await asyncpg.connect(_dsn(pg_dsn, database=name))
        try:
            await _seed_as_tripplanner(su)
        finally:
            await su.close()
        admin = await asyncpg.connect(ctx["admin_dsn"])
        try:
            await admin.execute(TP013.read_text(encoding="utf-8"))
        finally:
            await admin.close()

    async def teardown() -> None:
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await su.close()

    try:
        ctx = run(setup())
        run(seed_and_apply(ctx))
        ctx["su_dsn"] = _dsn(pg_dsn, database=name)
        yield ctx
    finally:
        run(teardown())


async def _seed_as_tripplanner(su) -> None:
    """One customer, session, draft, event and outbox row written as the runtime role."""
    async with su.transaction():
        await su.execute("SET LOCAL ROLE tripplanner")
        cid = await su.fetchval(
            "INSERT INTO tripplanner.customers (name, email) VALUES ('Before', 'before@example.test') RETURNING id")
        sid = await su.fetchval("INSERT INTO tripplanner.sessions (customer_id) VALUES ($1) RETURNING id", cid)
        tid = await su.fetchval(
            "INSERT INTO tripplanner.trip_drafts (session_id, status, itinerary) VALUES ($1, 'draft', '{}') "
            "RETURNING id", sid)
        await su.execute("INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload) "
                         "VALUES ($1, $2, 'sent', '{}')", tid, sid)
        await su.execute("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload) "
                         "VALUES ('inquiry.submitted', 1, $1, $2, '{}')", f"{tid}:before", tid)


def test_guard_refuses_without_the_cis_243_grants(db):
    assert db["refused"] is not None and "apply CIS 243 first" in db["refused"]
    assert db["owner_after_refusal"] == "aa_cis_admin"
    assert db["registered_after_refusal"] == 0


def test_every_object_is_owned_by_tp_owner(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            rows = await su.fetch("""
                SELECT 'schema' AS kind, nspname::text AS name, pg_get_userbyid(nspowner)::text AS owner
                  FROM pg_namespace WHERE nspname = 'tripplanner'
                UNION ALL
                SELECT 'relation ' || relkind::text, oid::regclass::text, pg_get_userbyid(relowner)::text
                  FROM pg_class WHERE relnamespace = 'tripplanner'::regnamespace
                UNION ALL
                SELECT 'type', oid::regtype::text, pg_get_userbyid(typowner)::text
                  FROM pg_type WHERE typnamespace = 'tripplanner'::regnamespace""")
            registered = await su.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-013'")
        finally:
            await su.close()
        return rows, registered

    rows, registered = run(check())
    assert registered == 1
    assert {r["owner"] for r in rows} == {"tp_owner"}, [dict(r) for r in rows if r["owner"] != "tp_owner"]
    names = {r["name"] for r in rows}
    for t in TABLES:
        assert f"tripplanner.{t}" in names
    assert {"tripplanner.v_inquiry_outbox", "tripplanner.trip_events_id_seq", "tripplanner.outbox_id_seq"} <= names


def test_default_privileges_are_the_tp_owner_rows_only(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            return await su.fetch("""
                SELECT pg_get_userbyid(d.defaclrole)::text AS role, d.defaclobjtype::text AS objtype,
                       pg_get_userbyid(x.grantee)::text AS grantee, x.privilege_type AS privilege
                  FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) x
                 WHERE d.defaclnamespace = 'tripplanner'::regnamespace""")
        finally:
            await su.close()

    rows = {(r["role"], r["objtype"], r["grantee"], r["privilege"]) for r in run(check())}
    assert rows == {("tp_owner", "r", "tripplanner", p) for p in ("SELECT", "INSERT", "UPDATE", "DELETE")} | {
        ("tp_owner", "S", "tripplanner", p) for p in ("USAGE", "SELECT")}


def test_tripplanner_runtime_keeps_its_dml(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                sid = await su.fetchval("INSERT INTO tripplanner.sessions DEFAULT VALUES RETURNING id")
                tid = await su.fetchval("INSERT INTO tripplanner.trip_drafts (session_id, status, itinerary) "
                                        "VALUES ($1, 'draft', '{}') RETURNING id", sid)
                ev = await su.fetchval("INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload) "
                                       "VALUES ($1, $2, 'add_day', '{}') RETURNING id", tid, sid)
                ob = await su.fetchval("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, "
                                       "aggregate_id, payload) VALUES ('inquiry.submitted', 1, $1, $2, '{}') "
                                       "RETURNING id", f"{tid}:after", tid)
                await su.execute("UPDATE tripplanner.trip_drafts SET status = 'sent' WHERE id = $1", tid)
                await su.execute("INSERT INTO tripplanner.llm_spend_daily (day, stage) VALUES (current_date, 'tp_x')")
                await su.execute("DELETE FROM tripplanner.llm_spend_daily WHERE stage = 'tp_x'")
                seen = await su.fetchval("SELECT count(*) FROM tripplanner.v_inquiry_outbox")
                await su.execute("SELECT count(*) FROM tripplanner.tour_stop")
                await su.execute("SELECT count(*) FROM tripplanner.itinerary_components")
                raise _Rollback((ev, ob, seen))
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    ev, ob, seen = run(check())
    assert ev > 1 and ob > 1          # the BIGSERIAL sequences moved with their tables and stay usable
    assert seen == 2


def test_booking_reads_the_view_but_not_the_table(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE booking")
                rows = await su.fetchval("SELECT count(*) FROM tripplanner.v_inquiry_outbox")
            denied = []
            for sql in ("SELECT count(*) FROM tripplanner.outbox", "SELECT count(*) FROM tripplanner.sessions",
                        "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload) "
                        "VALUES ('inquiry.submitted', 1, 'x', gen_random_uuid(), '{}')"):
                try:
                    async with su.transaction():
                        await su.execute("SET LOCAL ROLE booking")
                        await su.execute(sql)
                except asyncpg.InsufficientPrivilegeError:
                    denied.append(sql)
            return rows, denied
        finally:
            await su.close()

    rows, denied = run(check())
    assert rows == 1                  # the seeded row, read through the view now owned by tp_owner
    assert len(denied) == 3


def test_rerun_as_the_master_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            await admin.execute(TP013.read_text(encoding="utf-8"))
            return await admin.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-013'")
        finally:
            await admin.close()

    assert run(check()) == 1


def test_master_still_reads_through_membership(db):
    """Documents the interim state: the CIS runtime (still the master until X1 S18) reads frozen
    tripplanner tables (photo_sync) as a member of tp_owner (rl-001)."""
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            return await admin.fetchval("SELECT count(*) FROM tripplanner.tour_stop")
        finally:
            await admin.close()

    assert run(check()) == 0


def test_a_later_file_runs_as_tp_owner_and_is_covered_by_default_privileges(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            await admin.execute(LATER_FILE)
        finally:
            await admin.close()
        su = await asyncpg.connect(db["su_dsn"])
        try:
            owners = await su.fetch("""
                SELECT oid::regclass::text AS name, pg_get_userbyid(relowner)::text AS owner FROM pg_class
                 WHERE relnamespace = 'tripplanner'::regnamespace AND relname LIKE 'x1_after_transfer%'""")
            registered_by = await su.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-999'")
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                new_id = await su.fetchval(
                    "INSERT INTO tripplanner.x1_after_transfer (note) VALUES ('ok') RETURNING id")
                await su.execute("UPDATE tripplanner.x1_after_transfer SET note = 'ok2' WHERE id = $1", new_id)
                await su.execute("DELETE FROM tripplanner.x1_after_transfer WHERE id = $1", new_id)
            return owners, registered_by, new_id
        finally:
            await su.close()

    owners, registered_by, new_id = run(check())
    assert {r["owner"] for r in owners} == {"tp_owner"} and len(owners) >= 3   # table, sequence, pkey
    assert registered_by == 1
    assert new_id == 1


class _Rollback(Exception):
    def __init__(self, value):
        super().__init__("rollback")
        self.value = value
