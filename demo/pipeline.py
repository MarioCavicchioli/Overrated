"""Appeal pipeline for the local customer app.

One appeal is a job on a background thread:
  Scout (score the shop) -> Case builder (evidence pack) -> VOA advocate (tries to kill it)
  -> owner gate (case row, waiting for approval) -> mock Check -> Challenge -> decision.

The builder and advocate here are local stand-ins (builder-local-v1, advocate-local-v1) for the
Managed Agents Person 2 is building; they write the same rows (packs, verdicts, cases) and an
events row for every meaningful step, which is the live feed.

Facts come only from the data service (VOA 2026 list, read locally on 127.0.0.1:8000) and
Supabase. VOA filing is mocked: nothing is ever sent to the VOA.
"""
import calendar
import copy
import json
import logging
import os
import re
import secrets
import string
import threading
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import jsonschema

log = logging.getLogger("overrated.demo")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_env():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k and v:
            os.environ.setdefault(k, v)


_load_env()

# The data service is always called locally, never through its public tunnel.
DATA_URL = os.environ.get("LOCAL_DATA_SERVICE_URL", "http://127.0.0.1:8000")
DATA_KEY = os.environ.get("DATA_SERVICE_KEY", "")
SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

SNAPSHOT = "voa-2026-compiled-epoch-0004"
HACKNEY_AREA = "ba5360"
LOOKUP_AREA = "lookup"
BUILDER_VERSION = "builder-local-v1"
ADVOCATE_VERSION = "advocate-local-v1"
MODEL = "claude-opus-5-5"
BETAS = ["server-side-fallback-2026-07-01"]
ADVOCATE_MAX_TURNS = 8
# Shops (VOA special category 249) are billed at the retail, hospitality and leisure multipliers. Identical to
# RETAIL_SCAT in data/service.py, whose proposal (and so every saving we show) uses the standard multiplier for any
# other category. Until both sets list the hospitality and leisure codes, we only take cases for shops.
RETAIL_SCAT = {"249"}
LONDON = ZoneInfo("Europe/London")
ZONE_FACTORS = {"Retail Zone A": 1, "Retail Zone B": 0.5, "Retail Zone C": 0.25,
                "Remaining Retail Zone": 0.125, "Retail Zone D": 0.125}  # as data/build.py

with open(os.path.join(ROOT, "contracts", "pack.schema.json")) as f:
    PACK_SCHEMA = json.load(f)
PACK_VALIDATOR = jsonschema.Draft202012Validator(PACK_SCHEMA)

STAGES = [
    ("scout", "scout", "Scout scores the shop"),
    ("builder", "builder", "Case builder writes the evidence pack"),
    ("advocate", "advocate", "VOA advocate tries to kill the case"),
    ("owner", "owner", "You approve the filing"),
    ("check", "orchestrator", "Check filed with the VOA (mock)"),
    ("challenge", "caseworker", "Challenge"),
    ("decision", "caseworker", "Decision and refund"),
]
STAGE_KEYS = [k for k, _, _ in STAGES]
# who writes the events rows for each stage (events.agent)
EVENT_AGENT = {"scout": "scout", "builder": "builder", "advocate": "advocate", "owner": "orchestrator",
               "check": "orchestrator", "challenge": "caseworker", "decision": "caseworker"}
CHECK_STAGES = ("check_draft", "check_submitted", "waiting_check_decision")
CHALLENGE_STAGES = ("challenge_draft", "challenge_submitted", "waiting_challenge_decision")


# --- errors -------------------------------------------------------------------------
class DemoError(Exception):
    status = 500

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class BadRequest(DemoError):
    status = 400


class NotFound(DemoError):
    status = 404


class Conflict(DemoError):
    status = 409


class Upstream(DemoError):
    status = 502


class StageFailed(Exception):
    """A pipeline step could not finish; the message is shown to the customer."""


# --- HTTP clients (keys stay in this process) ----------------------------------------
_ds = httpx.Client(base_url=DATA_URL, headers={"X-API-Key": DATA_KEY},
                   timeout=httpx.Timeout(60, connect=5))
_sb = httpx.Client(base_url=f"{SB_URL}/rest/v1", headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"},
                   timeout=httpx.Timeout(20, connect=5))


def _detail(r):
    try:
        j = r.json()
        if isinstance(j, dict):
            return str(j.get("detail") or j.get("message") or j)[:300]
    except ValueError:
        pass
    return r.text[:300]


def ds(path, **params):
    """GET from the data service. Paths only ever carry validated digits."""
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    try:
        r = _ds.get(path, params=params)
    except httpx.HTTPError as e:
        raise Upstream(f"The VOA data service is not reachable ({type(e).__name__}).") from None
    if r.status_code == 404:
        raise NotFound(_detail(r))
    if r.status_code >= 400:
        raise Upstream(f"VOA data service error {r.status_code}: {_detail(r)}")
    return r.json()


def sb(method, table, params=None, body=None, prefer=None):
    headers = {"Prefer": prefer} if prefer else {}
    try:
        r = _sb.request(method, f"/{table}", params=params, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise Upstream(f"The database is not reachable ({type(e).__name__}).") from None
    if r.status_code >= 400:
        raise Upstream(f"Database error {r.status_code} on {table}: {_detail(r)}")
    return r


def sb_select(table, **params):
    return sb("GET", table, params).json()


def sb_one(table, **params):
    rows = sb_select(table, limit="1", **params)
    return rows[0] if rows else None


def sb_insert(table, row):
    return sb("POST", table, body=row, prefer="return=representation").json()[0]


def sb_update(table, match, patch):
    params = {k: f"eq.{v}" for k, v in match.items()}
    return sb("PATCH", table, params, patch, prefer="return=representation").json()


def sb_count(table, **params):
    r = sb("GET", table, {"select": "*", "limit": "1", **params}, prefer="count=exact")
    total = r.headers.get("content-range", "*/0").split("/")[-1]
    return int(total) if total.isdigit() else 0


def log_event(agent, kind, message, *, stage=None, candidate_id=None, case_id=None, pack_id=None, run_id=None,
              tokens=None, payload=None, ts=None, key=None):
    """Append one events row (the live feed). A failed write is logged, never fatal."""
    row = {"agent": agent, "kind": kind, "message": message[:2000], "candidate_id": candidate_id,
           "case_id": case_id, "pack_id": pack_id, "run_id": run_id, "tokens": tokens,
           "payload": {**({"stage": stage} if stage else {}), **(payload or {})}}
    if ts:
        row["ts"] = ts
    if key:
        row["idempotency_key"] = key
    try:
        sb("POST", "events", body=row, prefer="return=minimal")
    except Upstream as e:
        log.warning("events write failed: %s", e.detail)


# --- formatting ------------------------------------------------------------------------
def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def parse_ts(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_date(s):
    dt = parse_ts(s)
    if not dt:
        return "n/a"
    dt = dt.astimezone(LONDON)
    return f"{dt.day} {dt:%b %Y}"


def fmt_time(s):
    dt = parse_ts(s)
    return dt.astimezone(LONDON).strftime("%H:%M") if dt else ""


def add_months(dt, n):
    m = dt.month - 1 + n
    y, m = dt.year + m // 12, m % 12 + 1
    return dt.replace(year=y, month=m, day=min(dt.day, calendar.monthrange(y, m)[1]))


def gbp(x):
    return "n/a" if x is None else f"£{x:,.0f}"


def price(x):
    """£ per m2: no decimals unless needed."""
    if x is None:
        return "n/a"
    return f"£{x:,.0f}/m²" if abs(x - round(x)) < 0.005 else f"£{x:,.2f}/m²"


def m2(x):
    return "n/a" if x is None else f"{x:,.1f} m²"


def nice(s):
    return " ".join(w.capitalize() for w in (s or "").split())


def street_name(street_key):
    return nice((street_key or "").split("|")[0])


def short_address(addr):
    """'BST AND GRD FLR 223, SHOREDITCH HIGH STREET, LONDON' -> '223 Shoreditch High Street'."""
    parts = [p.strip() for p in (addr or "").split(",") if p.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return nice(parts[0])
    first = re.sub(r"\(.*?\)", " ", parts[0]).strip()
    m = re.search(r"(\d+[A-Z]?(?:\s*-\s*\d+[A-Z]?)?)$", first)
    if m:
        return f"{m.group(1).replace(' ', '')} {nice(parts[1])}"
    return f"{nice(first or parts[0])}, {nice(parts[1])}"


def pct_diff(a, b):
    return round((a / b - 1) * 100) if a is not None and b else None


def num(x):
    """Supabase numeric -> int when whole."""
    if x is None:
        return None
    return int(x) if float(x).is_integer() else float(x)


# --- money (same rules as data/service.py) ----------------------------------------------
def multiplier(rv, retail=True):
    if rv >= 500000:
        return 0.508
    if rv >= 51000:
        return 0.43 if retail else 0.48
    return 0.382 if retail else 0.432


def band_label(rv, retail=True):
    kind = "retail, hospitality and leisure" if retail else "standard multiplier assumed"
    if rv >= 500000:
        return "£500,000 and over"
    if rv >= 51000:
        return f"{kind}, £51,000 to £499,999"
    return f"{kind}, under £51,000"


# --- reading a property ------------------------------------------------------------------
def get_valuation(uarn):
    return ds(f"/get_valuation/{int(uarn)}")


def assess(v):
    """Plain verdict on a get_valuation result: (verdict, sentence, can_appeal, blocked_reason)."""
    shop, entry = v.get("shop"), v["entry"]
    if not shop:
        use = (entry.get("desc_text") or "a non-shop property").lower()
        return ("no_zoning",
                f"This property is valued as {use}, not on the Zone A shop basis, so it cannot be compared "
                "with shops on the street.",
                False, "We can only challenge shops valued on the Zone A basis.")
    t = v.get("street_tone") or {}
    n, med, za = t.get("n") or 0, t.get("median"), shop.get("zone_a_price")
    z, p = v.get("z_score"), v.get("proposal")
    street = street_name(shop.get("street_key"))
    stretch = f"the {n} shop{'s' if n != 1 else ''} on this stretch of {street}"
    if p and z is not None and z >= 2 and n >= 5:
        sentence = f"Your Zone A price of {price(za)} is {pct_diff(za, med)}% above the {price(med)} median for {stretch}."
        if entry.get("scat_code") not in RETAIL_SCAT:
            # the data service prices the saving at the standard multiplier for anything but a shop, which is wrong
            # for pubs, restaurants and cafes; we do not promise or file a saving we cannot work out correctly
            use = (entry.get("desc_text") or "this kind of property").lower()
            return ("overpaying", sentence, False,
                    f"We only take shop cases for now, so we do not work out a saving or file a case for a {use}.")
        return "overpaying", sentence, True, None
    if n < 5:
        return ("in_line",
                f"Only {n} shop{'s' if n != 1 else ''} on this stretch of {street} "
                f"{'are' if n != 1 else 'is'} valued the same way, too few to show your price is out of line.",
                False, "Too few similar shops on your stretch of street to make a case.")
    if za is not None and med is not None and za <= med:
        if za == med:
            s = f"Your Zone A price of {price(za)} matches the median for {stretch}."
        else:
            s = f"Your Zone A price of {price(za)} is {-pct_diff(za, med)}% below the {price(med)} median for {stretch}."
        return "in_line", s, False, "Your Zone A price is not above similar shops, so there is no case to file."
    if not shop.get("itza"):
        return ("in_line", f"Your Zone A price of {price(za)} is above the {price(med)} median for {stretch}, "
                "but the valuation has no zoned area to work out a saving from.",
                False, "The valuation has no zoned area, so we cannot work out a saving.")
    return ("in_line",
            f"Your Zone A price of {price(za)} is {pct_diff(za, med)}% above the {price(med)} median for {stretch}, "
            "but within the normal spread for the street.",
            False, "Your price is within the normal spread for your street, so a challenge is unlikely to succeed.")


def use_word(desc):
    return (desc or "").split(" ")[0].upper()


def select_comparables(shop, comps, k=6):
    """Same segment first (fallback same street, then sector); prefer the same use word and an ITZA
    within 0.5x-2x of the subject; nearest first. Only rows returned by the data service."""
    usable = [c for c in comps if c.get("zone_a_price") and c.get("distance_m") is not None]
    pool, basis = [c for c in usable if c.get("same_segment")], "same street segment"
    if len(pool) < 3:
        pool, basis = [c for c in usable if c.get("same_street")], "same street"
    if len(pool) < 3:
        pool, basis = usable, "same postcode sector"
    sw, si = use_word(shop.get("desc_text")), shop.get("itza")

    def tier(c):
        same_use = use_word(c.get("use")) == sw
        size_ok = bool(si and c.get("itza") and 0.5 * si <= c["itza"] <= 2 * si)
        return 0 if same_use and size_ok else 1 if same_use else 2 if size_ok else 3

    chosen = sorted(pool, key=lambda c: (tier(c), c["distance_m"]))[:k]
    best = sum(1 for c in chosen if tier(c) == 0)
    chosen.sort(key=lambda c: c["distance_m"])
    return chosen, basis, best


def spread_positions(shops):
    """Shops share postcode centroids; fan same-point shops out on a small circle so each is visible."""
    import math
    groups = {}
    for s in shops:
        if s.get("lat") is not None and s.get("lng") is not None:
            groups.setdefault((round(s["lat"], 6), round(s["lng"], 6)), []).append(s)
    for (lat, lng), members in groups.items():
        if len(members) < 2:
            continue
        r = min(6 + 2 * len(members), 30)  # metres
        for i, s in enumerate(members):
            a = 2 * math.pi * i / len(members)
            s["lat"] = lat + r * math.sin(a) / 111320
            s["lng"] = lng + r * math.cos(a) / (111320 * math.cos(math.radians(lat)))


def segment_view(shop, subject_uarn, comparable_uarns=()):
    seg = ds("/street_tone", segment_key=shop["segment_key"], scat_code=shop["scat_code"])["segments"][0]
    t = seg["tone"]
    shops = [{"uarn": str(s["uarn"]), "address": s["address"], "use": s.get("use"),
              "zone_a_price": s["zone_a_price"], "itza": s.get("itza"), "rv": s.get("rv"), "z": s.get("z_score"),
              "lat": s.get("latitude"), "lng": s.get("longitude"),
              "is_subject": str(s["uarn"]) == str(subject_uarn),
              "is_comparable": str(s["uarn"]) in comparable_uarns} for s in seg["shops"]]
    spread_positions(shops)
    return {"segment_key": seg["segment_key"], "street_name": street_name(shop["street_key"]),
            "scheme_ref": shop.get("scheme_ref"), "n": t["n"], "median": t["median"], "p25": t["p25"],
            "p75": t["p75"], "spread": round(t["spread"], 2), "shops": shops}


def property_view(uarn):
    v = get_valuation(uarn)
    entry, shop = v["entry"], v.get("shop")
    uarn = str(entry["uarn"])
    rv = entry.get("rv") or 0
    retail = entry.get("scat_code") in RETAIL_SCAT
    mult = multiplier(rv, retail)
    verdict, reason, can_appeal, blocked = assess(v)
    out = {"uarn": uarn, "address": entry["full_address"], "postcode": entry.get("postcode"),
           "use": entry.get("desc_text"), "rv": rv, "latlng": v.get("latlng"),
           # basis: 'retail' for shops; anything else is billed here at the standard multiplier, which may be too high
           # for hospitality and leisure, so the page labels it as an assumption
           "bill": {"multiplier": mult, "annual_bill": round(rv * mult, 2), "band": band_label(rv, retail),
                    "basis": "retail" if retail else "standard_assumed"},
           "zoning": None, "segment": None, "z_score": None, "flags": v.get("flags") or [],
           "verdict": verdict, "verdict_reason": reason, "proposal": None, "comparables": [],
           "can_appeal": can_appeal, "appeal_blocked_reason": blocked, "existing": existing_for(uarn, v)}
    if not shop:
        return out
    z = v.get("z_score")
    out["z_score"] = round(z, 2) if z is not None else None
    out["proposal"] = v.get("proposal")
    out["zoning"] = {"zone_a_price": shop["zone_a_price"], "itza": shop.get("itza"),
                     "zone_a_area": shop.get("zone_a_area"), "total_area": shop.get("total_area"),
                     "lines": v.get("lines") or []}
    comps = ds(f"/comparables/{int(uarn)}", n=12)["comparables"]
    chosen, _, _ = select_comparables(shop, comps)
    out["comparables"] = [{"uarn": str(c["uarn"]), "address": c["address"], "use": c.get("use"),
                           "zone_a_price": c["zone_a_price"], "itza": c.get("itza"), "distance_m": c["distance_m"],
                           "same_segment": bool(c.get("same_segment")), "rv": c.get("rv")} for c in chosen]
    out["segment"] = segment_view(shop, uarn, {c["uarn"] for c in out["comparables"]})
    return out


def existing_for(uarn, v=None):
    case = sb_one("cases", select="case_id,stage", uarn=f"eq.{uarn}", order="created_at.desc")
    job = running_job_for(uarn)
    out = {"case_id": case["case_id"] if case else None, "job_id": job.job_id if job else None,
           "stage": case["stage"] if case else None, "last_review": None}
    if not case and not job:
        # an earlier run the VOA advocate killed: the property page leads with its first reason
        pack = sb_one("packs", select="case_id,status,created_at,evidence_refs,current_rv,calc", uarn=f"eq.{uarn}",
                      status="eq.killed", order="created_at.desc")
        if pack:
            vd = sb_one("verdicts", select="reasons", case_id=f"eq.{pack['case_id']}", order="created_at.desc")
            reasons = [r for r in ((vd or {}).get("reasons") or []) if isinstance(r, dict) and r.get("reason")]
            out["last_review"] = {"case_id": pack["case_id"], "case_ref": case_ref_of(pack), "verdict": "kill",
                                  "created_at": pack.get("created_at"), "n_reasons": len(reasons),
                                  "reason": reasons[0]["reason"] if reasons else None,
                                  "voa_record": str(reasons[0].get("voa_record") or "") or None if reasons else None,
                                  "same_inputs": _same_inputs(pack, v) if v else None}
    return out


def _same_inputs(pack, v):
    """True when the list entry and street tone are what the killed pack was built from (so a re-run would see
    the same facts), False when any of them has changed."""
    calc, shop, t = pack.get("calc") or {}, v.get("shop") or {}, v.get("street_tone") or {}

    def same(a, b):
        return a is not None and b is not None and abs(float(a) - float(b)) < 0.005

    return (same(pack.get("current_rv"), v["entry"].get("rv")) and same(calc.get("zone_a_price"), shop.get("zone_a_price"))
            and same(calc.get("street_median"), t.get("median")))


# --- search ------------------------------------------------------------------------------
POSTCODE_RE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)(?:\s*(\d[A-Z]{0,2}))?$")
TRAILING_POSTCODE_RE = re.compile(r"(?:^|\s)([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})$")


def _postcode_match(pc, outward, inward):
    parts = (pc or "").upper().split()
    if len(parts) != 2:
        return (pc or "").upper().replace(" ", "").startswith(outward + inward)
    return parts[0] == outward and parts[1].startswith(inward)


# Street-type abbreviations people type that are not substrings of the word the VOA list uses.
ABBREVIATIONS = {"RD": "ROAD", "LN": "LANE", "GDNS": "GARDENS", "GRN": "GREEN", "PDE": "PARADE", "CT": "COURT",
                 "HSE": "HOUSE", "STN": "STATION", "MKT": "MARKET", "BDWY": "BROADWAY", "WY": "WAY"}


def _search_rows(text, outward, inward, limit=100):
    params = {"limit": limit}
    if text:
        params["q"] = text
    if outward:
        params["postcode"] = outward + inward
    rows = ds("/search_properties", **params)
    if outward:
        rows = [r for r in rows if _postcode_match(r.get("postcode"), outward, inward)]
    return rows


class _Budget:
    """Caps the data-service calls one fallback search may make."""

    def __init__(self, n):
        self.n = n

    def rows(self, text, outward, inward, limit=100):
        if self.n <= 0:
            return []
        self.n -= 1
        return _search_rows(text, outward, inward, limit)


def _fix_word(word, context, outward, inward, budget):
    """Closest spelling of a word, taken from list addresses that share a 3 or 4 letter chunk with it and match
    the rest of the query (the context narrows the candidates). None when nothing is close enough."""
    import difflib
    n = 3 if context or outward else 4
    need = 0.72 if context or outward else 0.8
    chunks = list(dict.fromkeys(word[i:i + n] for i in range(len(word) - n + 1)))[:6]
    best, best_score = None, 0.0
    for chunk in chunks:
        for r in budget.rows(" ".join(context + [chunk]), outward, inward, limit=60):
            for w in re.findall(r"[A-Z][A-Z']+", (r.get("address") or "").upper()):
                if chunk in w and w != word and abs(len(w) - len(word)) <= 3:
                    score = difflib.SequenceMatcher(None, word, w).ratio()
                    if score > best_score:
                        best, best_score = w, score
        if best_score >= 0.88:
            break
    return best if best_score >= need else None


def _search_fallback(words, outward, inward):
    """When nothing matches: expand street abbreviations, then set aside the words that stop the query
    matching, correct their spelling using the rest of the query, and if that fails search without them.
    Returns (rows, the words actually searched) or (None, None)."""
    budget = _Budget(30)
    expanded = [ABBREVIATIONS.get(w, w) for w in words]
    if expanded != words:
        rows = budget.rows(" ".join(expanded), outward, inward)
        if rows:
            return rows, expanded
    words = expanded
    idx = range(len(words))
    unknown = [i for i in idx if not words[i].isdigit() and len(words[i]) >= 3
               and not budget.rows(words[i], None, None, limit=1)]
    # words to try setting aside, in order: the unknown ones, then one more word (street words before numbers)
    others = sorted((i for i in idx if i not in unknown), key=lambda i: (words[i].isdigit(), -i))
    drops = [unknown] if unknown else []
    drops += [unknown + [i] for i in others]
    fallback = None
    for drop in drops:
        rest = [words[i] for i in idx if i not in drop]
        if not rest:
            continue
        rows = budget.rows(" ".join(rest), outward, inward)
        if not rows:
            continue
        fixed = list(words)
        for i in drop:
            if not words[i].isdigit() and len(words[i]) >= 3:
                fixed[i] = _fix_word(words[i], rest, outward, inward, budget) or fixed[i]
        if fixed != words:
            fixed_rows = budget.rows(" ".join(fixed), outward, inward)
            if fixed_rows:
                return fixed_rows, fixed
        if fallback is None:
            fallback = (rows, rest)
            if not unknown:  # a stray extra word (e.g. the borough): searching without it is the answer
                break
    if fallback:
        return fallback
    if len(unknown) == len(words):  # every word is unknown: correct them all without context, or give up
        fixed = [_fix_word(w, [], outward, inward, budget) for w in words]
        if all(fixed):
            rows = budget.rows(" ".join(fixed), outward, inward)
            if rows:
                return rows, fixed
    return None, None


def search(q):
    text = " ".join(re.sub(r"[^A-Za-z0-9&'/\- ]", " ", q or "").upper().split())[:100]
    if len(text) < 3:
        return {"results": [], "corrected": None}
    outward = inward = None
    m = POSTCODE_RE.match(text)
    if m:
        outward, inward, text = m.group(1), m.group(2) or "", ""
    else:
        m = TRAILING_POSTCODE_RE.search(text)
        if m:
            outward, inward, text = m.group(1), m.group(2), text[:m.start()].strip()
    rows = _search_rows(text, outward, inward)
    corrected = None
    if not rows and text:
        rows, used = _search_fallback(text.split(), outward, inward)
        rows = rows or []
        if used:
            corrected = " ".join(used + ([f"{outward} {inward}".strip()] if outward else []))
    rows.sort(key=lambda r: r.get("zone_a_price") is None)
    return {"results": [{"uarn": str(r["uarn"]), "address": r["address"], "postcode": r.get("postcode"),
                         "use": r.get("use"), "rv": r.get("rv"), "zone_a_price": r.get("zone_a_price"),
                         "street_key": r.get("street_key")} for r in rows[:12]],
            "corrected": corrected}


# --- Claude ------------------------------------------------------------------------------
_claude = None
_claude_lock = threading.Lock()


def claude():
    global _claude
    with _claude_lock:
        if _claude is None:
            import anthropic
            _claude = anthropic.Anthropic()
        return _claude


# --- jobs ----------------------------------------------------------------------------------
def blank_stages():
    return [{"key": k, "agent": a, "label": label, "status": "pending", "started_at": None, "finished_at": None,
             "summary": None, "steps": []} for k, a, label in STAGES]


class Job:
    def __init__(self, uarn, valuation, candidate, run_label):
        self.job_id = str(uuid.uuid4())
        self.uarn = uarn
        self.valuation = valuation
        self.candidate_id = candidate["candidate_id"]
        self.run_id = candidate.get("run_id")
        self.run_label = run_label
        self.planned_case_id = str(uuid.uuid4())  # becomes packs.case_id; tags every events row of this job
        self.case_id = None  # public once the pack is saved
        self.pack_id = None
        self.case_ref = None
        self.status = "running"
        self.stages = blank_stages()
        self.verdict = None
        self.error = None
        self.lock = threading.Lock()
        self.seq = 0
        self.created = now()

    def _stage(self, key):
        return self.stages[STAGE_KEYS.index(key)]

    def start(self, key):
        with self.lock:
            s = self._stage(key)
            s["status"], s["started_at"] = "running", iso(now())

    def step(self, key, kind, text, tokens=None, **payload):
        ts = iso(now())
        with self.lock:
            self.seq += 1
            seq = self.seq
            self._stage(key)["steps"].append({"ts": ts, "text": text})
        log_event(EVENT_AGENT[key], kind, text, stage=key, candidate_id=self.candidate_id,
                  case_id=self.planned_case_id, pack_id=self.pack_id, run_id=self.run_id, tokens=tokens,
                  payload={"job_id": self.job_id, **payload}, ts=ts, key=f"demo:{self.job_id}:{seq:03d}")

    def finish(self, key, summary, status="done"):
        with self.lock:
            s = self._stage(key)
            s["status"], s["finished_at"], s["summary"] = status, iso(now()), summary

    def set(self, key, status, summary=None):
        with self.lock:
            s = self._stage(key)
            s["status"] = status
            if summary is not None:
                s["summary"] = summary

    def to_dict(self):
        with self.lock:
            d = {"job_id": self.job_id, "uarn": self.uarn, "status": self.status, "case_id": self.case_id,
                 "pack_id": self.pack_id, "candidate_id": self.candidate_id,
                 # every events row of this job carries this case id from the first step, so the agent log
                 # can follow this run only (not earlier runs for the same shop)
                 "events_case_id": self.planned_case_id,
                 "stages": copy.deepcopy(self.stages), "verdict": copy.deepcopy(self.verdict), "error": self.error}
        if d["case_id"] and d["verdict"] and d["verdict"]["verdict"] == "survive" and d["status"] == "done":
            try:  # owner/check/challenge/decision follow the case row once it exists
                case = sb_one("cases", select="*", case_id=f"eq.{d['case_id']}")
                if case:
                    events = case_events(d["case_id"])
                    pack = {"current_rv": self.valuation["entry"]["rv"]}
                    d["stages"][3:] = tail_stages(case, d["verdict"], group_events(events), pack)
            except DemoError as e:
                log.warning("job %s: could not refresh case stages: %s", self.job_id, e.detail)
        return d


JOBS = {}
RUNNING = {}  # uarn -> job_id while running
_start_lock = threading.Lock()
_ref_lock = threading.Lock()
_lookup_run = {}


def running_job_for(uarn):
    jid = RUNNING.get(str(uarn))
    job = JOBS.get(jid) if jid else None
    return job if job and job.status == "running" else None


def get_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        raise NotFound("No such job. Jobs live in memory and are gone after a server restart; open the case instead.")
    return job.to_dict()


def lookup_run_id():
    """scout_runs row that holds candidates created by customer lookups (made once)."""
    if "id" in _lookup_run:
        return _lookup_run["id"]
    row = sb_one("scout_runs", select="run_id", area_code=f"eq.{LOOKUP_AREA}", order="created_at.asc")
    if not row:
        row = sb_insert("scout_runs", {"area_name": "Customer lookups", "area_code": LOOKUP_AREA,
                                       "rating_list": "2026", "source_snapshot": SNAPSHOT, "status": "running",
                                       "started_at": iso(now()), "config": {"source": "demo app"}})
    _lookup_run["id"] = row["run_id"]
    return row["run_id"]


def find_or_create_candidate(v):
    entry, shop, t, p = v["entry"], v["shop"], v["street_tone"], v["proposal"]
    uarn = str(entry["uarn"])
    rows = sb_select("candidates", select="candidate_id,run_id,status", uarn=f"eq.{uarn}", rating_list="eq.2026",
                     order="created_at.desc", limit="1")
    if rows:
        run = sb_one("scout_runs", select="area_name", run_id=f"eq.{rows[0]['run_id']}")
        return rows[0], f"the {run['area_name']} sweep" if run else "an earlier sweep"
    run_id = lookup_run_id()
    ll = v.get("latlng") or [None, None]
    row = sb_insert("candidates", {
        "run_id": run_id, "uarn": uarn, "rating_list": "2026", "source_snapshot": SNAPSHOT,
        "address": entry["full_address"], "postcode": entry.get("postcode"), "street_key": shop.get("street_key"),
        "latitude": ll[0], "longitude": ll[1], "property_use": entry.get("desc_text"), "rv": entry["rv"],
        "floor_area_m2": shop.get("total_area"), "zone_a_area_m2": shop.get("zone_a_area"),
        "zone_a_price": shop.get("zone_a_price"), "street_median": t["median"],
        "street_spread": round(t["spread"], 4), "z_score": round(v["z_score"], 4),
        "est_saving": p["est_saving"], "score": p["est_saving"], "flags": v.get("flags") or [],
        "evidence_refs": [{"source": "data_service", "endpoint": f"/get_valuation/{uarn}",
                           "segment_key": shop["segment_key"], "segment_n": t["n"],
                           "scheme_ref": shop.get("scheme_ref"), "source_snapshot": SNAPSHOT}],
        "playbook_version": "customer-lookup", "status": "flagged"})
    try:
        run = sb_one("scout_runs", select="properties_checked,candidates_flagged", run_id=f"eq.{run_id}")
        sb_update("scout_runs", {"run_id": run_id}, {"properties_checked": run["properties_checked"] + 1,
                                                     "candidates_flagged": run["candidates_flagged"] + 1})
    except (DemoError, TypeError, KeyError):
        pass
    return row, "a customer lookup"


def start_appeal(uarn):
    """-> ('case', case_id) | ('job', Job). Raises Conflict when there is no case to make."""
    uarn = str(int(uarn))
    with _start_lock:
        case = sb_one("cases", select="case_id", uarn=f"eq.{uarn}", order="created_at.desc")
        if case:
            return "case", case["case_id"]
        job = running_job_for(uarn)
        if job:
            return "job", job
        v = get_valuation(uarn)
        verdict, _, can_appeal, blocked = assess(v)
        if not can_appeal:
            raise Conflict(blocked)
        candidate, source = find_or_create_candidate(v)
        job = Job(uarn, v, candidate, source)
        JOBS[job.job_id] = job
        RUNNING[uarn] = job.job_id
    threading.Thread(target=run_job, args=(job,), name=f"appeal-{uarn}", daemon=True).start()
    return "job", job


def run_job(job):
    current = "scout"
    try:
        stage_scout(job)
        current = "builder"
        pack = stage_builder(job)
        current = "advocate"
        verdict = stage_advocate(job, pack)
        current = "owner"
        owner_gate(job, verdict)
        with job.lock:
            job.status = "done"
    except Exception as e:  # noqa: BLE001 - any failure ends the job with a readable reason
        msg = str(e.detail if isinstance(e, DemoError) else e) or type(e).__name__
        if not isinstance(e, (StageFailed, DemoError)):
            log.error("job %s failed:\n%s", job.job_id, traceback.format_exc())
            msg = f"{type(e).__name__}: {msg}"[:400]
        job.step(current, "failed", f"Failed: {msg}")
        job.finish(current, msg, status="failed")
        with job.lock:
            for s in job.stages:
                if s["status"] in ("pending", "running", "waiting"):
                    s["status"] = "skipped"
            job.status, job.error = "failed", msg
        try:
            back = "pack_ready" if job.pack_id else "flagged"
            sb_update("candidates", {"candidate_id": job.candidate_id}, {"status": back})
        except DemoError:
            pass


# --- stage 1: Scout -------------------------------------------------------------------------
def stage_scout(job):
    job.start("scout")
    v = job.valuation
    entry, shop, t, p = v["entry"], v["shop"], v["street_tone"], v["proposal"]
    short = short_address(entry["full_address"])
    za, z = shop["zone_a_price"], v["z_score"]
    job.step("scout", "list_entry",
             f"Read the 2026 list entry for {short}: {entry['desc_text'].lower()}, rateable value {gbp(entry['rv'])}")
    job.step("scout", "street_tone",
             f"Compared it with the {t['n']} shops on this stretch of {street_name(shop['street_key'])} "
             f"(VOA scheme {shop.get('scheme_ref')}): median Zone A {price(t['median'])}")
    job.step("scout", "scored",
             f"Zone A {price(za)} is {pct_diff(za, t['median'])}% above the median, z-score {z:.2f}, "
             "outside the street's normal spread", z_score=round(z, 2))
    job.step("scout", "saving",
             f"Likely saving {gbp(p['annual_saving'])} a year, {gbp(p['est_saving'])} to 31 March 2029")
    sb_update("candidates", {"candidate_id": job.candidate_id}, {"status": "building_pack"})
    job.step("scout", "candidate", f"Candidate from {job.run_label}; handed to the Case builder")
    job.finish("scout", scout_summary(za, t["median"], z))


def scout_summary(za, median, z):
    return f"Zone A {price(za)} against a {price(median)} median, z-score {z:.2f}"


# --- stage 2: Case builder -----------------------------------------------------------------
BUILDER_SYSTEM = """You write the argument section of an evidence pack for a business rates Check and Challenge \
on the 2026 rating list in England, filed with the Valuation Office Agency (VOA) on behalf of the ratepayer.

Write as the ratepayer's agent ("we"), in plain, formal English: 130 to 220 words, one to three paragraphs, \
no headings, no bullet points, no markdown.

Rules:
- Use only the facts in the JSON you are given. Every figure you mention must appear in it. Do not add \
anything about the property or the street that is not in the JSON (frontage, footfall, condition, rents, tenants).
- Cite comparables by address and Zone A price.
- If any comparable is priced at or above the subject, say so plainly and explain, from the facts, why the \
tone of the segment as a whole still supports the reduction.
- End with a concrete request: the Zone A price reduced to the segment median and the rateable value to the \
proposed figure.
- Do not mention Overrated, software, AI or how the pack was produced."""

ARGUMENT_SCHEMA = {"type": "object", "properties": {"argument": {"type": "string"}},
                   "required": ["argument"], "additionalProperties": False}
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text):
    out = set()
    for tok in NUMBER_RE.findall(text):
        try:
            out.add(float(tok.replace(",", "")))
        except ValueError:
            pass
    return out


def unknown_figures(argument, facts):
    """Figures of 100 or more in the argument that do not appear in the facts (allowing rounding)."""
    known = _numbers(json.dumps(facts))
    return sorted({x for x in _numbers(argument) if x >= 100 and not any(abs(x - k) <= 0.51 for k in known)})


def write_argument(facts):
    """One Claude call; returns (argument, output_tokens, rewrote)."""
    messages = [{"role": "user", "content": "Facts for the evidence pack:\n" + json.dumps(facts, indent=1)}]
    tokens, rewrote = 0, None
    for attempt in range(2):
        r = claude().beta.messages.create(
            model=MODEL, max_tokens=2000, betas=BETAS, fallbacks="default", system=BUILDER_SYSTEM,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ARGUMENT_SCHEMA}},
            messages=messages)
        tokens += r.usage.output_tokens
        if r.stop_reason == "refusal":
            raise StageFailed("Claude declined to write the argument.")
        if r.stop_reason == "max_tokens":
            raise StageFailed("The argument was cut off before it finished.")
        text = next((b.text for b in r.content if b.type == "text"), "")
        try:
            argument = json.loads(text)["argument"].strip()
        except (ValueError, KeyError, TypeError):
            raise StageFailed("The argument came back in the wrong format.") from None
        bad = unknown_figures(argument, facts)
        if not bad:
            return argument, tokens, rewrote
        rewrote = bad
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "These figures are not in the facts: "
                      + ", ".join(f"{x:,g}" for x in bad) + ". Rewrite the argument using only figures from the facts."}]
    raise StageFailed("The argument used figures that are not in the data: "
                      + ", ".join(f"{x:,g}" for x in rewrote))


def next_case_ref():
    rows = sb_select("packs", select="evidence_refs", limit="10000")
    best = len(rows)
    for r in rows:
        for ref in r.get("evidence_refs") or []:
            if isinstance(ref, dict) and ref.get("kind") == "case_ref":
                m = re.fullmatch(r"c_(\d+)", str(ref.get("value", "")))
                if m:
                    best = max(best, int(m.group(1)))
    return f"c_{best + 1:04d}"


def case_ref_of(pack_row):
    for ref in pack_row.get("evidence_refs") or []:
        if isinstance(ref, dict) and ref.get("kind") == "case_ref":
            return ref.get("value")
    return None


def proposal_of(pack_row):
    for ref in pack_row.get("evidence_refs") or []:
        if isinstance(ref, dict) and ref.get("kind") == "proposal":
            return ref
    return None


def zoning_check(lines, shop, summary):
    zl = [ln for ln in lines if ln.get("description") in ZONE_FACTORS and ln.get("area")]
    itza = sum(ln["area"] * ZONE_FACTORS[ln["description"]] for ln in zl)
    terms = " + ".join(m2(ln["area"]) + ("" if ZONE_FACTORS[ln["description"]] == 1
                                          else f" ÷ {int(1 / ZONE_FACTORS[ln['description']])}") for ln in zl)
    total = sum(ln.get("value") or 0 for ln in lines)
    match = abs(itza - (shop.get("itza") or 0)) < 0.05
    text = (f"Checked the zoning: {terms} = {itza:.1f} m² ITZA"
            + (", matching the VOA's figure" if match else f"; the VOA's figure is {shop.get('itza') or 0:.1f} m²")
            + f". Valuation lines total {gbp(total)}, adopted RV {gbp((summary or {}).get('adopted_rv'))}")
    return text, itza, total


def stage_builder(job):
    job.start("builder")
    v = job.valuation
    entry, shop, p, summary = v["entry"], v["shop"], v["proposal"], v.get("summary") or {}
    uarn = str(entry["uarn"])

    comps = ds(f"/comparables/{int(uarn)}", n=12)["comparables"]
    chosen, basis, best = select_comparables(shop, comps)
    if len(chosen) < 3:
        raise StageFailed(f"Only {len(chosen)} comparable shops on the data service; a pack needs at least 3.")
    sw, si = use_word(shop.get("desc_text")), shop.get("itza")
    n_use = sum(1 for c in chosen if use_word(c.get("use")) == sw)
    n_size = sum(1 for c in chosen if si and c.get("itza") and 0.5 * si <= c["itza"] <= 2 * si)
    count = lambda k: "all" if k == len(chosen) else "none" if k == 0 else str(k)  # noqa: E731
    fit = (f"{count(n_use)} the same use, {count(n_size)} of a similar size (ITZA within half to double "
           f"the subject's {si:.1f} m²)" if si else f"{count(n_use)} the same use")
    job.step("builder", "comparables",
             f"Picked {len(chosen)} comparables on the {basis}, {fit}, nearest first: "
             + "; ".join(f"{short_address(c['address'])} {price(c['zone_a_price'])}" for c in chosen),
             comparables=[str(c["uarn"]) for c in chosen])

    seg = ds("/street_tone", segment_key=shop["segment_key"], scat_code=shop["scat_code"])["segments"][0]
    t = seg["tone"]
    calc = {"zone_a_price": shop["zone_a_price"], "street_median": t["median"], "street_spread": round(t["spread"], 2)}
    z_by_uarn = {str(s["uarn"]): s["z_score"] for s in seg["shops"]}

    text, itza_calc, lines_total = zoning_check(v.get("lines") or [], shop, summary)
    job.step("builder", "zoning", text)

    za, med, itza, rv = shop["zone_a_price"], t["median"], shop["itza"], entry["rv"]
    drop = (za - med) * itza
    proposed, annual = p["proposed_rv"], p["annual_saving"]
    if proposed >= rv:
        raise StageFailed("The proposed value is not below the current value; there is no case.")
    job.step("builder", "calculation",
             f"Worked out the reduction: ({price(za)} − {price(med)}) × {itza:.1f} m² = {gbp(drop)} off the RV, "
             f"so {gbp(rv)} becomes {gbp(proposed)} (rounded down to £50); saving {gbp(annual)} a year")

    retail = shop.get("scat_code") in RETAIL_SCAT
    facts = {
        "subject": {"uarn": uarn, "address": entry["full_address"], "postcode": entry.get("postcode"),
                    "use": entry.get("desc_text"), "billing_authority": summary.get("ba_name"),
                    "current_rv": rv, "zone_a_price_per_m2": za, "zone_a_area_m2": shop.get("zone_a_area"),
                    "itza_m2": round(itza, 2), "total_area_m2": shop.get("total_area"),
                    "voa_scheme_ref": shop.get("scheme_ref"), "valuation_lines": v.get("lines") or [],
                    "valuation_lines_total": lines_total},
        "segment": {"street": street_name(shop["street_key"]), "postcode_sector": shop.get("postcode_sector"),
                    "voa_scheme_ref": shop.get("scheme_ref"), "shops_on_segment": t["n"],
                    "median_zone_a_price": med, "p25_zone_a_price": t["p25"], "p75_zone_a_price": t["p75"],
                    "spread": round(t["spread"], 2), "subject_z_score": round(v["z_score"], 2),
                    "shops_priced_at_the_median": sum(1 for s in seg["shops"] if s["zone_a_price"] == med),
                    "other_shops_priced_at_or_above_the_subject": sum(
                        1 for s in seg["shops"] if s["zone_a_price"] >= za and str(s["uarn"]) != uarn)},
        "comparables": [{"uarn": str(c["uarn"]), "address": c["address"], "use": c.get("use"),
                         "zone_a_price": c["zone_a_price"], "itza_m2": round(c["itza"], 2) if c.get("itza") else None,
                         "distance_m": c["distance_m"], "rv": c.get("rv"), "z_score": z_by_uarn.get(str(c["uarn"]))}
                        for c in chosen],
        "calculation": {"difference_per_m2": round(za - med, 2), "percent_above_median": pct_diff(za, med),
                        "rv_reduction": round(drop), "proposed_rv": proposed, "rounding": "down to the nearest £50",
                        "multiplier_now": multiplier(rv, retail), "multiplier_after": multiplier(proposed, retail),
                        "annual_saving": annual, "years_left_on_list": p["years_left"],
                        "saving_to_31_march_2029": p["est_saving"], "list_years": [2026, 2029]},
    }
    argument, tokens, rewrote = write_argument(facts)
    if rewrote:
        job.step("builder", "argument_rewrite",
                 "First draft used figures not in the data (" + ", ".join(f"{x:,g}" for x in rewrote)
                 + "); asked for a rewrite")
    job.step("builder", "argument", f"Wrote the argument ({len(argument.split())} words); every figure checked "
             "against the data", tokens=tokens)

    with _ref_lock:
        ref = next_case_ref()
        pack = {"case_id": ref, "uarn": uarn, "address": entry["full_address"], "current_rv": int(rv),
                "proposed_rv": int(proposed),
                "comparables": [{"uarn": str(c["uarn"]), "zone_a_price": c["zone_a_price"],
                                 "distance_m": c["distance_m"], "use": c.get("use") or ""} for c in chosen],
                "calc": calc, "annual_saving": int(annual), "argument": argument, "version": BUILDER_VERSION}
        errors = sorted(PACK_VALIDATOR.iter_errors(pack), key=lambda e: list(e.path))
        if errors:
            e = errors[0]
            raise StageFailed(f"The pack failed validation ({'/'.join(map(str, e.path)) or 'pack'}: {e.message}); "
                              "it was not saved.")
        job.step("builder", "validated", f"Validated pack {ref} against pack.schema.json")
        version = sb_count("packs", candidate_id=f"eq.{job.candidate_id}") + 1
        row = sb_insert("packs", {
            "case_id": job.planned_case_id, "candidate_id": job.candidate_id, "pack_version": version,
            "uarn": uarn, "address": pack["address"], "current_rv": pack["current_rv"],
            "proposed_rv": pack["proposed_rv"], "comparables": pack["comparables"], "calc": calc,
            "annual_saving": pack["annual_saving"], "argument": argument,
            "evidence_refs": [
                {"kind": "case_ref", "value": ref},
                {"kind": "proposal", "source": f"/get_valuation/{uarn}", **p},
                {"kind": "data_service", "endpoint": f"/comparables/{uarn}?n=12", "basis": basis},
                {"kind": "data_service", "endpoint": "/street_tone", "segment_key": shop["segment_key"],
                 "n": t["n"], "median": med, "spread": round(t["spread"], 2)},
            ],
            "source_snapshot": SNAPSHOT, "status": "built", "builder_version": BUILDER_VERSION})
    with job.lock:
        job.pack_id, job.case_id, job.case_ref = row["pack_id"], row["case_id"], ref
    sb_update("candidates", {"candidate_id": job.candidate_id}, {"status": "pack_ready"})
    bump_run(job.run_id, "packs_built")
    job.step("builder", "pack_saved", f"Saved evidence pack {ref}: proposed RV {gbp(proposed)}, "
             f"saving {gbp(annual)} a year")
    job.finish("builder", builder_summary(ref, len(chosen), proposed, annual))
    return pack


def builder_summary(ref, n, proposed, annual):
    return f"Pack {ref}: {n} comparables, proposed RV {gbp(proposed)}, saving {gbp(annual)} a year"


def bump_run(run_id, column):
    if not run_id:
        return
    try:
        run = sb_one("scout_runs", select=column, run_id=f"eq.{run_id}")
        sb_update("scout_runs", {"run_id": run_id}, {column: (run or {}).get(column, 0) + 1})
    except DemoError:
        pass


# --- stage 3: VOA advocate ----------------------------------------------------------------
ADVOCATE_SYSTEM = """You act for the Valuation Office Agency (VOA). A ratepayer's agent has filed the evidence \
pack below to reduce the rateable value of a shop on the 2026 rating list. Defend the list entry the way an \
experienced VOA valuation officer would at Check and Challenge, and decide honestly whether the case survives.

You have three tools over the VOA data: get_valuation(uarn), comparables(uarn) and street_tone(segment_key). \
Check the pack's facts against them; take nothing in the pack on trust.

Look for:
- comparables weaker than claimed: a different use, much smaller or larger (ITZA), further away, on another \
street or valuation scheme;
- the subject differing from the comparables (corner or return frontage, size, layout) where the data shows it;
- maths errors in the Zone A price, ITZA, proposed RV or saving;
- a deliberate priced run: several neighbouring shops valued at the subject's Zone A price, which suggests the \
VOA priced that stretch on purpose;
- whether the segment median really is the tone of the list.

Then call submit_verdict once. Verdict "kill" if the VOA has a sound answer that would likely defeat the case \
at Check and Challenge; "survive" if the case is strong enough to file despite your best counter-arguments. \
Give 1 to 5 reasons, each one or two plain sentences, each citing as voa_record the uarn of a VOA record you \
looked at with a tool. You have at most 8 turns: call several tools at once where you can."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["survive", "kill"]},
        "reasons": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
            "type": "object",
            "properties": {"reason": {"type": "string"},
                           "voa_record": {"type": "string",
                                          "description": "uarn of a VOA record a tool returned to you"}},
            "required": ["reason", "voa_record"], "additionalProperties": False}},
    },
    "required": ["verdict", "reasons"], "additionalProperties": False,
}


def _adv_valuation(uarn):
    v = get_valuation(uarn)
    e, s, sm = v["entry"], v.get("shop") or {}, v.get("summary") or {}
    z = v.get("z_score")
    return {"uarn": str(e["uarn"]), "address": e["full_address"], "postcode": e.get("postcode"),
            "use": e.get("desc_text"), "scat_code": e.get("scat_code"), "rv": e.get("rv"),
            "summary": {k: sm.get(k) for k in ("total_area", "adopted_rv", "scheme_ref", "unadjusted_price",
                                               "primary_desc", "unit", "ba_name")} if sm else None,
            "lines": v.get("lines") or [],
            "shop": {k: s.get(k) for k in ("zone_a_price", "zone_a_area", "itza", "total_area", "segment_key")}
            if s else None,
            "street_tone": v.get("street_tone"), "z_score": round(z, 2) if z is not None else None,
            "flags": v.get("flags") or []}


def stage_advocate(job, pack):
    from anthropic.lib.tools import ToolError, beta_tool

    job.start("advocate")
    sb_update("packs", {"pack_id": job.pack_id}, {"status": "reviewing"})
    job.step("advocate", "review_started",
             f"Opened a separate review of pack {pack['case_id']}; the advocate sees only the pack and the VOA data")
    seen = set()
    ctx = {"verdict": None, "pending_tokens": None, "calls": 0}

    def tokens_once():
        t, ctx["pending_tokens"] = ctx["pending_tokens"], None
        return t

    def clean_uarn(u):
        u = str(u).strip()
        if not u.isdigit() or len(u) > 15:
            raise ToolError("uarn must be digits only")
        return u

    def data(fn, *args):
        try:
            return fn(*args)
        except NotFound as e:
            raise ToolError(f"Not found: {e.detail}") from None
        except DemoError as e:
            raise ToolError(e.detail) from None

    @beta_tool(name="get_valuation")
    def get_valuation_tool(uarn: str) -> str:
        """Get a property's 2026 list entry, summary valuation lines, Zone A price, ITZA, its street segment's
        tone (median and spread of Zone A prices) and its z-score.

        Args:
            uarn: VOA unique address reference number (digits).
        """
        u = clean_uarn(uarn)
        out = data(_adv_valuation, u)
        seen.add(out["uarn"])
        ctx["calls"] += 1
        s = out.get("shop")
        job.step("advocate", "tool_get_valuation",
                 f"Checked the valuation of {short_address(out['address'])}: RV {gbp(out['rv'])}"
                 + (f", Zone A {price(s['zone_a_price'])}, ITZA {s['itza']:.1f} m²" if s and s.get("itza")
                    else ", not zoned"), tokens=tokens_once(), uarn=out["uarn"])
        return json.dumps(out, default=str)


    @beta_tool(name="comparables")
    def comparables_tool(uarn: str) -> str:
        """Similar shops for a property: same street segment (street + postcode sector + VOA scheme) first,
        then same street, then same postcode sector, nearest first, with Zone A price, ITZA, RV and distance.

        Args:
            uarn: VOA unique address reference number of the subject (digits).
        """
        u = clean_uarn(uarn)
        out = data(lambda x: ds(f"/comparables/{int(x)}", n=12), u)
        subject = out["subject"]
        rows = [{"uarn": str(c["uarn"]), "address": c["address"], "use": c.get("use"), "rv": c.get("rv"),
                 "zone_a_price": c.get("zone_a_price"), "itza": round(c["itza"], 2) if c.get("itza") else None,
                 "same_segment": c.get("same_segment"), "same_street": c.get("same_street"),
                 "distance_m": c.get("distance_m")} for c in out["comparables"]]
        seen.update([str(subject["uarn"])] + [r["uarn"] for r in rows])
        ctx["calls"] += 1
        job.step("advocate", "tool_comparables",
                 f"Pulled {len(rows)} comparables around {short_address(subject['address'])}",
                 tokens=tokens_once(), uarn=str(subject["uarn"]))
        return json.dumps({"subject": {"uarn": str(subject["uarn"]), "address": subject["address"],
                                       "zone_a_price": subject.get("zone_a_price"), "itza": subject.get("itza"),
                                       "segment_key": subject.get("segment_key"), "rv": subject.get("rv")},
                           "comparables": rows}, default=str)


    @beta_tool(name="street_tone")
    def street_tone_tool(segment_key: str) -> str:
        """Zone A tone of one street segment: median, quartiles and spread, plus every shop on it with its
        Zone A price, ITZA, RV, use and z-score.

        Args:
            segment_key: segment key as returned by get_valuation, e.g. 'SHOREDITCH HIGH STREET|E1 6#694626'.
        """
        key = str(segment_key).strip()[:200]
        if "|" not in key:
            raise ToolError("segment_key looks like 'STREET|SECTOR#SCHEME'; get it from get_valuation")
        out = data(lambda k: ds("/street_tone", segment_key=k), key)
        seg = out["segments"][0]
        shops = [{"uarn": str(s["uarn"]), "address": s["address"], "use": s.get("use"), "rv": s.get("rv"),
                  "zone_a_price": s["zone_a_price"], "itza": round(s["itza"], 2) if s.get("itza") else None,
                  "z_score": s.get("z_score")} for s in seg["shops"]]
        seen.update(s["uarn"] for s in shops)
        ctx["calls"] += 1
        t = seg["tone"]
        street = street_name(key.split("#")[0])
        scheme = key.split("#")[1] if "#" in key else ""
        job.step("advocate", "tool_street_tone",
                 f"Checked the tone of {street}" + (f" (scheme {scheme})" if scheme else "")
                 + f": {t['n']} shops, median {price(t['median'])}", tokens=tokens_once())
        return json.dumps({"segment_key": seg["segment_key"], "tone": t, "shops": shops}, default=str)


    @beta_tool(input_schema=VERDICT_SCHEMA)
    def submit_verdict(verdict: str, reasons: list[dict]) -> str:
        """Submit your final verdict on the evidence pack. Call it exactly once, after checking the data."""
        if ctx["verdict"]:
            return "A verdict was already recorded."
        if verdict not in ("survive", "kill"):
            raise ToolError("verdict must be 'survive' or 'kill'")
        clean = []
        for r in reasons:
            rec = re.sub(r"\D", "", str(r.get("voa_record", "")))
            if rec not in seen:
                raise ToolError(f"voa_record {r.get('voa_record')!r} was not returned by any tool you called. "
                                "Cite the uarn of a record you looked at.")
            text = str(r.get("reason", "")).strip()
            if not text:
                raise ToolError("every reason needs text")
            clean.append({"reason": text, "voa_record": rec})
        ctx["verdict"] = {"verdict": verdict, "reasons": clean}
        return "Verdict recorded."

    pack_json = json.dumps(pack, indent=1)
    runner = claude().beta.messages.tool_runner(
        model=MODEL, max_tokens=8000, betas=BETAS, fallbacks="default", output_config={"effort": "high"},
        system=ADVOCATE_SYSTEM, max_iterations=ADVOCATE_MAX_TURNS,
        tools=[get_valuation_tool, comparables_tool, street_tone_tool, submit_verdict],
        messages=[{"role": "user", "content": f"Evidence pack:\n```json\n{pack_json}\n```"}])
    total_tokens, turns, last_stop = 0, 0, None
    try:
        for message in runner:
            turns += 1
            out_tokens = message.usage.output_tokens
            total_tokens += out_tokens
            ctx["pending_tokens"] = out_tokens
            last_stop = message.stop_reason
            if last_stop == "refusal":
                raise StageFailed("Claude declined to review the pack.")
            runner.generate_tool_call_response()  # runs this turn's tool calls now (the runner reuses the result)
            if ctx["verdict"] or turns >= ADVOCATE_MAX_TURNS:
                break
    finally:
        runner.close()
    verdict = ctx["verdict"]
    if not verdict:
        raise StageFailed(f"The advocate did not reach a verdict within {turns} turns (last stop: {last_stop}).")

    row = sb_insert("verdicts", {"case_id": job.case_id, "pack_id": job.pack_id, "verdict": verdict["verdict"],
                                 "reasons": verdict["reasons"],
                                 "cited_records": sorted({r["voa_record"] for r in verdict["reasons"]}),
                                 "advocate_version": ADVOCATE_VERSION})
    survive = verdict["verdict"] == "survive"
    sb_update("packs", {"pack_id": job.pack_id}, {"status": "survive" if survive else "killed"})
    with job.lock:
        job.verdict = verdict
    first = verdict["reasons"][0]["reason"]
    job.step("advocate", "verdict",
             ("Verdict: the case survives. " if survive else "Verdict: case killed. ") + first,
             tokens=tokens_once(), verdict=verdict["verdict"], verdict_id=row["verdict_id"],
             turns=turns, tool_calls=ctx["calls"], total_output_tokens=total_tokens)
    job.finish("advocate", advocate_summary(verdict))
    return verdict


def advocate_summary(verdict):
    """A short status line; the reasons themselves are shown once, in the verdict box."""
    n = len(verdict.get("reasons") or [])
    points = f"{n} point{'s' if n != 1 else ''}"
    if verdict.get("verdict") == "survive":
        return f"Survived: {points} checked, none would stop the case"
    return f"Killed: {n} reason{'s' if n != 1 else ''} the VOA would likely win"


# --- owner gate --------------------------------------------------------------------------
def owner_gate(job, verdict):
    if verdict["verdict"] != "survive":
        sb_update("candidates", {"candidate_id": job.candidate_id}, {"status": "killed"})
        bump_run(job.run_id, "packs_killed")
        log_event("orchestrator", "case_closed", "Not filed: the VOA advocate killed the case, so nothing goes "
                  "to the VOA and you pay nothing", stage="advocate", candidate_id=job.candidate_id,
                  case_id=job.case_id, pack_id=job.pack_id, run_id=job.run_id, payload={"job_id": job.job_id})
        with job.lock:
            for s in job.stages[3:]:
                s["status"] = "skipped"
            job.stages[3]["summary"] = "Nothing to approve: the advocate killed the case"
        return
    ts = now()
    sb_insert("cases", {
        "case_id": job.case_id, "pack_id": job.pack_id, "candidate_id": job.candidate_id, "uarn": job.uarn,
        "stage": "waiting_owner", "owner_approved": False, "next_action": "Owner to approve filing the Check",
        "submission_mode": "mock",
        "history": [{"ts": iso(ts), "event": "case_opened", "stage": "waiting_owner",
                     "note": "The VOA advocate's review survived; waiting for the owner to approve filing the Check"}]})
    sb_update("candidates", {"candidate_id": job.candidate_id}, {"status": "waiting_owner"})
    sb_update("packs", {"pack_id": job.pack_id}, {"status": "waiting_owner"})
    job.start("owner")
    job.step("owner", "case_opened", f"Case {job.case_ref} opened: waiting for you to approve filing the Check")
    job.set("owner", "waiting", "Waiting for you to approve filing the Check")


# --- case view, derived from the rows (works after a restart) --------------------------------
def case_events(case_id):
    return sb_select("events", select="ts,agent,kind,message,tokens,payload", case_id=f"eq.{case_id}",
                     order="ts.asc", limit="500")


def group_events(events):
    by = {}
    for e in events:
        key = (e.get("payload") or {}).get("stage")
        if key in STAGE_KEYS:
            by.setdefault(key, []).append(e)
    return by


def _fill_steps(stage, events):
    stage["steps"] = [{"ts": e["ts"], "text": e["message"]} for e in events]
    if events:
        stage["started_at"] = events[0]["ts"]


def tail_stages(case, verdict, by, pack):
    stages = blank_stages()[3:]
    owner, check, challenge, decision = stages
    for s in stages:
        _fill_steps(s, by.get(s["key"], []))
    v = (verdict or {}).get("verdict")
    if v and v != "survive":
        for s in stages:
            s["status"] = "skipped"
        owner["summary"] = "Nothing to approve: the advocate killed the case"
        return stages
    if not case:
        return stages
    st, hist = case.get("stage"), case.get("history") or []
    replies = [h for h in hist if h.get("event") == "voa_decision"]
    check_entry = next((h for h in replies if h.get("at") == "check"), None)
    check_reply = (check_entry or {}).get("reply")

    if case.get("owner_approved"):
        owner.update(status="done", finished_at=case.get("owner_approved_at"),
                     summary=f"Approved on {fmt_date(case.get('owner_approved_at'))} at "
                             f"{fmt_time(case.get('owner_approved_at'))}")
    else:
        owner.update(status="waiting", summary="Waiting for you to approve filing the Check")
        owner["started_at"] = owner["started_at"] or case.get("created_at")

    if case.get("submitted_at"):
        # the Check stays open (waiting) until the VOA's decision on it is in the history
        filed = f"Filed as {case.get('external_case_ref')} on {fmt_date(case['submitted_at'])}"
        started = check["started_at"] or case["submitted_at"]
        if check_reply:
            decided = "no change" if check_reply == "unchanged" else "value reduced"
            check.update(status="done", started_at=started,
                         finished_at=check_entry.get("ts") or case["submitted_at"],
                         summary=f"{filed}; VOA decision: {decided}")
        elif st in CHECK_STAGES:
            check.update(status="waiting", started_at=started,
                         summary=f"{filed}; waiting for the VOA's decision, mock target "
                                 f"{fmt_date(case.get('next_deadline'))}")
        else:
            check.update(status="done", started_at=started, finished_at=case["submitted_at"], summary=filed)

    if st in CHALLENGE_STAGES:
        challenge.update(status="waiting",
                         summary=f"{case.get('next_action') or 'Challenge in progress'}, "
                                 f"deadline {fmt_date(case.get('next_deadline'))}")
    elif st in ("resolved", "closed"):
        if check_reply == "reduced":
            challenge.update(status="skipped", summary="Not needed: the value was reduced at Check")
        elif check_reply == "unchanged":
            ch = next((h for h in replies if h.get("at") == "challenge"), None)
            challenge.update(status="done", finished_at=(ch or {}).get("ts"),
                             summary="Challenge decided" + (f": {ch.get('reply')}" if ch else ""))

    if st == "resolved":
        outcome = case.get("outcome")
        if outcome in ("reduced", "partially_reduced"):
            summary = (f"Rateable value reduced from {gbp(num(pack.get('current_rv')))} to "
                       f"{gbp(num(case.get('final_rv')))}; saving {gbp(num(case.get('confirmed_annual_saving')))} "
                       "a year, refunded by the council (mock)")
        else:
            summary = f"Outcome: {outcome or 'unknown'}"
        decision.update(status="done", started_at=decision["started_at"] or case.get("resolved_at"),
                        finished_at=case.get("resolved_at"), summary=summary)
    elif st in CHALLENGE_STAGES:
        decision.update(summary="After the VOA decides the Challenge")
    return stages


def head_stages(cand, pack, verdict, by):
    stages = blank_stages()[:3]
    scout, builder, advocate = stages
    for s in stages:
        _fill_steps(s, by.get(s["key"], []))
        if s["steps"]:
            s["finished_at"] = s["steps"][-1]["ts"]
    scout.update(status="done", summary=scout_summary(num(cand.get("zone_a_price")), num(cand.get("street_median")),
                                                      float(cand.get("z_score") or 0)) if cand else None)
    builder.update(status="done", summary=builder_summary(case_ref_of(pack) or f"v{pack.get('pack_version', 1)}",
                                                          len(pack.get("comparables") or []),
                                                          num(pack["proposed_rv"]), num(pack["annual_saving"])))
    if not builder["finished_at"]:
        builder["finished_at"] = pack.get("created_at")
    if verdict:
        advocate.update(status="done", summary=advocate_summary(verdict))
        if not advocate["finished_at"]:
            advocate["finished_at"] = verdict.get("created_at")
    elif pack.get("status") in ("built", "reviewing"):
        advocate.update(status="failed", summary="The advocate did not return a verdict")
    return stages


_comp_cache = {}


def enrich_comparables(uarn, comps):
    """Add address, RV and ITZA to the pack's comparables, all read from the data service."""
    key = (str(uarn), tuple(c["uarn"] for c in comps))
    if key in _comp_cache:
        return copy.deepcopy(_comp_cache[key])
    info = {}
    try:
        for c in ds(f"/comparables/{int(uarn)}", n=20)["comparables"]:
            info[str(c["uarn"])] = c
    except DemoError:
        pass
    out = []
    for c in comps:
        extra = info.get(str(c["uarn"]))
        if extra is None:
            try:
                v = get_valuation(c["uarn"])
                extra = {"address": v["entry"]["full_address"], "rv": v["entry"].get("rv"),
                         "itza": (v.get("shop") or {}).get("itza"),
                         "same_segment": None}
            except DemoError:
                extra = {}
        out.append({**c, "address": extra.get("address"), "rv": extra.get("rv"), "itza": extra.get("itza"),
                    "same_segment": extra.get("same_segment")})
    _comp_cache[key] = out
    return copy.deepcopy(out)


def load_case_rows(case_id):
    pack = sb_one("packs", select="*", case_id=f"eq.{case_id}")
    if not pack:
        raise NotFound("No case with this id.")
    case = sb_one("cases", select="*", case_id=f"eq.{case_id}")
    verdict = sb_one("verdicts", select="verdict,reasons,cited_records,advocate_version,created_at",
                     case_id=f"eq.{case_id}", order="created_at.desc")
    cand = sb_one("candidates", select="candidate_id,run_id,z_score,est_saving,street_median,zone_a_price,"
                  "street_spread,address,postcode,status", candidate_id=f"eq.{pack['candidate_id']}")
    return pack, case, verdict, cand


def case_view(case_id):
    pack, case, verdict, cand = load_case_rows(case_id)
    events = case_events(case_id)
    by = group_events(events)
    job = next((j for j in JOBS.values() if j.planned_case_id == case_id), None)
    if job and job.status == "running":
        stages = job.to_dict()["stages"]
    else:
        stages = head_stages(cand, pack, verdict, by) + tail_stages(case, verdict, by, pack)
    c = case or {}
    return {
        "case_id": case_id, "uarn": pack["uarn"], "address": pack["address"],
        "stage": c.get("stage"), "owner_approved": bool(c.get("owner_approved")),
        "owner_approved_at": c.get("owner_approved_at"),
        "external_case_ref": c.get("external_case_ref"), "submitted_at": c.get("submitted_at"),
        "resolved_at": c.get("resolved_at"),
        "next_deadline": c.get("next_deadline"), "next_action": c.get("next_action"),
        "outcome": c.get("outcome"), "final_rv": num(c.get("final_rv")),
        "confirmed_annual_saving": num(c.get("confirmed_annual_saving")), "history": c.get("history") or [],
        "submission_mode": c.get("submission_mode", "mock"),
        "pack": {"pack_id": pack["pack_id"], "case_ref": case_ref_of(pack), "pack_version": pack["pack_version"],
                 "current_rv": num(pack["current_rv"]), "proposed_rv": num(pack["proposed_rv"]),
                 "annual_saving": num(pack["annual_saving"]),
                 "comparables": enrich_comparables(pack["uarn"], pack.get("comparables") or []),
                 "calc": pack.get("calc"), "argument": pack["argument"], "status": pack["status"],
                 "builder_version": pack["builder_version"], "created_at": pack.get("created_at"),
                 "proposal": proposal_of(pack)},
        "verdict": {"verdict": verdict["verdict"], "reasons": verdict.get("reasons") or [],
                    "advocate_version": verdict["advocate_version"], "created_at": verdict.get("created_at")}
        if verdict else None,
        "candidate": {"candidate_id": cand["candidate_id"], "z_score": num(cand.get("z_score")),
                      "est_saving": num(cand.get("est_saving")), "street_median": num(cand.get("street_median"))}
        if cand else None,
        "stages": stages,
        "report_url": f"/report/{case_id}",
    }


def _case_row(case_id):
    case = sb_one("cases", select="*", case_id=f"eq.{case_id}")
    if not case:
        if sb_one("packs", select="pack_id", case_id=f"eq.{case_id}"):
            raise Conflict("This pack has no open case (the advocate killed it or it is still under review).")
        raise NotFound("No case with this id.")
    return case


def approve(case_id):
    case = _case_row(case_id)
    if case.get("owner_approved"):
        return case_view(case_id)
    if case["stage"] != "waiting_owner":
        raise Conflict(f"The case is at stage {case['stage']}, not waiting for approval.")
    pack = sb_one("packs", select="pack_id,pack_version,address", case_id=f"eq.{case_id}")
    ts = now()
    ref = "MOCK-CHK-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    deadline = add_months(ts, 12)
    history = (case.get("history") or []) + [
        {"ts": iso(ts), "event": "owner_approved", "stage": "waiting_owner",
         "note": "The owner approved filing the Check"},
        {"ts": iso(ts), "event": "check_submitted", "stage": "check_submitted", "external_case_ref": ref,
         "note": f"Check filed with the VOA (mock) as {ref}; no real submission was made"},
    ]
    rows = sb("PATCH", "cases", {"case_id": f"eq.{case_id}", "stage": "eq.waiting_owner"}, {
        "owner_approved": True, "owner_approved_at": iso(ts), "owner_approval_version": pack["pack_version"],
        "stage": "check_submitted", "external_case_ref": ref, "submitted_at": iso(ts),
        "next_deadline": iso(deadline), "next_action": "Wait for the VOA Check decision", "history": history,
    }, prefer="return=representation").json()
    if not rows:  # someone else approved it in the meantime
        return case_view(case_id)
    sb_update("packs", {"case_id": case_id}, {"status": "approved"})
    sb_update("candidates", {"candidate_id": case["candidate_id"]}, {"status": "approved"})
    common = {"candidate_id": case["candidate_id"], "case_id": case_id, "pack_id": case["pack_id"]}
    log_event("orchestrator", "owner_approved", f"You approved filing the Check for {short_address(pack['address'])}",
              stage="owner", ts=iso(ts), key=f"demo:{case_id}:approved", **common)
    log_event("orchestrator", "check_filed",
              f"Check filed with the VOA (mock) as {ref}. Mock VOA target for a decision: {fmt_date(deadline)}",
              stage="check", ts=iso(ts + timedelta(milliseconds=1)), key=f"demo:{case_id}:check_filed",
              payload={"external_case_ref": ref, "submission_mode": "mock"}, **common)
    return case_view(case_id)


def simulate(case_id, reply):
    """MOCK ONLY: pretend the VOA replied."""
    if reply not in ("reduced", "unchanged"):
        raise BadRequest("reply must be 'reduced' or 'unchanged'")
    case = _case_row(case_id)
    if not case.get("owner_approved"):
        raise Conflict("Approve the filing first.")
    st = case["stage"]
    if st in ("resolved", "closed"):
        raise Conflict("This case is already resolved.")
    if st not in CHECK_STAGES + CHALLENGE_STAGES:
        raise Conflict(f"Nothing to reply to at stage {st}.")
    if reply == "unchanged" and st not in CHECK_STAGES:
        raise Conflict("An unchanged reply can only be simulated while the Check is with the VOA.")
    pack = sb_one("packs", select="pack_id,current_rv,proposed_rv,annual_saving,address", case_id=f"eq.{case_id}")
    ts = now()
    at = "check" if st in CHECK_STAGES else "challenge"
    history = list(case.get("history") or [])
    common = {"candidate_id": case["candidate_id"], "case_id": case_id, "pack_id": case["pack_id"], "ts": iso(ts),
              "payload": {"mock": True, "reply": reply, "at": at}}
    n = len(history)
    if reply == "reduced":
        history.append({"ts": iso(ts), "event": "voa_decision", "at": at, "reply": "reduced", "stage": "resolved",
                        "note": f"Mock VOA reply: value reduced to {gbp(num(pack['proposed_rv']))}"})
        patch = {"stage": "resolved", "outcome": "reduced", "final_rv": pack["proposed_rv"],
                 "confirmed_annual_saving": pack["annual_saving"], "resolved_at": iso(ts),
                 "next_action": "Council to refund the overpayment", "next_deadline": None, "history": history}
        sb_update("cases", {"case_id": case_id}, patch)
        log_event("caseworker", "voa_reply",
                  f"VOA replied to the {at.capitalize()} (simulated): rateable value reduced", stage=at,
                  key=f"demo:{case_id}:reply:{n}", **common)
        common["ts"] = iso(ts + timedelta(milliseconds=1))
        log_event("caseworker", "decision",
                  f"Rateable value reduced from {gbp(num(pack['current_rv']))} to {gbp(num(pack['proposed_rv']))}: "
                  f"saving {gbp(num(pack['annual_saving']))} a year; the council refunds the overpayment (mock)",
                  stage="decision", key=f"demo:{case_id}:decision", **common)
    else:
        deadline = add_months(ts, 4)
        history.append({"ts": iso(ts), "event": "voa_decision", "at": "check", "reply": "unchanged",
                        "stage": "challenge_draft", "note": "Mock VOA reply: no change at Check"})
        patch = {"stage": "challenge_draft", "next_action": "Draft the Challenge", "next_deadline": iso(deadline),
                 "history": history}
        sb_update("cases", {"case_id": case_id}, patch)
        log_event("caseworker", "voa_reply", "VOA replied to the Check (simulated): no change to the value",
                  stage="check", key=f"demo:{case_id}:reply:{n}", **common)
        common["ts"] = iso(ts + timedelta(milliseconds=1))
        log_event("caseworker", "challenge_next",
                  f"Drafting the Challenge: it must reach the VOA within 4 months, by {fmt_date(deadline)}",
                  stage="challenge", key=f"demo:{case_id}:challenge:{n}", **common)
    return case_view(case_id)


# --- feed and area -------------------------------------------------------------------------
def list_events(case_id=None, candidate_id=None, limit=50):
    params = {"select": "ts,agent,kind,message,tokens", "order": "ts.desc", "limit": str(limit)}
    if case_id:
        params["case_id"] = f"eq.{case_id}"
    elif candidate_id:
        params["candidate_id"] = f"eq.{candidate_id}"
    return {"events": sb_select("events", **params)}


def area():
    run = sb_one("scout_runs", select="run_id,area_name,status,properties_checked,candidates_flagged,finished_at",
                 area_code=f"eq.{HACKNEY_AREA}", order="created_at.desc")
    if not run:
        return {"run": None, "candidates": []}
    rows = sb_select("candidates", select="uarn,address,latitude,longitude,zone_a_price,street_median,z_score,"
                     "est_saving,flags,status", run_id=f"eq.{run['run_id']}", order="est_saving.desc.nullslast",
                     limit="1000")
    for r in rows:
        for k in ("zone_a_price", "street_median", "z_score", "est_saving"):
            r[k] = num(r.get(k))
    return {"run": run, "candidates": rows}
