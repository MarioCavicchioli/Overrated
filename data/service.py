"""Data service: DuckDB over the Parquet tables built by data/build.py.

Run:  .venv/bin/uvicorn data.service:app --port 8000
Auth: every request needs header X-API-Key = DATA_SERVICE_KEY from .env
      (the VOA licence forbids publishing the data, and ngrok makes the URL public).

Tools: search_properties, get_valuation, comparables, street_tone,
historic_values (eval only), plus scan (Scout helper: flags every shop in an area).
Distances use postcode centroids from postcodes.io (open data, cached on disk).
"""
import json
import math
import os
import threading
from datetime import date

import duckdb
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query

HERE = os.path.dirname(os.path.abspath(__file__))
PQ = os.path.join(HERE, "parquet")
GEO_CACHE = os.path.join(HERE, "raw", "_work", "geo.json")
LIST_END = date(2029, 3, 31)
# VOA download each table was built from (see data/build.py)
SNAPSHOT = {"2026": "voa-2026-compiled-epoch-0004", "2023": "voa-2023-compiled-epoch-0023"}


def load_env():
    path = os.path.join(HERE, "..", ".env")
    if os.path.exists(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k and not k.startswith("#") and v and k not in os.environ:
                os.environ[k] = v


load_env()
API_KEY = os.environ.get("DATA_SERVICE_KEY", "")

con = duckdb.connect()
for t in ("list", "historic", "sv", "sv_lines"):
    for y in ("2026", "2023"):
        con.execute(f"create view {t}_{y} as select * from '{PQ}/{t}_{y}.parquet'")
# A street segment is a street + postcode sector + VOA valuation scheme: the VOA prices
# stretches of one street under different schemes (e.g. Shoreditch High St at £1,140 and £1,550).
for y in ("2026", "2023"):
    con.execute(f"""create view shops_{y} as select *,
        street_key || '#' || coalesce(scheme_ref, '') as segment_key
        from '{PQ}/shops_{y}.parquet'""")

app = FastAPI(title="Overrated data service")


def auth(x_api_key: str = Header(default="")):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(401, "missing or wrong X-API-Key")


def q(sql, params=()):
    cur = con.cursor()
    rel = cur.execute(sql, params)
    cols = [d[0] for d in rel.description]
    return [dict(zip(cols, r)) for r in rel.fetchall()]


# --- postcode geocoding (postcodes.io), cached ---------------------------------
_geo_lock = threading.Lock()
try:
    _geo = json.load(open(GEO_CACHE))
except (OSError, ValueError):
    _geo = {}


def geocode(postcodes):
    want = sorted({p for p in postcodes if p and p not in _geo})
    for i in range(0, len(want), 100):
        try:
            r = httpx.post("https://api.postcodes.io/postcodes",
                           json={"postcodes": want[i:i + 100]}, timeout=10)
            for item in r.json().get("result", []):
                res = item.get("result")
                _geo[item["query"]] = [res["latitude"], res["longitude"]] if res else None
        except httpx.HTTPError:
            break
    if want:
        with _geo_lock:
            json.dump(_geo, open(GEO_CACHE, "w"))
    return {p: _geo.get(p) for p in postcodes}


def distance_m(a, b):
    if not a or not b:
        return None
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(6371000 * 2 * math.asin(math.sqrt(h)))


# --- money ----------------------------------------------------------------------
RETAIL_SCAT = {"249"}  # shops; extend with hospitality/leisure codes if we go beyond shops


def multiplier(rv, retail=True):
    if rv >= 500000:
        return 0.508
    if rv >= 51000:
        return 0.43 if retail else 0.48
    return 0.382 if retail else 0.432


def years_left(today=None):
    today = today or date.today()
    return max((LIST_END - today).days / 365.25, 0)


def saving(rv, proposed_rv, retail=True):
    annual = rv * multiplier(rv, retail) - proposed_rv * multiplier(proposed_rv, retail)
    return round(annual), round(annual * years_left())


# --- street tone ----------------------------------------------------------------
def tone(segment_key, scat_code, year="2026"):
    rows = q(f"""
        select count(*) n, median(zone_a_price) median, quantile_cont(zone_a_price, 0.25) p25,
               quantile_cont(zone_a_price, 0.75) p75, avg(zone_a_price) mean,
               median(abs(zone_a_price - (select median(zone_a_price) from shops_{year}
                   where segment_key = $1 and scat_code = $2))) mad
        from shops_{year} where segment_key = $1 and scat_code = $2
    """, (segment_key, scat_code))[0]
    if not rows["n"]:
        return None
    rows["segment_key"] = segment_key
    # robust spread; floor at 5% of the median because a street's tone is often a single price
    rows["spread"] = max(1.4826 * (rows["mad"] or 0), 0.05 * rows["median"])
    return rows


def flags_for(shop, t):
    out = []
    z = (shop["zone_a_price"] - t["median"]) / t["spread"] if t else None
    if z is not None and t["n"] >= 5 and z > 2:
        out.append("zone_a_above_tone")
    rv = shop["rv"] or 0
    if 51000 <= rv < 54000:
        out.append("just_over_51k")
    if 500000 <= rv < 525000:
        out.append("just_over_500k")
    return z, out


def proposal(shop, t):
    """Proposed RV if Zone A came down to the street median (zoned area only)."""
    if not t or not shop["itza"] or shop["zone_a_price"] <= t["median"]:
        return None
    drop = (shop["zone_a_price"] - t["median"]) * shop["itza"]
    proposed = int(max(shop["rv"] - drop, 0) // 50 * 50)
    annual, total = saving(shop["rv"], proposed, shop["scat_code"] in RETAIL_SCAT)
    return {"proposed_rv": proposed, "annual_saving": annual, "est_saving": total,
            "years_left": round(years_left(), 2)}


def get_shop(uarn, year="2026"):
    rows = q(f"select * from shops_{year} where uarn = $1", (uarn,))
    return rows[0] if rows else None


# --- tools ----------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "shops_2026": q("select count(*) n from shops_2026")[0]["n"]}


@app.get("/search_properties", dependencies=[Depends(auth)])
def search_properties(q_: str = Query("", alias="q"), postcode: str = "", limit: int = Query(20, le=100)):
    """Find properties on the 2026 list by address text and/or postcode."""
    where, params = [], []
    for word in q_.upper().split():
        params.append(f"%{word}%")
        where.append(f"l.full_address like ${len(params)}")
    if postcode:
        params.append(postcode.upper().replace(" ", ""))
        where.append(f"replace(l.postcode, ' ', '') like ${len(params)} || '%'")
    if not where:
        raise HTTPException(400, "give q or postcode")
    params.append(limit)
    return q(f"""
        select l.uarn, l.full_address address, l.postcode, l.street_key, l.desc_text as "use",
               l.scat_code, l.rv, s.zone_a_price
        from list_2026 l left join shops_2026 s using (uarn)
        where {' and '.join(where)} order by l.full_address limit ${len(params)}
    """, params)


@app.get("/get_valuation/{uarn}", dependencies=[Depends(auth)])
def get_valuation(uarn: int):
    """List entry, summary valuation lines, Zone A price, street tone, flags, likely saving."""
    entry = q("select * from list_2026 where uarn = $1", (uarn,))
    if not entry:
        raise HTTPException(404, "uarn not on the 2026 list")
    sv = q("select * from sv_2026 where uarn = $1", (uarn,))
    lines = q("select floor, description, area, price, value from sv_lines_2026 where uarn = $1 order by line_no", (uarn,))
    shop = get_shop(uarn)
    out = {"entry": entry[0], "summary": sv[0] if sv else None, "lines": lines, "shop": shop}
    if shop:
        t = tone(shop["segment_key"], shop["scat_code"])
        z, flags = flags_for(shop, t)
        out.update(street_tone=t, z_score=z, flags=flags, proposal=proposal(shop, t))
        if shop["postcode"]:
            out["latlng"] = geocode([shop["postcode"]])[shop["postcode"]]
    return out


@app.get("/comparables/{uarn}", dependencies=[Depends(auth)])
def comparables(uarn: int, n: int = Query(6, ge=1, le=20)):
    """Similar shops: same street segment (street + VOA scheme) first, then same street, then same sector."""
    shop = get_shop(uarn)
    if not shop:
        raise HTTPException(404, "no zoned shop valuation for this uarn")
    rows = q("""
        select uarn, address, postcode, street_key, segment_key, desc_text as "use", rv, zone_a_price, itza,
               segment_key = $5 as same_segment, street_key = $2 as same_street
        from shops_2026
        where uarn <> $1 and scat_code = $3
          and (street_key = $2 or postcode_sector = $4)
    """, (uarn, shop["street_key"], shop["scat_code"], shop["postcode_sector"], shop["segment_key"]))
    geo = geocode([shop["postcode"]] + [r["postcode"] for r in rows])
    here = geo.get(shop["postcode"])
    for r in rows:
        r["distance_m"] = 0 if r["postcode"] == shop["postcode"] else distance_m(here, geo.get(r["postcode"]))
    rows.sort(key=lambda r: (not r["same_segment"], not r["same_street"],
                             r["distance_m"] if r["distance_m"] is not None else 1e9))
    return {"subject": shop, "comparables": rows[:n]}


@app.get("/street_tone", dependencies=[Depends(auth)])
def street_tone(street_key: str = "", segment_key: str = "", uarn: int = 0, scat_code: str = "249",
                year: str = "2026"):
    """Zone A tone per street segment (street + VOA scheme): median, spread and every shop with its z-score.

    Give uarn (its own segment), segment_key (one segment) or street_key (every segment on the street).
    """
    if year not in ("2026", "2023"):
        raise HTTPException(400, "year is 2026 or 2023")
    if uarn:
        shop = get_shop(uarn, year)
        if not shop:
            raise HTTPException(404, "no zoned shop valuation for this uarn")
        segment_key, scat_code = shop["segment_key"], shop["scat_code"]
    if segment_key:
        keys = [segment_key]
    elif street_key:
        keys = [r["segment_key"] for r in q(
            f"select distinct segment_key from shops_{year} where street_key = $1 and scat_code = $2",
            (street_key, scat_code))]
    else:
        raise HTTPException(400, "give uarn, segment_key or street_key")
    segments = []
    for key in keys:
        t = tone(key, scat_code, year)
        if not t:
            continue
        shops = q(f"select uarn, address, postcode, rv, zone_a_price, itza, desc_text as \"use\" from shops_{year} "
                  "where segment_key = $1 and scat_code = $2 order by zone_a_price desc", (key, scat_code))
        for s in shops:
            s["z_score"] = round((s["zone_a_price"] - t["median"]) / t["spread"], 2)
        segments.append({"segment_key": key, "tone": t, "shops": shops})
    if not segments:
        raise HTTPException(404, "no shops on this street or segment")
    # map position of every shop (postcode centroid, cached), so a map can plot the segment
    geo = geocode([s["postcode"] for seg in segments for s in seg["shops"]])
    for seg in segments:
        for s in seg["shops"]:
            ll = geo.get(s["postcode"])
            s["latitude"], s["longitude"] = (ll[0], ll[1]) if ll else (None, None)
    segments.sort(key=lambda s: -s["tone"]["n"])
    return {"street_key": street_key or segments[0]["segment_key"].split("#")[0], "scat_code": scat_code,
            "year": year, "segments": segments}


@app.get("/historic_values/{uarn}", dependencies=[Depends(auth)])
def historic_values(uarn: int):
    """Eval only: the 2023 list entry, historic entries and 2023 Zone A tone for a property."""
    shop23 = get_shop(uarn, "2023")
    t23 = tone(shop23["segment_key"], shop23["scat_code"], "2023") if shop23 else None
    return {
        "list_2023": q("select * from list_2023 where uarn = $1", (uarn,)),
        "historic_2023": q("select * from historic_2023 where uarn = $1 order by from_date", (uarn,)),
        "historic_2026": q("select * from historic_2026 where uarn = $1 order by from_date", (uarn,)),
        "shop_2023": shop23,
        "street_tone_2023": t23,
    }


@app.get("/scan", dependencies=[Depends(auth)])
def scan(area: str = Query("", description="postcode district or sector, e.g. SW19 or SW19 5"),
         ba_code: str = Query("", description="billing authority code, e.g. 5360 for Hackney"),
         year: str = "2026", scat_code: str = "249", min_street_n: int = 5,
         flagged_only: bool = False, geocode_rows: bool = True, limit: int = Query(200, le=5000)):
    """Scout helper: score every shop in an area or borough against its street segment
    (street + postcode sector + VOA scheme); flagged ones first. street_n / street_median /
    street_spread describe the shop's segment."""
    if year not in ("2026", "2023"):
        raise HTTPException(400, "year is 2026 or 2023")
    area = area.upper().strip()
    if not area and not ba_code:
        raise HTTPException(400, "give area or ba_code")
    rows = q(f"""
        with s as (
          select * from shops_{year} where scat_code = $2
            and ($1 = '' or postcode_sector = $1 or postcode_sector like $1 || ' %')
            and ($4 = '' or ba_code = $4)
        ),
        t as (
          select segment_key, count(*) n, median(zone_a_price) median from shops_{year}
          where scat_code = $2 and segment_key in (select segment_key from s) group by 1
        ),
        m as (
          select x.segment_key, median(abs(x.zone_a_price - t.median)) mad
          from shops_{year} x join t using (segment_key) where x.scat_code = $2 group by 1
        )
        select s.uarn, s.address, s.postcode, s.street_key, s.segment_key, s.scheme_ref, s.ba_code,
               s.desc_text as "use", s.rv, s.total_area, s.zone_a_area, s.zone_a_price, s.itza, s.scat_code,
               t.n street_n, t.median street_median,
               greatest(1.4826 * m.mad, 0.05 * t.median) street_spread
        from s join t using (segment_key) join m using (segment_key)
        where t.n >= $3
    """, (area, scat_code, min_street_n, ba_code))
    out = []
    for r in rows:
        t = {"n": r["street_n"], "median": r["street_median"], "spread": r["street_spread"]}
        z, flags = flags_for(r, t)
        r["z_score"], r["flags"] = round(z, 2), flags
        p = proposal(r, t) if "zone_a_above_tone" in flags else None
        r["est_saving"] = p["est_saving"] if p else 0
        r["proposal"] = p
        out.append(r)
    out.sort(key=lambda r: (-len(r["flags"]), -r["est_saving"], -r["z_score"]))
    flagged = [r for r in out if r["flags"]]
    shown = (flagged if flagged_only else out)[:limit]
    if geocode_rows:
        geo = geocode([r["postcode"] for r in shown])
        for r in shown:
            ll = geo.get(r["postcode"])
            r["latitude"], r["longitude"] = (ll[0], ll[1]) if ll else (None, None)
    return {"area": area, "ba_code": ba_code, "year": year, "source_snapshot": SNAPSHOT[year],
            "scored": len(out), "streets": len({r["street_key"] for r in out}),
            "segments": len({r["segment_key"] for r in out}), "flagged": len(flagged), "shops": shown}
