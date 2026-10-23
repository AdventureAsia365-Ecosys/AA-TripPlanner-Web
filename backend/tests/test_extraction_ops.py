"""AA-712 slice 6a (ADR 0007): the place pipeline is CIS's; AA-712 X4: this repo reads CIS content only through the
catalog views. Every extraction op answers 410, no module writes place data, and no module reads anything of
CIS but catalog.* (plus the model gateway tables) or a frozen tripplanner place table (dropped by tp-018)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from backend.extraction import lambda_entry

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


# AA-712 X4: CIS 270 revokes every tripplanner grant on CIS objects except the catalog views and the model gateway
# (CIS 254 already revoked raw_tours (179) and tour_atoms (180)); tp-018 drops the frozen place tables. So the only
# schema-qualified objects a backend SQL string may name are these.
_QUALIFIED = re.compile(r"\b(shared|gold_aa_internal|silver_aa_internal|acp_\w+|geo|tripplanner|catalog|booking|public)"
                        r"\.(\w+)\b", re.IGNORECASE)
_ALLOWED_TRIPPLANNER = {"sessions", "customers", "trip_events", "trip_drafts", "outbox", "narration_cache",
                        "llm_spend_daily", "component_id_map"}
_ALLOWED_SHARED = {"llm_role_config", "llm_model_catalog", "llm_call_log"}   # CIS 173, the model gateway


def _allowed(schema: str, name: str) -> bool:
    schema, name = schema.lower(), name.lower()
    return (schema == "catalog" or (schema == "tripplanner" and name in _ALLOWED_TRIPPLANNER)
            or (schema == "shared" and name in _ALLOWED_SHARED))


def _sql_strings(text: str):
    """String literals of a module that are not docstrings (SQL lives in literals; prose in docstrings / comments)."""
    tree = ast.parse(text)
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.value


def _foreign_reads(text: str) -> list[str]:
    return [m.group(0) for lit in _sql_strings(text) for m in _QUALIFIED.finditer(lit)
            if not _allowed(m.group(1), m.group(2))]


def test_no_backend_module_reads_cis_internals_or_the_frozen_place_tables():
    offenders = [f"{rel}: {hit}" for rel, text in _backend_modules() for hit in _foreign_reads(text)]
    assert offenders == []


def test_read_guard_catches_the_old_readers():
    old = ('ACTIVE_TOURS_SQL = """\n    SELECT pt.tour_id FROM gold_aa_internal.published_tours pt\n'
           '    JOIN silver_aa_internal.raw_tours rt ON rt.tour_id = pt.tour_id\n"""\n'
           'X = ("SELECT * FROM tripplanner.itinerary_components c "\n'
           '     "JOIN shared.destinations d ON d.id = c.destination_id")\n'
           'Y = "SELECT * FROM acp_contract.v_active_tour_atoms, tripplanner.tour_stop, shared.v_tour_photos"\n')
    assert sorted(_foreign_reads(old)) == sorted([
        "gold_aa_internal.published_tours", "silver_aa_internal.raw_tours", "tripplanner.itinerary_components",
        "shared.destinations", "acp_contract.v_active_tour_atoms", "tripplanner.tour_stop", "shared.v_tour_photos"])
    ok = ('"""Docstring prose may name gold_aa_internal.published_tours."""\n# and so may tripplanner.tour_stop\n'
          'A = "SELECT * FROM catalog.v_tour_component c JOIN catalog.v_destination d USING (destination_id)"\n'
          'B = "INSERT INTO shared.llm_call_log (stage) VALUES ($1)"\n'
          'C = "SELECT * FROM tripplanner.component_id_map"\n')
    assert _foreign_reads(ok) == []


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


# backend.extraction holds only the 410 entry point now (writers removed in slice 6a, checks in X4).
_EXTRACTION_MODULES = {"lambda_entry"}


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


def test_extraction_package_holds_only_the_entry_point():
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


@pytest.mark.parametrize("op", ["tour_days", "components", "relink", "tour_graph", "prune",
                                "list", "orphans", "neighbours"])
async def test_removed_ops_answer_410(no_db, op):
    out = await lambda_entry.run_extraction_event({"op": op, "tour_ids": ["t1"]})
    assert out["status"] == 410 and out["removed"] is True
    assert out["error"] == f"unknown extraction op {op!r}"
    assert "CIS" in out["moved_to"] and out["ops"] == []


@pytest.mark.parametrize("spec", [{"op": "nope"}, {}, None])
async def test_unknown_op_answers_400(no_db, spec):
    out = await lambda_entry.run_extraction_event(spec)
    assert out["status"] == 400 and "removed" not in out and out["error"].startswith("unknown extraction op")
