"""tp-016 replay (AA-877): data standards DM-6.1 tags on the TripPlanner columns, applied the way DB Migrate applies
them (as tp_owner, after tp-013 / tp-014 / tp-015).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the stand-in of test_tp013_pg.py /
test_tp015_pg.py: tp-001…012 replayed as files, the CIS 243 grants, tp-013 as the master, tp-014 and tp-015 with
``SET ROLE tp_owner``; the column comments are snapshotted, then tp-016 runs the same way.

Checks: tp-016 changes exactly the columns of EXPECTED, each comment starts with its tag (and names its ``also``
tags); sessions.guest_token / guest_token_hash keep their tp-011 text after the '; '; every tag uses a DM-6.1 class;
the candidate rule (a column named like personal data carries a tag or an ALLOWLIST entry with a reason; same tokens
as AA-CIS-Infra db/gen_schema_reference.py); registration and a re-run as tp_owner being a no-op.
"""
from __future__ import annotations

import re
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

asyncpg = pytest.importorskip("asyncpg")

BEFORE_TP016 = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if 13 < int(f.name[:3]) < 16)
TP016 = MIGRATIONS / "016_pii_column_tags.sql"
CIS_READER = "DO $$ BEGIN IF to_regrole('cis_app') IS NULL THEN CREATE ROLE cis_app NOLOGIN; END IF; END $$;"

CLASSES = {
    "pii": {"contact", "name", "address", "profile", "online_id", "free_text", "travel", "payment"},
    "conf": {"commercial", "credential"},
}
TAG_RE = re.compile(r"^((?:pii|conf):[a-z_]+)(?=;|\s+[—–-]\s|$)")
ALSO_RE = re.compile(r"(?<![a-z_])((?:pii|conf):[a-z_]+)")

# relation.column -> (tag, also) set by tp-016.
EXPECTED = {
    "customers.name": ("pii:name", ()),
    "customers.phone": ("pii:contact", ()),
    "customers.email": ("pii:contact", ()),
    "outbox.payload": ("pii:contact", ("pii:name", "pii:free_text", "pii:travel")),
    "v_inquiry_outbox.payload": ("pii:contact", ("pii:name", "pii:free_text", "pii:travel")),
    "trip_drafts.itinerary": ("pii:travel", ("pii:free_text",)),
    "trip_drafts.date_start": ("pii:travel", ()),
    "trip_drafts.date_end": ("pii:travel", ()),
    "trip_events.payload": ("pii:free_text", ("pii:travel",)),
    "sessions.guest_token_hash": ("conf:credential", ()),
    "sessions.guest_token": ("conf:credential", ()),
}
# tp-011 form 'conf:credential — <text>': tp-016 restates it as 'conf:credential; <text>'.
TP011_DASH = "conf:credential — "
RESTATED = ("sessions.guest_token_hash", "sessions.guest_token")

TOKENS = frozenset({
    "email", "phone", "name", "address", "note", "notes", "message", "payload", "snapshot", "title", "summary",
    "label", "sub", "iss", "data", "itinerary",
})
# Untagged on purpose, one reason each (mirrored in AA-CIS-Infra PII_ALLOWLIST, tripplanner.* entries).
ALLOWLIST = {
    "itinerary_components.name": "frozen read model of the CIS catalog: component name",
    "tour_day.title": "frozen read model of the CIS catalog: tour day title",
    "tour_graph_node.name": "frozen read model of the CIS catalog: place name",
    "narration_cache.itinerary_hash": "hash of a catalog itinerary (cache key), no identity",
}

COMMENTS_SQL = """
SELECT c.relname || '.' || a.attname AS col, col_description(c.oid, a.attnum) AS comment
  FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
 WHERE c.relnamespace = 'tripplanner'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm')
   AND a.attnum > 0 AND NOT a.attisdropped"""


def parse(comment):
    if not comment:
        return None, []
    m = TAG_RE.match(comment)
    tag = m.group(1) if m else None
    also = []
    for t in ALSO_RE.findall(comment[m.end():] if m else comment):
        if t != tag and t not in also:
            also.append(t)
    return tag, also


async def _as_owner(admin, path) -> None:
    """DB Migrate's switch for a file after tp-013: SET ROLE to the live owner of schema tripplanner."""
    await admin.execute("SET ROLE tp_owner")
    try:
        await admin.execute(path.read_text(encoding="utf-8"))
    finally:
        await admin.execute("RESET ROLE")


async def _comments(conn) -> dict:
    return {r["col"]: r["comment"] for r in await conn.fetch(COMMENTS_SQL)}


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    name = f"tp_aa877_{uuid.uuid4().hex[:8]}"
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
            for f in BEFORE_TP016:
                await _as_owner(admin, f)
            before = await _comments(admin)
            await _as_owner(admin, TP016)
            after = await _comments(admin)
        finally:
            await admin.close()
        return {"admin_dsn": admin_dsn, "su_dsn": _dsn(pg_dsn, database=name), "before": before, "after": after}

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


def test_files_before_tp016_are_tp014_and_tp015():
    assert [f.name[:3] for f in BEFORE_TP016] == ["014", "015"]


def test_tp016_changes_exactly_the_expected_columns(db):
    before, after = db["before"], db["after"]
    assert set(before) == set(after)
    changed = {col for col in after if after[col] != before[col]}
    assert changed == set(EXPECTED), (sorted(changed - set(EXPECTED)), sorted(set(EXPECTED) - changed))
    for col, (tag, also) in EXPECTED.items():
        assert after[col].startswith(f"{tag}; "), (col, after[col])
        assert parse(after[col]) == (tag, list(also)), (col, after[col])


def test_tp011_text_is_kept_after_the_tag(db):
    before, after = db["before"], db["after"]
    for col in RESTATED:
        old = before[col]
        assert old.startswith(TP011_DASH), (col, old)
        assert after[col] == "conf:credential; " + old[len(TP011_DASH):], (col, after[col])


def test_every_tag_uses_a_known_class(db):
    for col, comment in db["after"].items():
        tag, also = parse(comment)
        if tag is None:
            assert not also, f"{col}: names {also} but does not start with a tag: {comment!r}"
        for t in ([tag] if tag else []) + also:
            kind, _, klass = t.partition(":")
            assert klass in CLASSES[kind], f"{col}: unknown class {t}"


def test_columns_named_like_personal_data_are_tagged_or_allowlisted(db):
    untagged = {col for col, comment in db["after"].items() if parse(comment)[0] is None}
    candidates = {col for col in untagged if set(col.split(".", 1)[1].split("_")) & TOKENS}
    assert candidates - set(ALLOWLIST) == set(), sorted(candidates - set(ALLOWLIST))
    assert set(ALLOWLIST) - candidates == set(), f"stale allowlist entries: {sorted(set(ALLOWLIST) - candidates)}"
    assert all(reason.strip() for reason in ALLOWLIST.values())


def test_tp016_registered_and_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            await _as_owner(admin, TP016)
            registered = await admin.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-016'")
            return registered, await _comments(admin)
        finally:
            await admin.close()

    registered, comments = run(check())
    assert registered == 1
    assert comments == db["after"]
