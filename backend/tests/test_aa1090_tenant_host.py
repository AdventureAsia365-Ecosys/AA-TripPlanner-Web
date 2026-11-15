"""AA-1090 TripPlanner R8 — an agency's own domain: GET /browse/tenant-host/{host} (backend/browse/tenant_site.py
lookup_host over CIS 429 catalog.v_tenant_tripplanner_v1.custom_domains), offline with a fake pool.

A served tenant's active host answers the same tenant shape as /browse/tenant/{slug}; an unknown host, an unserved
tenant, a badly shaped host (an IP, a port only, upper case is normalised) and a deploy before CIS 429 (42703) are the
same 404; any other read error is a 503 that is never cached. The Postgres check is in test_aa1090_tenant_host_pg.py.
Invented hosts only.
"""
from __future__ import annotations

import json
import uuid

import pytest

from backend.browse import handler as browse_handler
from backend.browse import tenant_site

AGENCY = "4b1d7e57-0000-4000-8000-0000000000a1"


def _site_row(**over):
    row = {"tenant_id": uuid.UUID(AGENCY), "slug": "quillmere-voyages", "name": "Quillmere Voyages", "kind": "agency",
           "in_service": True, "served": True, "tp_launcher": True, "tp_branding": False,
           "theme": '{"accent": "#1a2b3c"}', "locale": "en"}
    row.update(over)
    return row


class SqlError(Exception):
    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class FakePool:
    def __init__(self, row=None, error=None):
        self.row, self.error, self.calls = row, error, []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if self.error:
            raise self.error
        return self.row


@pytest.mark.parametrize("raw,host", [
    ("trip.quillmere.example.invalid", "trip.quillmere.example.invalid"),
    ("Trip.Quillmere.Example.Invalid.", "trip.quillmere.example.invalid"),
    ("trip.quillmere.example.invalid:443", "trip.quillmere.example.invalid"),
    ("[::1]:443", ""),
])
def test_normalise_host(raw, host):
    assert tenant_site.normalise_host(raw) == host


async def test_an_active_host_of_a_served_tenant_answers_the_tenant():
    pool = FakePool(_site_row())
    site = await tenant_site.lookup_host("Trip.Quillmere.Example.Invalid", pool=pool)
    assert site == tenant_site.shape(_site_row())
    ((sql, args),) = pool.calls
    assert sql == tenant_site.HOST_SQL and args == ("trip.quillmere.example.invalid",)
    assert "= ANY (custom_domains)" in sql and "SELECT *" not in sql


@pytest.mark.parametrize("row", [None, _site_row(served=False), _site_row(in_service=False)])
async def test_unknown_or_unserved_is_none(row):
    assert await tenant_site.lookup_host("trip.quillmere.example.invalid", pool=FakePool(row)) is None


@pytest.mark.parametrize("host", ["10.0.0.1", "localhost", "trip_x.example.invalid", "", "a" * 300, "[::1]",
                                  "-trip.example.invalid"])
async def test_a_bad_host_runs_no_sql(host):
    pool = FakePool(_site_row())
    assert await tenant_site.lookup_host(host, pool=pool) is None and pool.calls == []


@pytest.mark.parametrize("state", ["42703", "42P01", "42501"])
async def test_before_cis_428_every_host_is_unknown(state, capsys):
    assert await tenant_site.lookup_host("trip.q.example.invalid", pool=FakePool(error=SqlError(state))) is None
    assert "tenant_host_unreadable" in capsys.readouterr().out


async def test_any_other_error_is_unavailable_and_never_echoes_the_value(capsys):
    with pytest.raises(tenant_site.TenantLookupUnavailable):
        await tenant_site.lookup_host("trip.q.example.invalid", pool=FakePool(error=ConnectionResetError("secret")))
    assert "secret" not in capsys.readouterr().out


async def test_the_route_200_404_503_never_cached():
    ok = await browse_handler.route("GET", "/browse/tenant-host/trip.quillmere.example.invalid", {},
                                    pool=FakePool(_site_row()))
    assert ok["statusCode"] == 200 and ok["headers"]["cache-control"] == "no-store"
    assert json.loads(ok["body"])["tenant"]["slug"] == "quillmere-voyages"
    miss = await browse_handler.route("GET", "/browse/tenant-host/unknown.example.invalid", {}, pool=FakePool(None))
    assert miss["statusCode"] == 404 and miss["headers"]["cache-control"] == "no-store"
    down = await browse_handler.route("GET", "/browse/tenant-host/trip.quillmere.example.invalid", {},
                                      pool=FakePool(error=ConnectionResetError("x")))
    assert down["statusCode"] == 503 and down["headers"]["cache-control"] == "no-store"
    assert browse_handler.route_name("GET", "/browse/tenant-host/trip.quillmere.example.invalid") == "tenant_host"
    # the slug route is not shadowed by the host route, and vice versa
    assert browse_handler.route_name("GET", "/browse/tenant/quillmere-voyages") == "tenant"
