"""The http adapter for Person 1's data service, against recorded-shape responses.

The fixture is invented data in the real service's shapes (see
tests/fixtures/make_voa_fixture.py). No network, no real VOA data.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from agents.builder import rates
from agents.builder.checks import check_pack, eligible_comparables
from orchestrator import data_client
from orchestrator.data_client import NotFound, VoaDataClient, make_data_client
from tests.test_builder import build_pack

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "voa_service_responses.json")
                     .read_text(encoding="utf-8"))["responses"]
KEY = "test-key-not-real"
SUBJECT = "900000001"
SEGMENT = "TEST STREET|E8 1#SCH1"


def replay(request: httpx.Request) -> httpx.Response:
    if request.headers.get("X-API-Key") != KEY:
        return httpx.Response(401, json={"detail": "missing or wrong X-API-Key"})
    path = unquote(request.url.path)
    if path == "/street_tone":
        path += f"?segment_key={request.url.params.get('segment_key')}"
    elif path == "/search_properties":
        path += f"?q={request.url.params.get('q')}"
    if path not in FIXTURE:
        return httpx.Response(404, json={"detail": "not found"})
    return httpx.Response(200, json=FIXTURE[path])


def client(key: str = KEY) -> VoaDataClient:
    http = httpx.Client(base_url="http://voa.test", headers={"X-API-Key": key},
                        transport=httpx.MockTransport(replay))
    return VoaDataClient(http=http)


@pytest.fixture
def voa() -> VoaDataClient:
    return client()


def test_valuation_is_normalised(voa):
    v = voa.get_valuation(SUBJECT)
    assert v["uarn"] == SUBJECT and v["street_key"] == SEGMENT
    assert v["use"] == "SHOP AND PREMISES" and v["use_class"] == "249"
    assert v["floor_area_m2"] == 95                       # summary valuation total area
    assert v["itza"] == 40 + 30 * 0.5 + 20 * 0.25          # the service's own figure
    assert [(z["zone"], z["area_m2"]) for z in v["zones"]] == [("A", 40), ("B", 30), ("C", 20)]


def test_comparables_same_segment_nearest_first(voa):
    comps = voa.comparables(SUBJECT, max_distance=200, same_use=True)
    assert [c["uarn"] for c in comps] == ["900000002", "900000003", "900000004", "900000005",
                                          "900000006"]
    assert "900000007" not in {c["uarn"] for c in comps}   # another segment
    assert "900000008" not in {c["uarn"] for c in comps}   # postcode not located
    assert comps[0]["distance_m"] == 0                      # same postcode centre


def test_street_tone_uses_the_team_spread(voa):
    t = voa.street_tone(SEGMENT)
    assert t["street_key"] == SEGMENT and t["count"] == 7
    assert t["spread"] == pytest.approx(max(1.4826 * t["mad"], 0.05 * t["median_zone_a"]))


def test_eligible_set_and_a_good_pack_pass(voa):
    eligible = [c["uarn"] for c in eligible_comparables(SUBJECT, voa)]
    assert eligible == ["900000002", "900000003", "900000004", "900000005"]   # 006 too big
    pack = build_pack(voa, SUBJECT)
    assert check_pack(pack, voa) == []
    subject = voa.get_valuation(SUBJECT)
    assert rates.use_of(subject) == "249"                   # retail multiplier for real shops
    assert pack["annual_saving"] == rates.annual_saving(subject["rv"], pack["proposed_rv"], "shop")


def test_unknown_uarn_and_bad_key(voa):
    with pytest.raises(NotFound):
        voa.get_valuation("123")
    with pytest.raises(RuntimeError) as e:
        client(key="wrong").get_valuation(SUBJECT)
    assert "401" in str(e.value) and "wrong" not in str(e.value)   # the key never appears


def test_search_filters_use(voa):
    rows = voa.search_properties(street="TEST STREET", use="shop and premises")
    assert rows and all(r["use"] == "SHOP AND PREMISES" for r in rows)


def test_backend_setting(monkeypatch):
    monkeypatch.setenv("DATA_SERVICE_URL", "http://voa.test")
    monkeypatch.setenv("DATA_SERVICE_KEY", KEY)
    monkeypatch.setenv("DATA_BACKEND", "http")
    assert isinstance(make_data_client(), VoaDataClient)
    monkeypatch.setenv("DATA_BACKEND", "mock")
    assert type(make_data_client()) is data_client.DataClient
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    with pytest.raises(ValueError):
        make_data_client()
    monkeypatch.setenv("DATA_BACKEND", "http")
    monkeypatch.delenv("DATA_SERVICE_KEY")
    with pytest.raises(RuntimeError):
        make_data_client()
