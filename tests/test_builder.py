"""Builder checks and handlers against the mock data service, in-process. No API calls."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agents.builder import rates
from agents.builder.checks import check_pack, itza
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


def good_pack(data: DataClient) -> dict:
    """A correct pack for outlier 1001008 (137 Mare Street, ZA £550)."""
    s = data.get_valuation("1001008")
    tone = data.street_tone("mare_street")
    pool = {c["uarn"]: c for c in data.comparables("1001008", same_use=True)}
    picks = ["1001009", "1001006", "1001011", "1001004"]   # 410, 400, 445, 390
    comps = [{"uarn": u, "zone_a_price": pool[u]["zone_a_price"],
              "distance_m": pool[u]["distance_m"], "use": pool[u]["use"]} for u in picks]
    proposed = round(itza(s) * 405)                         # median of the four
    return {
        "case_id": "c_0000", "version": "builder-v1", "uarn": "1001008",
        "address": s["address"], "current_rv": s["rv"], "proposed_rv": proposed,
        "comparables": comps,
        "calc": {"zone_a_price": s["zone_a_price"], "street_median": tone["median_zone_a"],
                 "street_spread": tone["spread_mad"]},
        "annual_saving": rates.annual_saving(s["rv"], proposed, "shop"),
        "argument": "Four shops of similar size within 90 m on Mare Street are valued at "
                    "£390-£445 per m2 in Zone A; this shop is valued at £550.",
    }


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
    result = h.handle("submit_pack", args)
    assert result.startswith("ACCEPTED. Pack c_0001")
    row = store.get_pack("c_0001")
    assert row["pack"]["case_id"] == "c_0001" and row["version"] == "builder-v1"
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
def columns(sql: str) -> dict[str, list[str]]:
    tables = {}
    for name, body in re.findall(r"create table if not exists (\w+) \((.*?)\n\);", sql, re.S):
        tables[name] = [line.split()[0] for line in body.strip().splitlines()
                        if line.strip() and not line.strip().startswith("--")]
    return tables


def test_sqlite_schema_matches_contract():
    contract = columns((ROOT / "contracts" / "schema.sql").read_text())
    local = columns((ROOT / "orchestrator" / "sqlite_schema.sql").read_text())
    assert contract == local
