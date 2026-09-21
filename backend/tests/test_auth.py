"""Unit tests for the edge shared-secret gate (backend/shared/auth.py)."""
from __future__ import annotations

from backend import config
from backend.shared import auth


def _event(headers: dict | None = None) -> dict:
    return {"headers": headers or {}}


def test_disabled_when_no_key_configured(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "")
    assert auth.check_event(_event({})) is None
    assert auth.check_event(_event({"x-tripplanner-key": "anything"})) is None


def test_allows_correct_key(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "s3cret")
    assert auth.check_event(_event({"x-tripplanner-key": "s3cret"})) is None


def test_allows_correct_key_case_insensitive_header(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "s3cret")
    # API Gateway lowercases, but be robust to a differently-cased header
    assert auth.check_event(_event({"X-TripPlanner-Key": "s3cret"})) is None


def test_blocks_missing_key(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "s3cret")
    resp = auth.check_event(_event({}))
    assert resp is not None
    assert resp["statusCode"] == 401


def test_blocks_wrong_key(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "s3cret")
    resp = auth.check_event(_event({"x-tripplanner-key": "nope"}))
    assert resp is not None
    assert resp["statusCode"] == 401
