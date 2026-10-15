"""Builder checks and handlers against the mock data service, in-process. No API calls."""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agents.builder import rates
from agents.builder.checks import check_pack, eligible_comparables, itza
from agents.builder.handlers import BuilderHandlers
from data.mock.main import app
from orchestrator.data_client import DataClient
from orchestrator.store import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def data() -> DataClient:
    client = DataClient("http://mock")
    client.http = TestClient(app)
    return client


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "t.db", tmp_path / "pdfs")


def build_pack(data: DataClient, uarn: str, exclude: tuple[str, ...] = ()) -> dict:
    """A correct pack: every eligible comparable used, except those in `exclude`."""
    s = data.get_valuation(uarn)
    tone = data.street_tone(s["street_key"])
    eligible = eligible_comparables(uarn, data)
    comps = [{k: c[k] for k in ("uarn", "zone_a_price", "distance_m", "use")}
             for c in eligible if c["uarn"] not in exclude]
    exclusions = [{"uarn": c["uarn"], "zone_a_price": c["zone_a_price"],
                   "distance_m": c["distance_m"],
                   "reason": f"{c['spreads_from_median']} spreads above the street median"}
                  for c in eligible if c["uarn"] in exclude]
    proposed = round(itza(s) * statistics.median(c["zone_a_price"] for c in comps))
    pack = {
        "case_id": "c_0000", "version": "builder-v1", "uarn": uarn,
        "address": s["address"], "current_rv": s["rv"], "proposed_rv": proposed,
        "comparables": comps,
        "calc": {"zone_a_price": s["zone_a_price"], "street_median": tone["median_zone_a"],
                 "street_spread": tone["spread_mad"]},
        "annual_saving": rates.annual_saving(s["rv"], proposed, s["use"]),
        "argument": "Similar shops nearby on the same street are valued at a lower Zone A "
                    "price per m2 than this shop.",
    }
    if exclusions:
        pack["exclusions"] = exclusions
    return pack


def good_pack(data: DataClient) -> dict:
    """A correct pack for outlier 1001008 (137 Mare Street, ZA £550): all six eligible."""
    return build_pack(data, "1001008")


# -- rates ---------------------------------------------------------------
def test_saving_uses_band_of_each_value():
    # 52,000 -> 50,000 crosses £51k: whole bill drops from 43.0p to 38.2p.
    assert rates.annual_saving(52_000, 50_000, "shop") == 52_000 * 0.430 - 50_000 * 0.382
    assert rates.annual_saving(40_000, 30_000, "shop") == round(10_000 * 0.382)
    assert rates.annual_saving(40_000, 30_000, "office") == round(10_000 * 0.432)


# -- checks --------------------------------------------------------------
def test_good_pack_passes(data):
    assert check_pack(good_pack(data), data) == []


def test_invented_comparable_rejected(data):
    pack = good_pack(data)
    pack["comparables"][0]["uarn"] = "9999999"
    errors = check_pack(pack, data)
    assert any("9999999" in e and "does not exist" in e for e in errors)


def test_different_use_rejected(data):
    pack = good_pack(data)
    cafe = data.get_valuation("1001007")   # cafe at 135 Mare Street
    pack["comparables"][0] = {"uarn": "1001007", "zone_a_price": cafe["zone_a_price"],
                              "distance_m": 25.0, "use": "cafe"}
    assert any("only same-use" in e for e in check_pack(pack, data))


def test_wrong_figures_rejected(data):
    pack = good_pack(data)
    pack["current_rv"] += 1000
    pack["annual_saving"] += 50
    pack["comparables"][1]["zone_a_price"] = 300
    errors = check_pack(pack, data)
    assert any("current_rv" in e for e in errors)
    assert any("annual_saving should be" in e for e in errors)
    assert any("zone_a_price should be 400" in e for e in errors)


def test_proposed_rv_outside_comparable_range_rejected(data):
    pack = good_pack(data)
    pack["proposed_rv"] = round(itza(data.get_valuation("1001008")) * 300)
    pack["annual_saving"] = rates.annual_saving(pack["current_rv"], pack["proposed_rv"], "shop")
    assert any("outside the comparables' range" in e for e in check_pack(pack, data))


def test_eligible_set_for_outlier(data):
    eligible = eligible_comparables("1001008", data)
    assert [c["uarn"] for c in eligible] == \
        ["1001009", "1001006", "1001011", "1001004", "1001012", "1001003"]
    assert not any(c["may_exclude"] for c in eligible)


def test_cherry_picked_fixture_rejected(data):
    """c_0001 from the first live run left out 1001011, 1001012 and 1001003."""
    pack = json.loads((ROOT / "tests" / "fixtures" / "pack_c_0001_cherry_picked.json")
                      .read_text(encoding="utf-8"))["pack"]
    errors = check_pack(pack, data)
    for uarn in ("1001011", "1001012", "1001003"):
        assert any(f"eligible comparable {uarn}" in e and "is missing" in e for e in errors)
    assert any("annual_saving should be 3696" in e for e in errors)


def test_exclusion_beyond_two_spreads_allowed(data):
    # Borderline 1001012: the £550 outlier 1001008 is 5.3 spreads up, so it may go.
    assert check_pack(build_pack(data, "1001012", exclude=("1001008",)), data) == []


def test_exclusion_within_two_spreads_rejected(data):
    pack = build_pack(data, "1001008", exclude=("1001011",))
    assert any("only beyond 2 may be excluded" in e for e in check_pack(pack, data))


def test_unlisted_omission_rejected(data):
    pack = build_pack(data, "1001012")
    pack["comparables"] = [c for c in pack["comparables"] if c["uarn"] != "1001008"]
    assert any("eligible comparable 1001008" in e for e in check_pack(pack, data))


def test_schema_errors_reported(data):
    pack = good_pack(data)
    pack["comparables"] = pack["comparables"][:2]
    pack["extra"] = 1
    errors = check_pack(pack, data)
    assert errors and all(e.startswith("schema:") for e in errors)


# -- handlers ------------------------------------------------------------
def seed(store, data, uarn):
    v = data.get_valuation(uarn)
    store.upsert_candidate({"uarn": uarn, "address": v["address"], "status": "building"})


def test_submit_writes_pack_pdf_and_events(store, data):
    seed(store, data, "1001008")
    h = BuilderHandlers(store, data)
    args = {k: v for k, v in good_pack(data).items() if k not in ("case_id", "version")}
    expected_saving = args.pop("annual_saving")
    args["annual_saving"] = 1          # the model's figure is ignored
    result = h.handle("submit_pack", args)
    assert result.startswith("ACCEPTED. Pack c_0001")
    row = store.get_pack("c_0001")
    assert row["pack"]["case_id"] == "c_0001" and row["version"] == "builder-v1"
    assert row["annual_saving"] == row["pack"]["annual_saving"] == expected_saving
    assert Path(row["pdf_url"].removeprefix("file:///")).exists()
    assert store.get_candidate("1001008")["status"] == "packed"
    assert [e["kind"] for e in store.list_events()] == ["pack_written"]


def test_rejected_pack_writes_nothing(store, data):
    seed(store, data, "1001008")
    h = BuilderHandlers(store, data)
    args = {k: v for k, v in good_pack(data).items() if k not in ("case_id", "version")}
    args["comparables"][0]["uarn"] = "9999999"
    for attempt in (1, 2, 3):
        assert h.handle("submit_pack", args).startswith("REJECTED.")
    assert "no attempts left" in h.handle("submit_pack", args)
    assert store.get_pack("c_0001") is None
    assert store.get_candidate("1001008")["status"] == "building"


def test_no_case_sets_its_own_status(store, data):
    seed(store, data, "1003007")
    h = BuilderHandlers(store, data)
    assert h.handle("no_case", {"uarn": "1003007", "reason": "No other restaurants."}) \
        .startswith("Recorded")
    assert store.get_candidate("1003007")["status"] == "no_case"


def test_data_results_are_wrapped_as_data(store, data):
    out = BuilderHandlers(store, data).handle("street_tone", {"street_key": "mare_street"})
    assert out.startswith('<data_service_result tool="street_tone">')
    assert out.rstrip().endswith("</data_service_result>")


# -- store matches the contract -----------------------------------------
def test_sqlite_schema_matches_contract(store):
    """The local SQLite tables are built from contracts/schema.sql, PENDING columns included."""
    from orchestrator import contract
    expected = {t: [c.name for c in cols] for t, cols in contract.tables().items()}
    sql = (ROOT / "contracts" / "schema.sql").read_text()
    assert set(expected) == set(re.findall(r"create table if not exists (\w+)", sql))
    assert "case_ref" in expected["packs"] and "pack" in expected["packs"]
    local = {t: [r[1] for r in store.db.execute(f"pragma table_info({t})")] for t in expected}
    assert local == expected
