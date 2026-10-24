"""tp-019 replay (AA-908, root ADR 0006 Phase 4): traveller accounts and sessions.account_id, applied the way DB
Migrate applies it (as tp_owner, after tp-013 … tp-018).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the stand-in of test_tp013_pg.py:
tp-001…012 replayed as files, the CIS 243 grants, tp-013 as the master, tp-014 … tp-018 with ``SET ROLE tp_owner``,
then tp-019 the same way.

Checks: the table is tp_owner's with the expected columns, the UNIQUE (iss, sub) and the DM-6.1 tags; the
sessions.account_id FK (ON DELETE SET NULL, DM-7) and its partial index; the runtime role tripplanner may SELECT /
INSERT / UPDATE accounts (no DELETE / TRUNCATE, no DDL) and may set sessions.account_id; the claim shape (set only
where NULL) never moves a session owned by another account; erasing an account unlinks its sessions; registration
once and a re-run as tp_owner being a no-op.
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

BEFORE_TP019 = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if 13 < int(f.name[:3]) < 19)
TP019 = MIGRATIONS / "019_traveller_accounts.sql"
ISS = "https://cognito-idp.us-west-1.amazonaws.com/us-west-1_TestPool1"
COLUMNS_SQL = """
SELECT a.attname::text, format_type(a.atttypid, a.atttypmod), a.attnotnull, col_description(a.attrelid, a.attnum)
  FROM pg_attribute a
 WHERE a.attrelid = $1::regclass AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY a.attnum"""
PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


async def _as_owner(admin, path) -> None:
    """DB Migrate's switch for a file after tp-013: SET ROLE to the live owner of schema tripplanner."""
    await admin.execute("SET ROLE tp_owner")
    try:
        await admin.execute(path.read_text(encoding="utf-8"))
    finally:
        await admin.execute("RESET ROLE")


class _Rollback(Exception):
    def __init__(self, value):
        super().__init__(value)
        self.value = value


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    name = f"tp_aa908_{uuid.uuid4().hex[:8]}"
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
            for f in BEFORE_TP019:
                await _as_owner(admin, f)
            await _as_owner(admin, TP019)
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


def test_files_before_tp019_are_tp014_to_tp018():
    assert [f.name[:3] for f in BEFORE_TP019] == ["014", "015", "016", "017", "018"]


def test_accounts_table_is_tp_owners_with_the_expected_columns_and_tags(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            owner = await su.fetchval("SELECT tableowner FROM pg_tables WHERE schemaname = 'tripplanner' "
                                      "AND tablename = 'traveller_accounts'")
            cols = [tuple(r) for r in await su.fetch(COLUMNS_SQL, "tripplanner.traveller_accounts")]
            unique = await su.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'tripplanner.traveller_accounts'::regclass AND contype = 'u'")
            return owner, cols, unique
        finally:
            await su.close()

    owner, cols, unique = run(check())
    assert owner == "tp_owner"
    assert [c[:3] for c in cols] == [("id", "uuid", True), ("iss", "text", True), ("sub", "text", True),
                                     ("created_at", "timestamp with time zone", True),
                                     ("last_sign_in_at", "timestamp with time zone", False)]
    tags = {c[0]: c[3] for c in cols}
    assert tags["iss"].startswith("pii:online_id; ") and tags["sub"].startswith("pii:online_id; ")
    assert not any(c[0] == "email" for c in cols)  # the email is read from the token, never stored
    assert unique == "UNIQUE (iss, sub)"


def test_sessions_account_id_is_a_nullable_fk_with_a_partial_index(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            col = [tuple(r) for r in await su.fetch(COLUMNS_SQL, "tripplanner.sessions") if r[0] == "account_id"]
            fk = await su.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'tripplanner.sessions'::regclass "
                "AND contype = 'f' AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = "
                "'tripplanner.sessions'::regclass AND attname = 'account_id')]")
            idx = await su.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname = 'tripplanner' "
                                    "AND indexname = 'sessions_account_idx'")
            return col, fk, idx
        finally:
            await su.close()

    col, fk, idx = run(check())
    assert [c[:3] for c in col] == [("account_id", "uuid", False)]
    assert fk == "FOREIGN KEY (account_id) REFERENCES tripplanner.traveller_accounts(id) ON DELETE SET NULL"
    assert idx.endswith("USING btree (account_id) WHERE (account_id IS NOT NULL)")


def test_runtime_privileges_on_accounts(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            return {p: await su.fetchval("SELECT has_table_privilege('tripplanner', "
                                         "'tripplanner.traveller_accounts', $1)", p) for p in PRIVS}
        finally:
            await su.close()

    assert run(check()) == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False, "TRUNCATE": False}


def test_runtime_upsert_and_claim_shape(db):
    """As tripplanner: the sign-in upsert is unique per (iss, sub); the claim sets account_id only where it is NULL,
    so a session already owned by another account is never moved; no DELETE and no DDL."""
    sub_a, sub_b = str(uuid.uuid4()), str(uuid.uuid4())
    guest, owned = uuid.uuid4(), uuid.uuid4()
    hash_ = secrets.token_bytes(32)

    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                upsert = ("INSERT INTO tripplanner.traveller_accounts (iss, sub, last_sign_in_at) VALUES ($1, $2, now()) "
                          "ON CONFLICT (iss, sub) DO UPDATE SET last_sign_in_at = EXCLUDED.last_sign_in_at "
                          "RETURNING id")
                a1 = await su.fetchval(upsert, ISS, sub_a)
                a2 = await su.fetchval(upsert, ISS, sub_a)
                b = await su.fetchval(upsert, ISS, sub_b)
                for sid in (guest, owned):
                    await su.execute("INSERT INTO tripplanner.sessions (id, guest_token_hash) VALUES ($1, $2)",
                                     sid, hash_)
                await su.execute("UPDATE tripplanner.sessions SET account_id = $1 WHERE id = $2", b, owned)
                claim = ("UPDATE tripplanner.sessions SET account_id = $1 "
                         "WHERE guest_token_hash = $2 AND account_id IS NULL")
                claimed = await su.execute(claim, a1, hash_)
                again = await su.execute(claim, a1, hash_)
                owners = {r["id"]: r["account_id"] for r in await su.fetch(
                    "SELECT id, account_id FROM tripplanner.sessions WHERE id = ANY($1::uuid[])", [guest, owned])}
                with pytest.raises(asyncpg.ForeignKeyViolationError):
                    async with su.transaction():
                        await su.execute("UPDATE tripplanner.sessions SET account_id = $1 WHERE id = $2",
                                         uuid.uuid4(), guest)
                with pytest.raises(asyncpg.UniqueViolationError):
                    async with su.transaction():
                        await su.execute("INSERT INTO tripplanner.traveller_accounts (iss, sub) VALUES ($1, $2)",
                                         ISS, sub_a)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with su.transaction():
                        await su.execute("DELETE FROM tripplanner.traveller_accounts")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with su.transaction():
                        await su.execute("ALTER TABLE tripplanner.traveller_accounts ADD COLUMN email text")
                raise _Rollback((a1, a2, b, claimed, again, owners))
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    a1, a2, b, claimed, again, owners = run(check())
    assert a1 == a2 and a1 != b
    assert claimed == "UPDATE 1" and again == "UPDATE 0"
    assert owners == {guest: a1, owned: b}


def test_erasing_an_account_unlinks_its_sessions(db):
    """DM-7: the reviewed erasure step deletes the account row (as tp_owner, never the runtime role); the account's
    sessions stay as guest sessions (account_id set to NULL) instead of blocking the DELETE."""
    sid = uuid.uuid4()

    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tp_owner")
                acc = await su.fetchval("INSERT INTO tripplanner.traveller_accounts (iss, sub) VALUES ($1, $2) "
                                        "RETURNING id", ISS, str(uuid.uuid4()))
                await su.execute("INSERT INTO tripplanner.sessions (id, guest_token_hash, account_id) "
                                 "VALUES ($1, $2, $3)", sid, secrets.token_bytes(32), acc)
                deleted = await su.execute("DELETE FROM tripplanner.traveller_accounts WHERE id = $1", acc)
                left = await su.fetch("SELECT account_id FROM tripplanner.sessions WHERE id = $1", sid)
                raise _Rollback((deleted, [r["account_id"] for r in left]))
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    assert run(check()) == ("DELETE 1", [None])


def test_checks_refuse_a_non_https_issuer_and_an_empty_sub(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            out = []
            for iss, sub in (("http://evil.example/pool", "x"), (ISS, "")):
                try:
                    async with su.transaction():
                        await su.execute("INSERT INTO tripplanner.traveller_accounts (iss, sub) VALUES ($1, $2)",
                                         iss, sub)
                    out.append(None)
                except asyncpg.CheckViolationError as e:
                    out.append(e.constraint_name)
            return out
        finally:
            await su.close()

    assert run(check()) == ["traveller_accounts_iss_chk", "traveller_accounts_sub_chk"]


def test_tp019_registered_and_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            await _as_owner(admin, TP019)
            return await admin.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-019'")
        finally:
            await admin.close()

    assert run(check()) == 1
