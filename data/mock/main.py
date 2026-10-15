"""Mock data service – stands in until the real DuckDB / Parquet service is ready.

Endpoints mirror the real service:
  GET /search_properties?street=&use=&postcode=&limit=50
  GET /get_valuation/{uarn}
  GET /comparables/{uarn}?max_distance=200&same_use=true&limit=50
  GET /street_tone/{street_key}

List endpoints take limit (default 50, max 200). Every request is cut off
after REQUEST_TIMEOUT_S seconds (default 5) with a 504.

Run:  uvicorn data.mock.main:app --port 8100
"""

from __future__ import annotations

import asyncio
import os
import statistics

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Overrated Mock Data Service", version="0.1.0")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
REQUEST_TIMEOUT_S = float(os.getenv("REQUEST_TIMEOUT_S", "5"))


@app.middleware("http")
async def _timeout(request: Request, call_next):
    try:
        return await asyncio.wait_for(call_next(request), timeout=REQUEST_TIMEOUT_S)
    except asyncio.TimeoutError:
        return JSONResponse({"detail": "request timed out"}, status_code=504)

# ── zoning helpers ──────────────────────────────────────────────

ZONE_MULT = {"A": 1.0, "B": 0.5, "C": 0.25, "D": 0.125}


def _rv(za_price: float, zones: list[dict]) -> int:
    return round(
        sum(za_price * ZONE_MULT[z["zone"]] * z["area_m2"] for z in zones)
    )


# ── seed data: ~30 Hackney shops on 3 streets ──────────────────
#
# Each entry stores raw fields; rv is computed from zone_a_price + zones.
# position_m = metres from an arbitrary origin on the street (for distance).
#
# Outliers are tagged in comments.
# "weak" = the restaurant on Broadway Market whose only nearby comps
#          are a different use (shops / cafes), so the case is flimsy.

_RAW: list[dict] = [
    # ── Mare Street (E8 3RH) ── median ZA ≈ 405, MAD ≈ 28 ──
    {"uarn": "1001001", "address": "123 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "shop",
     "zone_a_price": 355,
     "zones": [{"zone": "A", "area_m2": 40}, {"zone": "B", "area_m2": 30}],
     "position_m": 0},
    {"uarn": "1001002", "address": "125 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "cafe",
     "zone_a_price": 370,
     "zones": [{"zone": "A", "area_m2": 35}, {"zone": "B", "area_m2": 25}],
     "position_m": 20},
    {"uarn": "1001003", "address": "127 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "shop",
     "zone_a_price": 420,
     "zones": [{"zone": "A", "area_m2": 45}, {"zone": "B", "area_m2": 30},
               {"zone": "C", "area_m2": 20}],
     "position_m": 40},
    {"uarn": "1001004", "address": "129 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "shop",
     "zone_a_price": 390,
     "zones": [{"zone": "A", "area_m2": 38}, {"zone": "B", "area_m2": 28}],
     "position_m": 65},
    {"uarn": "1001005", "address": "131 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "cafe",
     "zone_a_price": 395,
     "zones": [{"zone": "A", "area_m2": 42}, {"zone": "B", "area_m2": 32}],
     "position_m": 85},
    {"uarn": "1001006", "address": "133 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "shop",
     "zone_a_price": 400,
     "zones": [{"zone": "A", "area_m2": 36}, {"zone": "B", "area_m2": 26}],
     "position_m": 110},
    {"uarn": "1001007", "address": "135 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "cafe",
     "zone_a_price": 435,
     "zones": [{"zone": "A", "area_m2": 50}, {"zone": "B", "area_m2": 35}],
     "position_m": 130},
    # ▸ OUTLIER 1 – ZA £550 vs street median ~£405 (5.3 MADs)
    {"uarn": "1001008", "address": "137 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "shop",
     "zone_a_price": 550,
     "zones": [{"zone": "A", "area_m2": 44}, {"zone": "B", "area_m2": 32},
               {"zone": "C", "area_m2": 18}],
     "position_m": 155},
    {"uarn": "1001009", "address": "139 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "shop",
     "zone_a_price": 410,
     "zones": [{"zone": "A", "area_m2": 40}, {"zone": "B", "area_m2": 30}],
     "position_m": 175},
    {"uarn": "1001010", "address": "141 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "cafe",
     "zone_a_price": 380,
     "zones": [{"zone": "A", "area_m2": 34}, {"zone": "B", "area_m2": 24}],
     "position_m": 200},
    {"uarn": "1001011", "address": "143 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "shop",
     "zone_a_price": 445,
     "zones": [{"zone": "A", "area_m2": 48}, {"zone": "B", "area_m2": 34}],
     "position_m": 220},
    # ▸ BORDERLINE – ZA £450, ~1.6 MADs above median; nearest shop comp is £445 (25 m)
    {"uarn": "1001012", "address": "145 Mare Street, Hackney, London E8 3RH",
     "postcode": "E8 3RH", "street_key": "mare_street", "use": "shop",
     "zone_a_price": 450,
     "zones": [{"zone": "A", "area_m2": 42}, {"zone": "B", "area_m2": 30},
               {"zone": "C", "area_m2": 15}],
     "position_m": 245},

    # ── Kingsland Road (E8 2PB) ── median ZA ≈ 382, MAD ≈ 40 ──
    {"uarn": "1002001", "address": "200 Kingsland Road, Hackney, London E8 2PB",
     "postcode": "E8 2PB", "street_key": "kingsland_road", "use": "shop",
     "zone_a_price": 300,
     "zones": [{"zone": "A", "area_m2": 38}, {"zone": "B", "area_m2": 28}],
     "position_m": 0},
    {"uarn": "1002002", "address": "202 Kingsland Road, Hackney, London E8 2PB",
     "postcode": "E8 2PB", "street_key": "kingsland_road", "use": "cafe",
     "zone_a_price": 330,
     "zones": [{"zone": "A", "area_m2": 35}, {"zone": "B", "area_m2": 25}],
     "position_m": 25},
    {"uarn": "1002003", "address": "204 Kingsland Road, Hackney, London E8 2PB",
     "postcode": "E8 2PB", "street_key": "kingsland_road", "use": "shop",
     "zone_a_price": 350,
     "zones": [{"zone": "A", "area_m2": 42}, {"zone": "B", "area_m2": 30}],
     "position_m": 50},
    {"uarn": "1002004", "address": "206 Kingsland Road, Hackney, London E8 2PB",
     "postcode": "E8 2PB", "street_key": "kingsland_road", "use": "shop",
     "zone_a_price": 365,
     "zones": [{"zone": "A", "area_m2": 36}, {"zone": "B", "area_m2": 26}],
     "position_m": 75},
    {"uarn": "1002005", "address": "208 Kingsland Road, Hackney, London E8 2PB",
     "postcode": "E8 2PB", "street_key": "kingsland_road", "use": "cafe",
     "zone_a_price": 375,
     "zones": [{"zone": "A", "area_m2": 40}, {"zone": "B", "area_m2": 28}],
     "position_m": 95},
    # ▸ OUTLIER 2 – ZA £520 vs street median ~£382 (3.4 MADs)
    {"uarn": "1002006", "address": "210 Kingsland Road, Hackney, London E8 2PB",
     "postcode": "E8 2PB", "street_key": "kingsland_road", "use": "shop",
     "zone_a_price": 520,
     "zones": [{"zone": "A", "area_m2": 45}, {"zone": "B", "area_m2": 32},
               {"zone": "C", "area_m2": 20}],
     "position_m": 120},
    {"uarn": "1002007", "address": "212 Kingsland Road, Hackney, London E8 2PB",
     "postcode": "E8 2PB", "street_key": "kingsland_road", "use": "shop",
     "zone_a_price": 390,
     "zones": [{"zone": "A", "area_m2": 37}, {"zone": "B", "area_m2": 27}],
     "position_m": 145},
    {"uarn": "1002008", "address": "214 Kingsland Road, Hackney, London E8 2PB",
     "postcode": "E8 2PB", "street_key": "kingsland_road", "use": "cafe",
     "zone_a_price": 430,
     "zones": [{"zone": "A", "area_m2": 33}, {"zone": "B", "area_m2": 23}],
     "position_m": 170},
    # ▸ OUTLIER 3 – ZA £500 vs street median ~£382 (2.9 MADs)
    {"uarn": "1002009", "address": "216 Kingsland Road, Hackney, London E8 2PB",
     "postcode": "E8 2PB", "street_key": "kingsland_road", "use": "shop",
     "zone_a_price": 500,
     "zones": [{"zone": "A", "area_m2": 50}, {"zone": "B", "area_m2": 35},
               {"zone": "C", "area_m2": 22}],
     "position_m": 195},
    {"uarn": "1002010", "address": "218 Kingsland Road, Hackney, London E8 2PB",
     "postcode": "E8 2PB", "street_key": "kingsland_road", "use": "shop",
     "zone_a_price": 410,
     "zones": [{"zone": "A", "area_m2": 44}, {"zone": "B", "area_m2": 30}],
     "position_m": 220},

    # ── Broadway Market (E8 4QJ) ── median ZA ≈ 330, MAD ≈ 20 ──
    {"uarn": "1003001", "address": "10 Broadway Market, Hackney, London E8 4QJ",
     "postcode": "E8 4QJ", "street_key": "broadway_market", "use": "shop",
     "zone_a_price": 280,
     "zones": [{"zone": "A", "area_m2": 30}, {"zone": "B", "area_m2": 22}],
     "position_m": 0},
    {"uarn": "1003002", "address": "12 Broadway Market, Hackney, London E8 4QJ",
     "postcode": "E8 4QJ", "street_key": "broadway_market", "use": "shop",
     "zone_a_price": 300,
     "zones": [{"zone": "A", "area_m2": 28}, {"zone": "B", "area_m2": 20}],
     "position_m": 20},
    {"uarn": "1003003", "address": "14 Broadway Market, Hackney, London E8 4QJ",
     "postcode": "E8 4QJ", "street_key": "broadway_market", "use": "cafe",
     "zone_a_price": 345,
     "zones": [{"zone": "A", "area_m2": 34}, {"zone": "B", "area_m2": 24}],
     "position_m": 40},
    {"uarn": "1003004", "address": "16 Broadway Market, Hackney, London E8 4QJ",
     "postcode": "E8 4QJ", "street_key": "broadway_market", "use": "shop",
     "zone_a_price": 310,
     "zones": [{"zone": "A", "area_m2": 26}, {"zone": "B", "area_m2": 18}],
     "position_m": 60},
    {"uarn": "1003005", "address": "18 Broadway Market, Hackney, London E8 4QJ",
     "postcode": "E8 4QJ", "street_key": "broadway_market", "use": "cafe",
     "zone_a_price": 355,
     "zones": [{"zone": "A", "area_m2": 36}, {"zone": "B", "area_m2": 26}],
     "position_m": 80},
    {"uarn": "1003006", "address": "20 Broadway Market, Hackney, London E8 4QJ",
     "postcode": "E8 4QJ", "street_key": "broadway_market", "use": "shop",
     "zone_a_price": 330,
     "zones": [{"zone": "A", "area_m2": 32}, {"zone": "B", "area_m2": 22}],
     "position_m": 105},
    # ▸ WEAK CASE – restaurant at ZA £400; only nearby comps are shops/cafes (different use)
    {"uarn": "1003007", "address": "22 Broadway Market, Hackney, London E8 4QJ",
     "postcode": "E8 4QJ", "street_key": "broadway_market", "use": "restaurant",
     "zone_a_price": 400,
     "zones": [{"zone": "A", "area_m2": 50}, {"zone": "B", "area_m2": 38},
               {"zone": "C", "area_m2": 25}],
     "position_m": 125},
    {"uarn": "1003008", "address": "24 Broadway Market, Hackney, London E8 4QJ",
     "postcode": "E8 4QJ", "street_key": "broadway_market", "use": "shop",
     "zone_a_price": 315,
     "zones": [{"zone": "A", "area_m2": 30}, {"zone": "B", "area_m2": 20}],
     "position_m": 145},
    {"uarn": "1003009", "address": "26 Broadway Market, Hackney, London E8 4QJ",
     "postcode": "E8 4QJ", "street_key": "broadway_market", "use": "shop",
     "zone_a_price": 340,
     "zones": [{"zone": "A", "area_m2": 33}, {"zone": "B", "area_m2": 23}],
     "position_m": 170},
]

# ── compute rv and build indexes ───────────────────────────────

for _p in _RAW:
    _p["rv"] = _rv(_p["zone_a_price"], _p["zones"])

BY_UARN: dict[str, dict] = {p["uarn"]: p for p in _RAW}

BY_STREET: dict[str, list[dict]] = {}
for _p in _RAW:
    BY_STREET.setdefault(_p["street_key"], []).append(_p)


# ── response helpers ───────────────────────────────────────────

def _summary(p: dict) -> dict:
    return {
        "uarn": p["uarn"],
        "address": p["address"],
        "postcode": p["postcode"],
        "street_key": p["street_key"],
        "use": p["use"],
        "zone_a_price": p["zone_a_price"],
        "rv": p["rv"],
    }


def _full_valuation(p: dict) -> dict:
    zones_detail = []
    for z in p["zones"]:
        zones_detail.append({
            "zone": z["zone"],
            "area_m2": z["area_m2"],
            "price_per_m2": round(p["zone_a_price"] * ZONE_MULT[z["zone"]], 2),
        })
    return {
        **_summary(p),
        "zones": zones_detail,
    }


# ── endpoints ──────────────────────────────────────────────────

@app.get("/search_properties")
def search_properties(
    street: str | None = Query(None),
    use: str | None = Query(None),
    postcode: str | None = Query(None),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    results = _RAW
    if street:
        results = [p for p in results if p["street_key"] == street]
    if use:
        results = [p for p in results if p["use"] == use]
    if postcode:
        results = [p for p in results if p["postcode"] == postcode]
    return [_summary(p) for p in results[:limit]]


@app.get("/get_valuation/{uarn}")
def get_valuation(uarn: str):
    p = BY_UARN.get(uarn)
    if not p:
        raise HTTPException(status_code=404, detail="property not found")
    return _full_valuation(p)


@app.get("/comparables/{uarn}")
def comparables(
    uarn: str,
    max_distance: float = Query(200, ge=0),
    same_use: bool = Query(True),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    subject = BY_UARN.get(uarn)
    if not subject:
        raise HTTPException(status_code=404, detail="property not found")
    hits = []
    for other in BY_STREET.get(subject["street_key"], []):
        if other["uarn"] == uarn:
            continue
        dist = abs(other["position_m"] - subject["position_m"])
        if dist > max_distance:
            continue
        if same_use and other["use"] != subject["use"]:
            continue
        hits.append({**_summary(other), "distance_m": round(dist, 1)})
    hits.sort(key=lambda c: c["distance_m"])
    return hits[:limit]


@app.get("/street_tone/{street_key}")
def street_tone(street_key: str):
    props = BY_STREET.get(street_key)
    if not props:
        raise HTTPException(status_code=404, detail="street not found")
    prices = [p["zone_a_price"] for p in props]
    med = statistics.median(prices)
    mad = statistics.median([abs(x - med) for x in prices])
    return {
        "street_key": street_key,
        "count": len(props),
        "median_zone_a": round(med, 2),
        "mean_zone_a": round(statistics.mean(prices), 2),
        "spread_mad": round(mad, 2),
        "uses": _use_counts(props),
    }


def _use_counts(props: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in props:
        counts[p["use"]] = counts.get(p["use"], 0) + 1
    return counts


# ── health ─────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "properties": len(_RAW), "streets": len(BY_STREET)}
