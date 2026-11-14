"""AA-1096 — settings-lint, the CI guard of TripPlanner's admin-editable settings (root thresholds audit
docs/audits/2026-11-13-S279-session/thresholds-audit/ section 5.4; tech lead S279: a threshold, limit, cap or policy
value is a setting the administrator edits on the CIS admin page Platform › Settings, never a code literal or an env
var alone). The TripPlanner copy of AA-CIS-App tests/unit/test_s279_settings_lint.py; the CI job "Settings Lint"
(.github/workflows/ci.yml) runs this file on every pull request.

1. **No new policy constant in the backend.** In every module of ``backend/`` (:data:`SCANNED`, minus the tests, the
   vendored CIS copies ``aa_llm`` and ``shared/identity_verifier.py``, which AA-CIS-App lints, and the CI helper
   scripts), a module-level UPPER_CASE constant
   whose name looks like policy (``*_MAX``, ``MAX_*``, ``*_CAP``, ``*_LIMIT``, ``*_DAYS``, ``*_HOURS``, ``*_PCT``,
   ``*_THRESHOLD``, ``*_USD``, ``*_ATTEMPTS``, ``*_RETRIES``, ``*_TTL``, ...) bound to a number literal, or to a number
   read from the environment (``int(_get(...))``, ``float(os.environ.get(...))``), fails unless:
   - its line carries ``# technical-constant: <reason>`` (an engineering constant: a timeout, a page size, a protocol
     bound), or
   - it is the fallback of a registry setting (``backend/config.py``, the ``attr`` of a
     ``backend.shared.settings.SETTINGS`` entry: the CIS registry row wins, the constant only serves while the view
     cannot be read), or
   - it is in the baseline of the constants that were there on 14/11/2026
     (``fixtures/aa1096_settings_lint_baseline.json``, a list that only shrinks).
   Fix: add the value to the registry (a CIS migration seed row with app ``tripplanner`` + a ``SETTINGS`` entry here,
   lane L5 pattern), or mark a true engineering constant.
2. **The baseline only shrinks.** A baseline entry whose constant is gone (or is now a setting or marked) fails until
   it is removed from the file.

The other direction (every registry key has a reader, every reader key exists) is ``test_s279_settings.py``.
No DB, no network. Rewrite the baseline only to remove entries: ``python -m backend.tests.test_aa1096_settings_lint``
from the repo root.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

from backend.shared import settings

BACKEND = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).resolve().parent / "fixtures" / "aa1096_settings_lint_baseline.json"

SCANNED = ("",)                                      # the whole backend package
# tests; the vendored CIS byte copies (aa_llm, the identity verifier: linted in AA-CIS-App, their owner, and
# compared byte for byte by test_vendored_copies.py, so a marker here would break the copy); CI helper scripts.
EXCLUDED = ("tests/", "aa_llm/", "shared/identity_verifier.py", "scripts/")
POLICY_NAME = re.compile(
    r"(^|_)(MAX|MIN|CAP|LIMIT|LIMITS|DAYS|HOURS|PCT|PERCENT|THRESHOLD|THRESHOLDS|USD|ATTEMPTS|RETRIES|QUOTA|BUDGET|"
    r"FLOOR|CEILING|TTL)(_|$)"
)
MARK = "# technical-constant:"
ENV_NAMES = ("_get", "getenv", "environ")            # config._get(name, default) is the env reader of config.py
ENV_ATTRS = ("get", "getenv", "environ")


def _files() -> list[Path]:
    out: list[Path] = []
    for rel in SCANNED:
        p = BACKEND / rel
        out.extend([p] if p.suffix == ".py" else sorted(p.rglob("*.py")))
    keep = []
    for p in out:
        rel = p.relative_to(BACKEND).as_posix()
        if "__pycache__" in p.parts or rel.startswith(EXCLUDED):
            continue
        keep.append(p)
    return keep


def _is_number(node: ast.AST | None) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        node = node.operand
    return isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool)


def _env_read(node: ast.AST | None) -> bool:
    """int(_get(...)) / float(os.environ.get(...)) / int(os.getenv(...)) and the like: a numeric env read."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("int", "float")):
        return False
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in ENV_ATTRS:
            return True
        if isinstance(n, ast.Name) and n.id in ENV_NAMES:
            return True
    return False


def setting_fallbacks() -> set[str]:
    """``config.py:<attr>`` of every registry setting: the fallback the CIS row overrides (lint rule 1, exception 2)."""
    return {f"config.py:{s.attr}" for s in settings.SETTINGS.values()}


def findings() -> list[str]:
    """Every policy-looking numeric constant or numeric env read at module level of a scanned file, unmarked and not a
    registry fallback, as ``<path under backend/>:<NAME>``."""
    allowed = setting_fallbacks()
    out = []
    for path in _files():
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        rel = path.relative_to(BACKEND).as_posix()
        for node in ast.parse(text).body:
            targets: list[ast.expr] = []
            value = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            for t in targets:
                if not (isinstance(t, ast.Name) and t.id.isupper() and POLICY_NAME.search(t.id)):
                    continue
                if not (_is_number(value) or _env_read(value)):
                    continue
                if MARK in lines[node.lineno - 1] or f"{rel}:{t.id}" in allowed:
                    continue
                out.append(f"{rel}:{t.id}")
    return sorted(set(out))


def _baseline() -> list[str]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))["constants"]


def test_no_new_policy_constant_in_the_backend() -> None:
    new = sorted(set(findings()) - set(_baseline()))
    assert not new, (
        "policy-looking constants with no setting (make each a registry setting, lane L5 pattern: a CIS seed row with "
        f"app 'tripplanner' + backend/shared/settings.py SETTINGS, or mark '{MARK} <reason>'): {new}"
    )


def test_the_baseline_only_shrinks() -> None:
    stale = sorted(set(_baseline()) - set(findings()))
    assert not stale, (
        f"remove these from {BASELINE.name} (python -m backend.tests.test_aa1096_settings_lint): {stale}"
    )


def test_the_baseline_is_sorted_and_unique() -> None:
    base = _baseline()
    assert base == sorted(set(base))


def test_every_setting_fallback_exists_in_config() -> None:
    config_text = (BACKEND / "config.py").read_text(encoding="utf-8")
    for s in settings.SETTINGS.values():
        assert re.search(rf"^{s.attr}\b", config_text, re.M), f"{s.key}: config.{s.attr} is gone"


def test_the_lint_catches_a_new_cap(tmp_path: Path, monkeypatch) -> None:
    mod = tmp_path / "zz_lint"
    mod.mkdir(parents=True)
    (mod / "m.py").write_text(
        "import os\nRUN_CAP_USD = 5.0\nPOOL_SIZE = 3\nWAIT_DAYS = 2  # technical-constant: x\n"
        'RETRY_MAX = int(os.environ.get("ZZ", "3"))\nHOLD_HOURS = int(_get("ZZ_H", "48"))\nNAME_MAX = "x"\n'
        "MAX_STOPS: int = -1\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("SKIP_MAX = 1\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "BACKEND", tmp_path)
    assert findings() == [
        "zz_lint/m.py:HOLD_HOURS", "zz_lint/m.py:MAX_STOPS", "zz_lint/m.py:RETRY_MAX", "zz_lint/m.py:RUN_CAP_USD",
    ]


if __name__ == "__main__":  # rewrite the baseline to the current findings (only to remove entries)
    current = findings()
    BASELINE.write_text(
        json.dumps(
            {
                "_doc": "AA-1096 settings-lint baseline: policy-looking constants of the TripPlanner backend on "
                        "14/11/2026 that are not registry settings yet and not marked technical. Only shrinks.",
                "constants": current,
            },
            indent=1,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {BASELINE.relative_to(BACKEND)} ({len(current)} entries)")
