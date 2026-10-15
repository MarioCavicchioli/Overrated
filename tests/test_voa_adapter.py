"""The http adapter for Person 1's data service, against recorded-shape responses.

The fixture is invented data in the real service's shapes (see
tests/fixtures/make_voa_fixture.py). No network, no real VOA data.
"""

from __future__ import annotations

import json
import statistics
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


BLOCK_SUBJECT = "900000101"     # BLOCK STREET: built like 221-233 Old Street
BLOCK_NEIGHBOURS = ["900000102", "900000103", "900000104", "900000105"]   # all £1,030, 0 m


def test_old_street_block_neighbours_are_the_eligible_set(voa):
    eligible = eligible_comparables(BLOCK_SUBJECT, voa)
    assert [c["uarn"] for c in eligible][:4] == BLOCK_NEIGHBOURS
    for c in eligible[:4]:
        assert c["spreads_from_median"] > 2          # far above the segment...
        assert c["spreads_from_subject"] == 0        # ...but priced exactly like the subject
        assert not c["may_exclude"]


def test_old_street_block_cannot_be_excluded_away(voa):
    """Leaving out three neighbours priced like the subject is rejected: at most one."""
    pack = build_pack(voa, BLOCK_SUBJECT, exclude=tuple(BLOCK_NEIGHBOURS[:3]))
    errors = check_pack(pack, voa)
    assert any("at most 1 eligible comparable" in e for e in errors)


def test_neighbour_priced_like_the_subject_is_never_excluded(voa):
    """Seven Sisters Road c_0001: the builder left out its twin at 0 m and the same price.
    Even one such exclusion is rejected, and with the twin kept there's no reduction."""
    pack = build_pack(voa, BLOCK_SUBJECT, exclude=(BLOCK_NEIGHBOURS[0],))
    errors = check_pack(pack, voa)
    assert any("priced like the candidate can never be excluded" in e for e in errors)
    honest = build_pack(voa, BLOCK_SUBJECT)
    assert honest["proposed_rv"] >= honest["current_rv"]
    assert any("proposed_rv must be below current_rv" in e for e in check_pack(honest, voa))


def test_no_case_when_segment_mad_is_zero(voa):
    """TWIN STREET, like Seven Sisters Road c_0101: five shops, MAD 0, spread = 5% floor."""
    tone = voa.street_tone("TWIN STREET|N4 1#SCH5")
    assert tone["mad"] == 0 and tone["spread"] == pytest.approx(0.05 * tone["median_zone_a"])
    errors = check_pack(build_pack(voa, "900000201"), voa)
    assert any("MAD 0 over 5 shops" in e and "call no_case" in e for e in errors)


def test_no_case_when_the_lower_price_is_only_another_buildings(voa):
    """PAIR STREET: a real spread (MAD 15), but every cheaper comparable is in the next
    postcode while the subject's twin in its own postcode is at the same £300."""
    assert voa.street_tone("PAIR STREET|N5 1#SCH6")["mad"] == 15
    pack = build_pack(voa, "900000301")
    assert pack["proposed_rv"] < pack["current_rv"]           # the numbers alone would pass
    errors = check_pack(pack, voa)
    assert any("different postcode" in e and "900000302" in e for e in errors)
    assert not any("MAD 0" in e for e in errors)


ARCADE_SUBJECT = "900000401"   # £12,000 of value outside its zones
GAMING_CENTRE = "900000402"    # nearest, same postcode, same shop code 249


def test_adult_gaming_centre_is_never_a_shop_comparable(voa):
    nearest = voa.comparables(ARCADE_SUBJECT, max_distance=200, same_use=True)[0]
    assert nearest["uarn"] == GAMING_CENTRE and nearest["use_class"] == "249"   # code alone passes
    eligible = [c["uarn"] for c in eligible_comparables(ARCADE_SUBJECT, voa)]
    assert GAMING_CENTRE not in eligible
    assert {"900000404", "900000405"} <= set(eligible)      # "(DABW)" and plain "SHOP" still count
    pack = build_pack(voa, ARCADE_SUBJECT)
    gc = voa.get_valuation(GAMING_CENTRE)
    pack["comparables"][0] = {"uarn": GAMING_CENTRE, "zone_a_price": gc["zone_a_price"],
                              "distance_m": 0, "use": gc["use"]}
    assert any("only same-use comparables" in e for e in check_pack(pack, voa))


def test_proposed_value_keeps_the_value_outside_the_zones(voa):
    """rv - ITZA x (old price - new price), not ITZA x new price."""
    s = voa.get_valuation(ARCADE_SUBJECT)
    good = build_pack(voa, ARCADE_SUBJECT)
    assert check_pack(good, voa) == []
    median = statistics.median(c["zone_a_price"] for c in good["comparables"])
    assert good["proposed_rv"] == round(s["rv"] - s["itza"] * (s["zone_a_price"] - median))
    old_way = round(s["itza"] * median)
    assert good["proposed_rv"] - old_way == pytest.approx(12000, abs=100)   # the extras kept
    bad = {**good, "proposed_rv": old_way,
           "annual_saving": rates.annual_saving(s["rv"], old_way, rates.use_of(s))}
    errors = check_pack(bad, voa)
    assert any("proposed_rv should be" in e and "drops the value outside the zones" in e
               for e in errors)


def test_unknown_uarn_and_bad_key(voa):
    with pytest.raises(NotFound):
        voa.get_valuation("123")
    with pytest.raises(RuntimeError) as e:
        client(key="wrong").get_valuation(SUBJECT)
    assert "401" in str(e.value) and "wrong" not in str(e.value)   # the key never appears


def test_search_filters_use(voa):
    rows = voa.search_properties(street="TEST STREET", use="shop and premises")
    assert rows and all("SHOP AND PREMISES" in r["use"] for r in rows)   # contains, case-blind
    assert "900000003" not in {r["uarn"] for r in rows}                  # plain "SHOP" left out


MALICIOUS = ["../admin", "..", ".", "9000/../../scan", "1?ba_code=5360&limit=5000",
             "1#frag", "%2e%2e%2fadmin", "1/2"]


@pytest.mark.parametrize("uarn", MALICIOUS)
@pytest.mark.parametrize("backend", ["mock", "http"])
def test_uarn_cannot_leave_its_path_segment(uarn, backend):
    """An agent-supplied UARN stays one encoded segment under its endpoint: it can't
    reach another endpoint (e.g. /scan, bulk VOA data) or add a query string."""
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404, json={"detail": "not found"})

    http = httpx.Client(base_url="http://data.test", headers={"X-API-Key": KEY},
                        transport=httpx.MockTransport(capture))
    data = data_client.DataClient(http=http) if backend == "mock" else VoaDataClient(http=http)
    for call, prefix in ((data.get_valuation, b"/get_valuation/"),
                         (data.comparables, b"/comparables/")):
        with pytest.raises(NotFound):
            call(uarn)
        raw = seen[-1].url.raw_path
        path, _, query = raw.partition(b"?")
        assert path.startswith(prefix), raw
        rest = path[len(prefix):]
        assert rest and b"/" not in rest and b"." not in rest, raw     # one segment, no dots
        assert b"ba_code" not in query and b"limit=5000" not in query, raw
        assert unquote(rest.decode()) == uarn                           # the server sees it as-is


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
