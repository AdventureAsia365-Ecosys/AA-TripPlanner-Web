"""AA-712 slice 6a (ADR 0007): the place pipeline is CIS's. This repo keeps only read-only checks
over the frozen place tables; the writer ops answer 410 and no module writes place data."""
from __future__ import annotations

import ast
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from backend.extraction import checks, lambda_entry
from backend.shared import catalog_rls

BACKEND = Path(__file__).resolve().parents[1]

# Place data CIS owns now: shared.destinations and the frozen tripplanner place tables.
_PLACE_TABLE = (r"(?:shared\.destinations|tripplanner\.(?:itinerary_components|tour_day|tour_stop"
                r"|tour_graph_\w+|tour_leg|tour_junction))\b")
_WRITE = re.compile(r"\b(?:insert\s+into|update(?:\s+only)?|delete\s+from|merge\s+into|truncate(?:\s+table)?)\s+"
                    + _PLACE_TABLE,
                    re.IGNORECASE)
# A table name built at runtime (f"DELETE FROM tripplanner.{t}") would slip past the regex above.
_DYNAMIC = re.compile(r"\b(?:insert\s+into|update|delete\s+from|merge\s+into|truncate)\s+tripplanner\.\{",
                      re.IGNORECASE)
# asyncpg bulk loads name the table as a string argument, not in SQL (run_deterministic.py did
# conn.copy_records_to_table("itinerary_components", schema_name="tripplanner", ...)).
_COPY = re.compile(r"\bcopy_(?:records_)?to_table\(\s*[rf]?['\"](?:shared\.)?(?:destinations|itinerary_components"
                   r"|tour_day|tour_stop|tour_graph_\w+|tour_leg|tour_junction|\{)",
                   re.IGNORECASE)


def _backend_modules():
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] == "tests" or "__pycache__" in rel.parts:
            continue
        yield rel, path.read_text(encoding="utf-8")


def test_no_backend_module_writes_place_data():
    """Guard: CIS is the single writer of places (ADR 0002 / 0007). Readers stay until 6b/7."""
    offenders = [f"{rel}: {m.group(0)}" for rel, text in _backend_modules()
                 for rx in (_WRITE, _DYNAMIC, _COPY) for m in rx.finditer(text)]
    assert offenders == []


# AA-847: CIS 254 revokes the tripplanner grants of CIS 179 (raw_tours(tour_id, country)) and 180 (tour_atoms
# columns). No backend module may read those tables again; catalog.v_* is the read path (ADR 0001).
_REVOKED_READ = re.compile(r"\bsilver_aa_internal\.raw_tours\b|\bacp_contract\.tour_atoms\b", re.IGNORECASE)


def _sql_strings(text: str):
    """String literals of a module that are not docstrings (SQL lives in literals; prose in docstrings / comments)."""
    tree = ast.parse(text)
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.value


def test_no_backend_module_reads_raw_tours_or_tour_atoms():
    offenders = [f"{rel}: {m.group(0)}" for rel, text in _backend_modules() for lit in _sql_strings(text)
                 for m in _REVOKED_READ.finditer(lit)]
    assert offenders == []


def test_revoked_read_guard_catches_the_old_reader():
    old = ('ACTIVE_TOURS_SQL = """\n    SELECT pt.tour_id FROM gold_aa_internal.published_tours pt\n'
           '    JOIN silver_aa_internal.raw_tours rt ON rt.tour_id = pt.tour_id\n"""\n')
    assert any(_REVOKED_READ.search(lit) for lit in _sql_strings(old))
    doc = '"""Reads acp_contract.tour_atoms? No."""\n# nor silver_aa_internal.raw_tours\n'
    assert not any(_REVOKED_READ.search(lit) for lit in _sql_strings(doc))
    assert "catalog.v_tour" in catalog_rls.ACTIVE_TOURS_SQL


def test_guard_pattern_catches_the_old_writers():
    samples = ["INSERT INTO shared.destinations (name) VALUES ($1)",
               "UPDATE tripplanner.tour_day SET overnight_destination_id = $3",
               "DELETE FROM tripplanner.itinerary_components WHERE source_tour_id = $1",
               "insert into tripplanner.tour_graph_node (destination_id) values ($1)",
               "INSERT INTO\n  tripplanner.tour_junction (destination_a) VALUES ($1)",
               "MERGE INTO shared.destinations d USING x ON d.id = x.id",
               "UPDATE ONLY tripplanner.tour_leg SET km = 0"]
    assert all(_WRITE.search(s) for s in samples)
    assert _DYNAMIC.search('f"DELETE FROM tripplanner.{t}"')
    assert _COPY.search('await conn.copy_records_to_table(\n    "itinerary_components", schema_name="tripplanner")')
    assert _COPY.search("conn.copy_to_table('destinations', source=f, schema_name='shared')")
    assert not _WRITE.search("SELECT id FROM shared.destinations WHERE lower(name) = $1")
    assert not _WRITE.search("INSERT INTO tripplanner.trip_events (trip_id) VALUES ($1)")
    assert not _COPY.search('copy_records_to_table("trip_events", schema_name="tripplanner")')


# backend.extraction holds only the read-only checks now; every other submodule was a writer.
_EXTRACTION_MODULES = {"checks", "lambda_entry"}


def _extraction_imports(text: str) -> set[str]:
    """Names imported from backend.extraction (both `import backend.extraction.x` and
    `from backend.extraction import x, y` / `from backend.extraction.x import ...`)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[2] for a in node.names
                      if a.name.startswith("backend.extraction.")}
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "backend.extraction":
                found |= {a.name for a in node.names}
            elif node.module.startswith("backend.extraction."):
                found.add(node.module.split(".")[2])
    return found


def test_extraction_package_holds_only_the_read_only_checks():
    present = {p.stem for p in (BACKEND / "extraction").glob("*.py")} - {"__init__"}
    assert present == _EXTRACTION_MODULES


def test_no_module_imports_the_removed_writers():
    offenders = {str(rel): sorted(bad) for rel, text in _backend_modules()
                 if (bad := _extraction_imports(text) - _EXTRACTION_MODULES)}
    assert offenders == {}
    assert _extraction_imports("from backend.extraction import checks, tour_days") == {"checks", "tour_days"}


# --- lambda_entry ------------------------------------------------------------------------------

@pytest.fixture
def no_db(monkeypatch):
    """A removed or unknown op must answer before any DB connection is opened."""
    async def boom():
        raise AssertionError("get_pool must not be called")
    monkeypatch.setattr("backend.shared.db.get_pool", boom)


@pytest.mark.parametrize("op", ["tour_days", "components", "relink", "tour_graph", "prune"])
async def test_removed_writer_ops_answer_410(no_db, op):
    out = await lambda_entry.run_extraction_event({"op": op, "tour_ids": ["t1"]})
    assert out["status"] == 410 and out["removed"] is True
    assert out["error"] == f"unknown extraction op {op!r}"
    assert "CIS" in out["moved_to"] and out["ops"] == ["list", "orphans", "neighbours"]


@pytest.mark.parametrize("spec", [{"op": "nope"}, {}, None])
async def test_unknown_op_answers_400(no_db, spec):
    out = await lambda_entry.run_extraction_event(spec)
    assert out["status"] == 400 and "removed" not in out and out["error"].startswith("unknown extraction op")


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def test_neighbours_op_reads_the_frozen_graph(monkeypatch):
    dest, via = uuid.uuid4(), uuid.uuid4()

    class Conn:
        async def fetch(self, sql, *args):
            assert "tripplanner.tour_graph_edge" in sql and args == (str(dest), 5)
            return [{"destination_id": dest, "via_destination_id": via, "tour_count": 2, "via_name": None}]

    async def pool():
        return _Pool(Conn())
    monkeypatch.setattr("backend.shared.db.get_pool", pool)
    out = await lambda_entry.run_extraction_event({"op": "neighbours", "destination_id": str(dest), "limit": 5})
    assert out["op"] == "neighbours" and isinstance(out["query_ms"], float)
    assert out["neighbours"] == [{"destination_id": str(dest), "via_destination_id": str(via),
                                  "tour_count": 2, "via_name": None}]


# --- checks ------------------------------------------------------------------------------------

class _ListDb:
    def __init__(self, tours, hashes):
        self.tours, self.hashes = tours, hashes
        self.tenant = None

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *args):
        if "set_config('app.tenant_id'" in sql:
            self.tenant = args[0]

    async def fetch(self, sql, *args):
        # published_tours / raw_tours are tenant RLS: nothing is visible without app.tenant_id
        return self.tours if self.tenant else []

    async def fetchval(self, sql, tour_id):
        return self.hashes.get(tour_id)


def test_source_hash_changes_with_the_text_and_tolerates_none():
    assert checks.source_hash("a") != checks.source_hash("b")
    assert checks.source_hash(None) == checks.source_hash("")


async def test_list_tours_marks_changed_or_missing_itineraries_stale():
    tours = [{"tour_id": "a", "country": "Laos", "aa_name": "A", "aa_itineraries": "x"},
             {"tour_id": "b", "country": "Laos", "aa_name": "B", "aa_itineraries": "y"},
             {"tour_id": "c", "country": "Laos", "aa_name": "C", "aa_itineraries": "z"}]
    db = _ListDb(tours, {"a": checks.source_hash("x"), "b": checks.source_hash("old")})
    stale = {t["tour_id"]: t["stale"] for t in await checks.list_tours(db)}
    assert stale == {"a": False, "b": True, "c": True}


async def test_catalog_reads_set_the_aa_tenant_for_rls():
    db = _ListDb([{"tour_id": "a", "country": "Laos", "aa_name": "A", "aa_itineraries": "x"}], {})
    assert len(await checks.list_tours(db)) == 1
    assert db.tenant == "00000000-0000-0000-0000-000000000001"


async def test_orphans_counts_rows_of_tours_no_longer_active(monkeypatch):
    """AA-731: read-only check after a rerun wave / Master trash."""
    async def fake_catalog(db, sql, *args):
        assert sql == catalog_rls.ACTIVE_TOURS_SQL
        return [{"tour_id": "a"}, {"tour_id": "b"}]

    class Db:
        async def fetch(self, sql, active):
            assert active == ["a", "b"] and "NOT (source_tour_id = ANY" in sql
            return [{"source_tour_id": "gone", "n": 4}]

        async def fetchval(self, sql, active):
            return 2

    monkeypatch.setattr(catalog_rls, "fetch_catalog", fake_catalog)
    assert await checks.orphans(Db()) == {"active_tours": 2, "orphan_components": 4,
                                          "orphan_component_tours": ["gone"], "orphan_tour_day": 2}


async def test_orphans_refuses_when_no_active_tour_is_visible(monkeypatch):
    async def empty(db, sql, *args):
        return []

    monkeypatch.setattr(catalog_rls, "fetch_catalog", empty)
    assert await checks.orphans(object()) == {"error": "no active tours visible"}
