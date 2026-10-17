"""DB Migrate's own lint on every TripPlanner migration (X1 plan S7, AA-852; review S241).

AA-CIS-Infra ``db/run_migration.py --inspect`` is the lint DB Migrate runs at copy time (Copy Guard,
``check_migration_files.py``) and again before apply (exit 4). Running it here, in the owner PR, means a
tp-014+ file that passes ``migration_lint.py`` but not the runner fails in this repo's CI instead of at
copy or apply time.

Runs only with ``--infra-runner PATH`` (CI job "Migrations": a sparse checkout of AA-CIS-Infra at the
commit pinned in ci.yml; standard library only, run with ``python -I``). The runner applies its X1 role
lint only to a path shaped ``tripplanner/NNN_*.sql`` (``migration_path``): run against
``migrations/014_x.sql`` the dir parses as ``migrations`` and the role lint is skipped without an error.
So every file is copied to ``<tmp>/tripplanner/<name>`` first, and a canary test proves the role lint
is active for that path at the pinned commit.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from migration_lint import NAME_RE, migration_files

REG = ("INSERT INTO shared.schema_versions (version, applied_at, description)\n"
       "VALUES ('tp-{n:03d}', now(), 'x')\nON CONFLICT (version) DO NOTHING;\n")


@pytest.fixture(scope="module")
def runner(request) -> Path:
    path = request.config.getoption("--infra-runner")
    if not path:
        pytest.skip("pass --infra-runner with the path of AA-CIS-Infra db/run_migration.py")
    p = Path(path).resolve()
    assert p.is_file(), f"--infra-runner {path}: no such file"
    return p


def inspect(runner: Path, sql: str, name: str, tmp_path: Path) -> tuple:
    """(exit code, output) of `run_migration.py --inspect <tmp>/tripplanner/<name>`."""
    target = tmp_path / "tripplanner" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sql, encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", str(runner), "--inspect", str(target)],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout + r.stderr).strip()


def test_every_migration_passes_the_db_migrate_lint(runner, tmp_path):
    failures = []
    for f in migration_files():
        if not NAME_RE.match(f.name):
            continue                     # migration_lint.py reports the name
        rc, out = inspect(runner, f.read_text(encoding="utf-8"), f.name, tmp_path)
        if rc != 0:
            failures.append(f"{f.name}: exit {rc}: {out}")
    assert failures == [], "\n".join(failures)


@pytest.mark.parametrize("body, needle", [
    ("DO $$ BEGIN EXECUTE 'SET ROLE ' || 'aa_cis_admin'; END $$;", "dynamic SQL"),
    ("DO $$ BEGIN EXECUTE format('GRANT %I TO tripplanner', 'tp_owner'); END $$;", "dynamic SQL"),
    ("CREATE CONSTRAINT TRIGGER x1_t AFTER INSERT ON tripplanner.sessions DEFERRABLE INITIALLY DEFERRED "
     "FOR EACH ROW EXECUTE FUNCTION tripplanner.f();", "DEFERRABLE"),
    ("SET CONSTRAINTS ALL DEFERRED;", "DEFERRED"),
    ("GRANT tp_owner TO tripplanner;", "role membership"),
    ("SELECT set_config('role', 'aa_cis_admin', true);", "switches the role"),
    ("RESET ROLE;", "RESET ROLE"),
    ("SET LOCAL ROLE tp_owner;", "not allowed in tripplanner/"),
])
def test_the_role_lint_is_active_for_a_tp014_path(runner, tmp_path, body, needle):
    """Canary: the pinned runner refuses each dangerous tp-014-shaped file (exit 1), i.e. the
    tripplanner/ path shape turns its role lint on and the pin has the X1 rules."""
    sql = f"-- Migration tp-014: canary\nBEGIN;\n{body}\n{REG.format(n=14)}COMMIT;\n"
    rc, out = inspect(runner, sql, "014_x1_canary.sql", tmp_path)
    assert rc == 1 and needle in out, out
