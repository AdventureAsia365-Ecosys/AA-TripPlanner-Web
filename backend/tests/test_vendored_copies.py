"""AA-642 S8: the vendored AI gateway core (backend/aa_llm, root ADR 0009 decision 13) is an untouched copy.

The offline integrity check is required here (pytest runs in CI): the copy matches its MANIFEST.json, the lock names
that manifest, the canonical repo / path, this destination and a CIS commit. Provenance and the identity_verifier.py
compare need the network (scripts/check_vendored_copies.py without --offline); their logic is tested here with
fakes. Never edit backend/aa_llm: change AA-CIS-App shared/aa_llm, merge, then sync.
"""
from __future__ import annotations

import importlib.util
import io
import json
import shutil
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_vendored_copies", BACKEND / "scripts" / "check_vendored_copies.py")
cvc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cvc)


def test_the_aa_llm_copy_is_intact():
    assert cvc.integrity_problems() == []


def test_the_core_imports_under_this_package_name():
    from backend.aa_llm import CORE_VERSION, budget, contract, errors, ledger, ports, pricing

    assert CORE_VERSION and errors.classify("ThrottlingException") == "throttled"
    assert ledger.insert_sql().startswith("INSERT INTO shared.llm_call_log")
    assert budget.decide(None, 1.0).reason == "spend_unreadable"
    assert contract.GATEWAY_CONFIG_VIEW == "catalog.v_llm_gateway_config_v1"
    assert pricing.catalog_rates(None, 1.0) is None and ports.LedgerSink


@pytest.fixture
def copy(tmp_path):
    folder = tmp_path / "aa_llm"
    shutil.copytree(cvc.CORE_DIR, folder, ignore=shutil.ignore_patterns("__pycache__"))
    lock = tmp_path / "aa_llm.lock.json"
    shutil.copy(cvc.LOCK, lock)
    return folder, lock


def test_an_edited_copy_fails(copy):
    folder, lock = copy
    with open(folder / "ledger.py", "a", encoding="utf-8") as fh:
        fh.write("# local edit\n")
    assert any("ledger.py: sha256 differs" in p for p in cvc.integrity_problems(folder, lock))


def test_an_added_file_fails(copy):
    folder, lock = copy
    (folder / "extra.py").write_text("", encoding="utf-8")
    assert any("extra.py: not listed" in p for p in cvc.integrity_problems(folder, lock))


def _rewrite_lock(lock: Path, **changes):
    data = json.loads(lock.read_text(encoding="utf-8"))
    data.update(changes)
    lock.write_text(json.dumps(data), encoding="utf-8")


def test_lock_shape(copy, monkeypatch):
    folder, lock = copy
    _rewrite_lock(lock, dest="api/app/aa_llm", ref="local", canonical={"repo": "x/y", "path": "z"})
    found = cvc.integrity_problems(folder, lock)
    assert any("dest is" in p for p in found) and any("canonical is" in p for p in found)
    assert any("is not an AA-CIS-App commit sha" in p for p in found)
    monkeypatch.setenv(cvc.LOCAL_OK_ENV, "1")
    assert not any("commit sha" in p for p in cvc.integrity_problems(folder, lock))


def test_lock_must_name_this_manifest(copy):
    folder, lock = copy
    _rewrite_lock(lock, manifest_sha256="0" * 64)
    assert any("manifest_sha256 does not match" in p for p in cvc.integrity_problems(folder, lock))


def test_missing_lock(copy):
    folder, lock = copy
    lock.unlink()
    assert any("is missing" in p for p in cvc.integrity_problems(folder, lock))


def test_provenance_compares_with_the_lock_commit(copy):
    folder, lock = copy
    ref = "a" * 40
    _rewrite_lock(lock, ref=ref)
    files = {f"shared/aa_llm/{p.name}": p.read_bytes() for p in folder.iterdir() if p.is_file()}
    asked = []

    def fetch(path, at):
        asked.append(at)
        return files.get(path)

    assert cvc.provenance_problems(fetch, lambda r: True, folder, lock) == []
    assert set(asked) == {ref}
    files["shared/aa_llm/budget.py"] = b"# other bytes\n"
    found = cvc.provenance_problems(fetch, lambda r: False, folder, lock)
    assert any("is not on AA-CIS-App main" in p for p in found)
    assert any("budget.py: differs" in p for p in found)


def test_provenance_reports_a_fetch_error(copy):
    folder, lock = copy
    _rewrite_lock(lock, ref="b" * 40)

    def fetch(path, at):
        raise cvc.FetchError("HTTP 503")

    assert any("cannot compare" in p for p in cvc.provenance_problems(fetch, lambda r: True, folder, lock))


def test_identity_verifier_is_compared_with_cis_main():
    local = (BACKEND / "shared" / "identity_verifier.py").read_bytes()
    assert cvc.main_copy_problems(lambda path, ref: local if ref == "main" else None) == []
    assert cvc.main_copy_problems(lambda path, ref: b"other") != []
    assert cvc.main_copy_problems(lambda path, ref: None) != []


def test_offline_main_prints_the_result():
    out = io.StringIO()
    assert cvc.main(["--offline"], out=out) == 0
    assert "0 problem(s) (offline)" in out.getvalue()
