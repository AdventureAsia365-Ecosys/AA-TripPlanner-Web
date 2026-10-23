"""Full-chain replay, twice (AA-849): every ``migrations/NNN_*.sql`` applied in order, then applied again.

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres, see test_tp013_pg.py; other hosts are
refused). CI job "Migrations (lint, PG 15 replay)" runs it on PostgreSQL 15 + pgvector with ``--fail-on-skip``.

Stand-in: the one of test_tp013_pg.py (roles, the NOSUPERUSER master ``aa_cis_admin``, ``shared.schema_versions``,
the live default privileges FOR ROLE aa_cis_admin) plus the CIS runtime role ``cis_app`` of test_tp015_pg.py (tp-015
raises when it is missing), then:

  * pass 1: every file in file order (002 before 001, tp-001's FK), each as the role DB Migrate would use: the live
    owner of schema ``tripplanner`` read before the file (XD8), i.e. the login role ``aa_cis_admin`` until tp-013
    transfers the schema, then ``SET ROLE tp_owner``. The CIS 243 grants are applied right before tp-013, as on Dev;
  * pass 2: every file again, each as the role it ran as in pass 1, except ``NOT_RERUNNABLE`` (tp-001, which predates
    the registry convention and which DB Migrate never re-runs: tp-007 registered it) and ``SUPERSEDED_BY_DROP``
    (files about objects a later file dropped; DB Migrate never re-runs them either). Pass 2 must change nothing:
    the catalog of schemas ``tripplanner`` and ``shared``, the row count of every table there and every
    ``shared.schema_versions`` row (``applied_at`` included) are compared before and after.

Checks: the role rule equals ``migration_lint.migration_role``; the ``tp-%`` keys registered in
``shared.schema_versions`` are exactly the files' keys (after each pass); pass 2 is a no-op; the runtime role keeps
its grants on every table and sequence of ``tripplanner``.
"""
from __future__ import annotations

import secrets
import uuid

import pytest

from migration_lint import NAME_RE, REGISTRY_FROM, TRANSFER, migration_files, migration_role
from test_tp013_pg import pg_dsn  # noqa: F401  (fixture: --pg-dsn, local only, --pg-major / --pg-require-vector)
from test_tp013_pg import BOOTSTRAP_ROLES, CIS_243_GRANTS, LIVE_STANDINS, _dsn, _replay_text, run
from test_tp015_pg import CIS_READER

asyncpg = pytest.importorskip("asyncpg")

# Files pass 2 does not re-run, with the reason. Only files before the registry convention (tp-007) may be here
# (test_not_rerunnable_is_frozen_and_still_needed); every later file must be safe to run twice.
NOT_RERUNNABLE = {
    "001": "pre-convention (plain CREATE TABLE); registered by tp-007's backfill, so DB Migrate apply never "
           "re-runs it; making it idempotent means editing an applied file and its AA-CIS-Infra byte copy",
}

# Files pass 2 does not re-run because a LATER file dropped what they create or alter, with the reason. Unlike
# NOT_RERUNNABLE they may be after the registry convention, but each reason must name the dropping file, and each
# must still fail (or be a no-op only because the dropping file runs again) when re-run on the final state
# (test_superseded_by_drop_is_reviewed_and_still_needed). DB Migrate never re-runs a registered file.
SUPERSEDED_BY_DROP = {
    "003": "creates tripplanner.tour_day, which tp-018 dropped (AA-712 X4); a re-run would bring the table back",
    "004": "creates the Tour Graph tables (tour_stop, tour_graph_*, tour_leg, tour_junction), which tp-018 dropped",
    "013": "transfers ownership of tripplanner.itinerary_components / tour_*, which tp-018 dropped (tp-001, the only "
           "file creating itinerary_components, is NOT_RERUNNABLE); its own re-run as the master is covered by "
           "test_tp013_pg.py::test_rerun_as_the_master_is_a_no_op",
}

# Everything a re-run could change in the two schemas the files touch. One text line per fact; set equality.
CATALOG_SNAPSHOT = r"""
WITH ns AS (SELECT oid, nspname FROM pg_namespace WHERE nspname IN ('tripplanner', 'shared'))
SELECT 'schema ' || nspname || ' owner=' || pg_get_userbyid(nspowner)
       || ' acl=' || coalesce((SELECT string_agg(a::text, ',' ORDER BY a::text) FROM unnest(nspacl) a), '-')
  FROM pg_namespace WHERE oid IN (SELECT oid FROM ns)
UNION ALL
SELECT 'relation ' || c.oid::regclass::text || ' kind=' || c.relkind::text || ' owner=' || pg_get_userbyid(c.relowner)
       || ' acl=' || coalesce((SELECT string_agg(a::text, ',' ORDER BY a::text) FROM unnest(c.relacl) a), '-')
       || ' options=' || coalesce(array_to_string(c.reloptions, ','), '-')
       || ' comment=' || coalesce(obj_description(c.oid, 'pg_class'), '-')
  FROM pg_class c WHERE c.relnamespace IN (SELECT oid FROM ns)
UNION ALL
SELECT 'column ' || c.oid::regclass::text || '.' || a.attname || ' ' || format_type(a.atttypid, a.atttypmod)
       || ' notnull=' || a.attnotnull || ' default=' || coalesce(pg_get_expr(d.adbin, d.adrelid), '-')
       || ' acl=' || coalesce((SELECT string_agg(x::text, ',' ORDER BY x::text) FROM unnest(a.attacl) x), '-')
       || ' comment=' || coalesce(col_description(c.oid, a.attnum), '-')
  FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
  LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
 WHERE c.relnamespace IN (SELECT oid FROM ns)
UNION ALL
SELECT 'constraint ' || conrelid::regclass::text || ' ' || conname || ' ' || pg_get_constraintdef(oid)
  FROM pg_constraint WHERE connamespace IN (SELECT oid FROM ns)
UNION ALL
SELECT 'index ' || pg_get_indexdef(i.indexrelid)
  FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relnamespace IN (SELECT oid FROM ns)
UNION ALL
SELECT 'view ' || c.oid::regclass::text || ' ' || pg_get_viewdef(c.oid)
  FROM pg_class c WHERE c.relnamespace IN (SELECT oid FROM ns) AND c.relkind IN ('v', 'm')
UNION ALL
SELECT 'function ' || p.oid::regprocedure::text || ' owner=' || pg_get_userbyid(p.proowner)
       || ' acl=' || coalesce((SELECT string_agg(a::text, ',' ORDER BY a::text) FROM unnest(p.proacl) a), '-')
       || ' def=' || CASE WHEN p.prokind IN ('f', 'p') THEN pg_get_functiondef(p.oid) ELSE p.prokind::text END
  FROM pg_proc p WHERE p.pronamespace IN (SELECT oid FROM ns)
UNION ALL
SELECT 'trigger ' || pg_get_triggerdef(t.oid) || ' enabled=' || t.tgenabled::text
  FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
 WHERE NOT t.tgisinternal AND c.relnamespace IN (SELECT oid FROM ns)
UNION ALL
SELECT 'type ' || t.oid::regtype::text || ' owner=' || pg_get_userbyid(t.typowner)
  FROM pg_type t WHERE t.typnamespace IN (SELECT oid FROM ns) AND t.typtype IN ('e', 'd', 'c')
       AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.reltype = t.oid AND c.relkind <> 'c')
UNION ALL
SELECT 'default acl ' || pg_get_userbyid(d.defaclrole) || ' in ' || coalesce(n.nspname, '*') || ' on '
       || d.defaclobjtype::text || ' ' || (SELECT string_agg(a::text, ',' ORDER BY a::text) FROM unnest(d.defaclacl) a)
  FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
UNION ALL
SELECT 'registry ' || version || ' applied_at=' || applied_at::text || ' ' || coalesce(description, '-')
  FROM shared.schema_versions
"""

TABLES_SQL = """
SELECT c.oid::regclass::text FROM pg_class c
 WHERE c.relnamespace IN ('tripplanner'::regnamespace, 'shared'::regnamespace) AND c.relkind IN ('r', 'p')
 ORDER BY 1
"""

OWNER_SQL = "SELECT pg_get_userbyid(nspowner)::text FROM pg_namespace WHERE nspname = 'tripplanner'"


def _files() -> list:
    """(number, path) of every migration, in the order they were applied on Dev: 002 before 001 (tp-001's FK)."""
    numbered = sorted((int(NAME_RE.match(p.name).group(1)), p) for p in migration_files())
    return sorted(numbered, key=lambda np: (np[0] != 2, np[0]))


async def _snapshot(conn) -> set:
    facts = {r[0] for r in await conn.fetch(CATALOG_SNAPSHOT)}
    for (table,) in await conn.fetch(TABLES_SQL):
        facts.add(f"rows {table}={await conn.fetchval(f'SELECT count(*) FROM {table}')}")
    return facts


async def _registered(conn) -> set:
    return {r[0] for r in await conn.fetch("SELECT version FROM shared.schema_versions WHERE version LIKE 'tp-%'")}


async def _apply(admin, path, role: str, vector: bool) -> None:
    """Runs one file as `role`: the login role as is, another role through SET ROLE (DB Migrate's switch, XD8).
    On an error the file's open transaction is rolled back and the role reset, so the connection stays usable."""
    sql = _replay_text(path, vector)
    switch = role != "aa_cis_admin"
    try:
        if switch:
            await admin.execute(f"SET ROLE {role}")
        await admin.execute(sql)
    except asyncpg.PostgresError:
        if admin.is_in_transaction():
            await admin.execute("ROLLBACK")
        raise
    finally:
        if switch:
            await admin.execute("RESET ROLE")


@pytest.fixture(scope="module")
def replay(pg_dsn):  # noqa: F811
    name = f"tp_aa849_{uuid.uuid4().hex[:8]}"
    password = secrets.token_urlsafe(16)

    async def body() -> dict:
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(BOOTSTRAP_ROLES)
            # cis_app is cluster-global and tp-015 requires it; create it here so the module does not depend on
            # test_tp015_pg.py having run first on the same cluster (CI starts a fresh one each run).
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

        admin = await asyncpg.connect(_dsn(pg_dsn, user="aa_cis_admin", password=password, database=name))
        try:
            await admin.execute(LIVE_STANDINS)
            roles, pass2_errors = {}, {}
            for number, path in _files():                                   # pass 1
                if number == TRANSFER:
                    await admin.execute(CIS_243_GRANTS)                    # CIS 243 before tp-013, as on Dev
                owner = await admin.fetchval(OWNER_SQL)
                roles[number] = "aa_cis_admin" if owner in (None, "aa_cis_admin") else owner
                try:
                    await _apply(admin, path, roles[number], vector)
                except asyncpg.PostgresError as e:
                    pytest.fail(f"pass 1: {path.name} as {roles[number]}: {type(e).__name__}: {e}")
            registered_1 = await _registered(admin)
            before = await _snapshot(admin)
            for number, path in _files():                                   # pass 2
                if f"{number:03d}" in NOT_RERUNNABLE or f"{number:03d}" in SUPERSEDED_BY_DROP:
                    continue
                try:
                    await _apply(admin, path, roles[number], vector)
                except asyncpg.PostgresError as e:
                    pass2_errors[path.name] = f"as {roles[number]}: {type(e).__name__}: {e}"
            after = await _snapshot(admin)
            registered_2 = await _registered(admin)
            rerun_exempt = {}
            for number, path in _files():                                   # each exemption must still be needed
                if f"{number:03d}" in NOT_RERUNNABLE:
                    try:
                        await _apply(admin, path, roles[number], vector)
                        rerun_exempt[f"{number:03d}"] = "re-ran without an error"
                    except asyncpg.PostgresError as e:
                        rerun_exempt[f"{number:03d}"] = type(e).__name__
            grants = await admin.fetch("""
                SELECT c.relname::text AS name, c.relkind::text AS kind,
                       has_table_privilege('tripplanner', c.oid, 'SELECT') AS sel,
                       has_table_privilege('tripplanner', c.oid, 'INSERT') AS ins,
                       has_table_privilege('tripplanner', c.oid, 'UPDATE') AS upd,
                       has_table_privilege('tripplanner', c.oid, 'DELETE') AS del,
                       CASE WHEN c.relkind = 'S' THEN has_sequence_privilege('tripplanner', c.oid, 'USAGE')
                            AND has_sequence_privilege('tripplanner', c.oid, 'SELECT') END AS seq
                  FROM pg_class c
                 WHERE c.relnamespace = 'tripplanner'::regnamespace AND c.relkind IN ('r', 'p', 'S')""")
            # Last, because it changes the database (the files carry their own BEGIN / COMMIT, so no rollback):
            # each superseded file must still fail, or change the final state, when re-run.
            superseded = {}
            for number, path in _files():
                if f"{number:03d}" in SUPERSEDED_BY_DROP:
                    state = await _snapshot(admin)
                    try:
                        await _apply(admin, path, roles[number], vector)
                        changed = await _snapshot(admin) != state
                        superseded[f"{number:03d}"] = "changes the final state" if changed else "no-op"
                    except asyncpg.PostgresError as e:
                        superseded[f"{number:03d}"] = type(e).__name__
            return {"roles": roles, "registered_1": registered_1, "registered_2": registered_2, "before": before,
                    "after": after, "pass2_errors": pass2_errors, "rerun_exempt": rerun_exempt,
                    "superseded": superseded,
                    "grants": [dict(r) for r in grants]}
        finally:
            await admin.close()

    async def teardown() -> None:
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await su.close()

    try:
        yield run(body())
    finally:
        run(teardown())


def _keys() -> set:
    return {f"tp-{n:03d}" for n, _ in _files()}


def test_file_numbers_are_contiguous_from_001():
    numbers = sorted(n for n, _ in _files())
    assert numbers == list(range(1, len(numbers) + 1)), numbers


def test_not_rerunnable_is_frozen_and_still_needed(replay):
    assert all(int(k) < REGISTRY_FROM for k in NOT_RERUNNABLE), \
        f"only pre-convention files (before tp-{REGISTRY_FROM:03d}) may skip pass 2: {sorted(NOT_RERUNNABLE)}"
    stale = {k: v for k, v in replay["rerun_exempt"].items() if v == "re-ran without an error"}
    assert set(replay["rerun_exempt"]) == set(NOT_RERUNNABLE) and not stale, \
        f"{sorted(stale)} re-ran without an error: drop them from NOT_RERUNNABLE so pass 2 covers them"
    assert replay["rerun_exempt"] == {"001": "DuplicateTableError"}


def test_superseded_by_drop_is_reviewed_and_still_needed(replay):
    for number, reason in SUPERSEDED_BY_DROP.items():
        dropper = [f"tp-{n:03d}" for n, _ in _files() if f"tp-{n:03d}" in reason and n > int(number)]
        assert dropper, f"{number}: the reason must name the later file that dropped its objects"
    stale = {k: v for k, v in replay["superseded"].items() if v == "no-op"}
    assert set(replay["superseded"]) == set(SUPERSEDED_BY_DROP) and not stale, \
        f"{sorted(stale)} re-ran as a no-op on the final state: drop them from SUPERSEDED_BY_DROP"


def test_runner_role_rule_matches_the_lint(replay):
    assert replay["roles"] == {n: migration_role(n) for n, _ in _files()}


def test_registered_keys_match_the_files_after_pass_1(replay):
    keys = _keys()
    assert replay["registered_1"] == keys, (
        f"files not registered: {sorted(keys - replay['registered_1'])}; "
        f"registered without a file: {sorted(replay['registered_1'] - keys)}")


def test_pass_2_runs_every_file_again_without_an_error(replay):
    assert replay["pass2_errors"] == {}, "\n".join(f"{k}: {v}" for k, v in sorted(replay["pass2_errors"].items()))


def test_pass_2_changes_nothing(replay):
    lost, gained = sorted(replay["before"] - replay["after"]), sorted(replay["after"] - replay["before"])
    assert not lost and not gained, "pass 2 changed the database:\n" + "\n".join(
        [f"- {x}" for x in lost] + [f"+ {x}" for x in gained])


def test_registered_keys_match_the_files_after_pass_2(replay):
    assert replay["registered_2"] == _keys()


# Reviewed exceptions to the runtime's full DML: table -> the privileges it keeps (sel / ins / upd / del).
RUNTIME_DML_EXCEPTIONS = {
    # tp-017 (AA-847): the id-map is filled once (INSERT, as tripplanner) and read by assembly; never edited.
    "component_id_map": {"sel", "ins"},
}


def test_runtime_role_keeps_its_grants_on_every_table_and_sequence(replay):
    """The runtime role keeps what the tp_owner default privileges give (tp-013): DML on every table, USAGE + SELECT
    on every sequence, after both passes. A table that must deny the runtime something needs an explicit,
    reviewed exception here (RUNTIME_DML_EXCEPTIONS: exactly the privileges it keeps)."""
    tables = [g for g in replay["grants"] if g["kind"] != "S"]
    sequences = [g for g in replay["grants"] if g["kind"] == "S"]
    dml = ("sel", "ins", "upd", "del")
    missing = [g["name"] for g in tables if g["name"] not in RUNTIME_DML_EXCEPTIONS
               and not all(g[k] for k in dml)]
    missing += [g["name"] for g in tables if g["name"] in RUNTIME_DML_EXCEPTIONS
                and {k for k in dml if g[k]} != RUNTIME_DML_EXCEPTIONS[g["name"]]]
    missing += [g["name"] for g in sequences if not g["seq"]]
    assert tables and sequences and not missing, f"tripplanner lacks its DML / sequence grants on: {missing}"
