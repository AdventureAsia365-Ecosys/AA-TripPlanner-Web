"""tp-023 replay (AA-1015): tripplanner.ai_rate_counter, applied the way DB Migrate applies it (as tp_owner, after
tp-013 … tp-022).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the stand-in of test_tp013_pg.py:
tp-001…012 replayed as files, the CIS 243 grants, tp-013 as the master, tp-014 … tp-022 with ``SET ROLE tp_owner``,
then tp-023 the same way.

Checks: the expand header (root ADR 0017 d3) and the Infra-safe shape (one BEGIN … COMMIT, no DO block); the columns,
key, index, owner, comment and registration; the runtime role runs the limiter's upsert and the retention delete and
cannot truncate; cis_app sees nothing; a re-run as tp_owner is a no-op.
"""
from __future__ import annotations

import secrets
import uuid

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import (  # helpers of the tp-013 replay; same stand-in
    BOOTSTRAP_ROLES,
    CIS_243_GRANTS,
    LIVE_STANDINS,
    MIGRATIONS,
    TP013,
    _dsn,
    _replay_order,
    _replay_text,
    run,
)
from test_tp015_pg import CIS_READER

asyncpg = pytest.importorskip("asyncpg")

BEFORE_TP023 = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if 13 < int(f.name[:3]) < 23)
TP023 = MIGRATIONS / "023_ai_rate_counter.sql"
TABLE = "tripplanner.ai_rate_counter"

# The limiter's upsert, exactly as the code runs it (backend/shared/ratelimit.py), and the retention delete.
UPSERT = (MIGRATIONS.parent / "backend" / "shared" / "ratelimit.py").read_text(encoding="utf-8").split(
    'UPSERT_SQL = """', 1)[1].split('"""', 1)[0]


async def _as_owner(admin, path) -> None:
    await admin.execute("SET ROLE tp_owner")
    try:
        await admin.execute(path.read_text(encoding="utf-8"))
    finally:
        await admin.execute("RESET ROLE")


class _Rollback(Exception):
    def __init__(self, value):
        super().__init__("rollback")
        self.value = value


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    name = f"tp_ratecnt_{uuid.uuid4().hex[:8]}"
    password = secrets.token_urlsafe(16)

    async def setup() -> dict:
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(BOOTSTRAP_ROLES)
            await su.execute(CIS_READER)
            await su.execute(f"ALTER ROLE aa_cis_admin PASSWORD '{password}'")
            await su.execute(f'CREATE DATABASE "{name}" OWNER aa_cis_admin')
        finally:
            await su.close()
        su_db = await asyncpg.connect(_dsn(pg_dsn, database=name))
        try:
            vector = await su_db.fetchval("SELECT count(*) > 0 FROM pg_available_extensions WHERE name = 'vector'")
            if vector:
                await su_db.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            await su_db.close()
        admin_dsn = _dsn(pg_dsn, user="aa_cis_admin", password=password, database=name)
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(LIVE_STANDINS)
            for f in _replay_order():
                await admin.execute(_replay_text(f, vector))
            await admin.execute(CIS_243_GRANTS)
            await admin.execute(TP013.read_text(encoding="utf-8"))
            for f in BEFORE_TP023:
                await _as_owner(admin, f)
            await _as_owner(admin, TP023)
        finally:
            await admin.close()
        return {"admin_dsn": admin_dsn, "su_dsn": _dsn(pg_dsn, database=name)}

    async def teardown() -> None:
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await su.close()

    try:
        yield run(setup())
    finally:
        run(teardown())


def test_files_before_tp023_are_tp014_to_tp022():
    assert [f.name[:3] for f in BEFORE_TP023] == ["014", "015", "016", "017", "018", "019", "020", "021", "022"]


def test_file_is_an_expand_file_in_one_transaction_block():
    """Root ADR 0017 d3: the expand header, one BEGIN … COMMIT, no DO block (opaque to the Infra expand lint)."""
    text = TP023.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "-- migrate: expand"
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("--"))
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "DO $$" not in code and "REVOKE" not in code


def test_table_shape_owner_comment_and_registration(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            registered = await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-023'")
            cols = await su.fetch(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE table_schema = 'tripplanner' AND table_name = 'ai_rate_counter' ORDER BY ordinal_position")
            pk = await su.fetchval(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = 'ai_rate_counter_pkey'")
            index = await su.fetchval(
                "SELECT indexdef AS d FROM pg_indexes WHERE indexname = 'ai_rate_counter_window_idx'")
            owner = await su.fetchval(f"SELECT pg_get_userbyid(relowner) AS o FROM pg_class WHERE oid = '{TABLE}'::regclass")
            comment = await su.fetchval(f"SELECT obj_description('{TABLE}'::regclass, 'pg_class') AS c")
            return registered, [tuple(r) for r in cols], pk, index, owner, comment
        finally:
            await su.close()

    registered, cols, pk, index, owner, comment = run(check())
    assert registered == 1
    assert cols == [("key_hash", "text", "NO"), ("window_start", "timestamp with time zone", "NO"),
                    ("count", "integer", "NO")]
    assert pk == "PRIMARY KEY (key_hash, window_start)"
    assert "(window_start)" in index
    assert owner == "tp_owner"
    assert comment.startswith("tp-023 (AA-1015)") and "2 days" in comment


def test_runtime_role_counts_and_purges_but_cannot_truncate(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                counts = [(await su.fetchrow(UPSERT, "k1", 60))["count"] for _ in range(3)]
                retry = (await su.fetchrow(UPSERT, "k3", 3600))["retry_after_s"]
                other = (await su.fetchrow(UPSERT, "k2", 60))["count"]
                await su.execute(
                    "INSERT INTO tripplanner.ai_rate_counter (key_hash, window_start, count)"
                    " VALUES ('old', now() - interval '3 days', 4)")
                purged = await su.execute(
                    "DELETE FROM tripplanner.ai_rate_counter WHERE window_start < now() - interval '2 days'")
                left = await su.fetchval("SELECT count(*) AS n FROM tripplanner.ai_rate_counter")
                truncate = await su.fetchval(
                    "SELECT has_table_privilege('tripplanner', 'tripplanner.ai_rate_counter', 'TRUNCATE') AS t")
                raise _Rollback((counts, other, retry, purged, left, truncate))
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    counts, other, retry, purged, left, truncate = run(check())
    assert counts == [1, 2, 3] and other == 1
    assert 1 <= retry <= 3600                                 # seconds left in the hour window
    assert purged == "DELETE 1" and left == 3
    assert truncate is False


def test_cis_app_sees_nothing(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            return await su.fetchval(
                "SELECT has_table_privilege('cis_app', 'tripplanner.ai_rate_counter', 'SELECT') AS s")
        finally:
            await su.close()

    assert run(check()) is False


def test_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            first = await admin.fetchval("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-023'")
            await _as_owner(admin, TP023)
            rows = await admin.fetch("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-023'")
            return first, [r["applied_at"] for r in rows]
        finally:
            await admin.close()

    first, after = run(check())
    assert after == [first]
