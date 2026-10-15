"""Code checks every pack must pass before anything is written.

The builder's model proposes; this module decides. Every comparable is looked up
in the data service, so a comparable the model invented can't get through.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agents.builder import rates
from orchestrator.data_client import DataClient, NotFound

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts" / "pack.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)

COMP_MAX_DISTANCE_M = float(os.getenv("COMP_MAX_DISTANCE_M", "200"))
SIZE_RATIO_MIN, SIZE_RATIO_MAX = 0.5, 2.0   # comparable floor area vs the subject's
MAX_COMPARABLES = 6
EXCLUDE_BEYOND_SPREADS = 2.0                 # an eligible comparable may be left out only past this
MAX_EXCLUSIONS = 1                           # and at most one: a block priced like the subject
                                             # (e.g. 221-233 Old Street) can't be excluded away
SUBJECT_TWIN_SPREADS = 1.0                   # never one within this of the subject's own price
PRICE_TOL = 0.5                              # GBP/m2, for rounding
DISTANCE_TOL_M = 0.5


def itza(valuation: dict) -> float:
    """Area 'in terms of Zone A'. The data service's figure when it gives one;
    otherwise each zone's area at its fraction of the Zone A price."""
    if valuation.get("itza"):
        return float(valuation["itza"])
    za = valuation["zone_a_price"]
    return sum(z["area_m2"] * z["price_per_m2"] / za for z in valuation["zones"])


def floor_area(valuation: dict) -> float:
    """Floor area from the summary valuation (total area); the zones' sum as a fallback."""
    return float(valuation.get("floor_area_m2") or sum(z["area_m2"] for z in valuation["zones"]))


def same_use(a: dict, b: dict) -> bool:
    """Same use = same use_class (the VOA scheme code for real data)."""
    return (a.get("use_class") or a["use"]) == (b.get("use_class") or b["use"])


def spreads_from_median(zone_a_price: float, tone: dict) -> float:
    """How many spreads from the street median. spread = 1.4826 x MAD, at least
    5% of the median (Person 1's definition, used by the Scout, builder and advocate)."""
    spread = tone["spread"] or 1
    return (zone_a_price - tone["median_zone_a"]) / spread


def eligible_comparables(uarn: str, data: DataClient) -> list[dict]:
    """The comparables a pack must use: every same-use property on the subject's
    street segment within COMP_MAX_DISTANCE_M, with floor area between half and
    double the subject's, nearest first, at most MAX_COMPARABLES.

    Each entry carries spreads_from_median, spreads_from_subject and whether it
    may be excluded: only when it is more than EXCLUDE_BEYOND_SPREADS from the
    street median AND more than SUBJECT_TWIN_SPREADS from the subject's own price.
    A neighbour priced like the subject is the best evidence of how the VOA prices
    that spot, so it is never left out.
    """
    subject = data.get_valuation(uarn)
    tone = data.street_tone(subject["street_key"])
    spread = tone["spread"] or 1
    out = []
    for c in data.comparables(uarn, max_distance=COMP_MAX_DISTANCE_M, same_use=True, limit=200):
        record = data.get_valuation(c["uarn"])
        ratio = floor_area(record) / floor_area(subject)
        if not SIZE_RATIO_MIN <= ratio <= SIZE_RATIO_MAX:
            continue
        spreads = spreads_from_median(c["zone_a_price"], tone)
        from_subject = (c["zone_a_price"] - subject["zone_a_price"]) / spread
        out.append({"uarn": c["uarn"], "zone_a_price": c["zone_a_price"],
                    "distance_m": c["distance_m"], "use": c["use"],
                    "postcode": c.get("postcode"),
                    "floor_area_ratio": round(ratio, 2),
                    "spreads_from_median": round(spreads, 2),
                    "spreads_from_subject": round(from_subject, 2),
                    "may_exclude": abs(spreads) > EXCLUDE_BEYOND_SPREADS
                    and abs(from_subject) > SUBJECT_TWIN_SPREADS})
        if len(out) == MAX_COMPARABLES:
            break
    return out


def schema_errors(pack: dict) -> list[str]:
    return [f"schema: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in sorted(VALIDATOR.iter_errors(pack), key=lambda e: list(e.absolute_path))]


def check_pack(pack: dict[str, Any], data: DataClient) -> list[str]:
    """Return every problem found; an empty list means the pack can be written."""
    errors = schema_errors(pack)
    if errors:
        return errors   # the rest assumes the shape is right

    try:
        subject = data.get_valuation(pack["uarn"])
    except NotFound:
        return [f"subject uarn {pack['uarn']} does not exist in the data service"]

    if pack["address"] != subject["address"]:
        errors.append(f"address should be {subject['address']!r}")
    if pack["current_rv"] != subject["rv"]:
        errors.append(f"current_rv is {pack['current_rv']}, data service says {subject['rv']}")
    if abs(pack["calc"]["zone_a_price"] - subject["zone_a_price"]) > PRICE_TOL:
        errors.append(f"calc.zone_a_price is {pack['calc']['zone_a_price']}, "
                      f"data service says {subject['zone_a_price']}")

    tone = data.street_tone(subject["street_key"])
    if abs(pack["calc"]["street_median"] - tone["median_zone_a"]) > PRICE_TOL:
        errors.append(f"calc.street_median is {pack['calc']['street_median']}, "
                      f"street_tone says {tone['median_zone_a']}")
    if abs(pack["calc"]["street_spread"] - tone["spread"]) > PRICE_TOL:
        errors.append(f"calc.street_spread is {pack['calc']['street_spread']}, "
                      f"street_tone says {tone['spread']}")
    if tone.get("mad") == 0:
        errors.append(f"no clear case: the street segment's prices have no spread (MAD 0 over "
                      f"{tone.get('count')} shops), so its spread is only the 5% floor and "
                      "'spreads above the median' means nothing here: call no_case")

    # Everything on the subject's street segment, any use, as the data service sees it.
    pool = {c["uarn"]: c for c in data.comparables(
        pack["uarn"], max_distance=COMP_MAX_DISTANCE_M, same_use=False, limit=200)}

    seen: set[str] = set()
    comp_prices: list[float] = []
    comp_records: list[dict] = []
    for i, comp in enumerate(pack["comparables"]):
        tag = f"comparables[{i}] {comp['uarn']}"
        if comp["uarn"] in seen:
            errors.append(f"{tag}: listed twice")
            continue
        seen.add(comp["uarn"])
        if comp["uarn"] == pack["uarn"]:
            errors.append(f"{tag}: is the subject itself")
            continue
        try:
            record = data.get_valuation(comp["uarn"])
        except NotFound:
            errors.append(f"{tag}: does not exist in the data service")
            continue
        listed = pool.get(comp["uarn"])
        if listed is None:
            errors.append(f"{tag}: not on the subject's street segment "
                          f"(within {COMP_MAX_DISTANCE_M:.0f} m)")
            continue
        if not same_use(record, subject):
            errors.append(f"{tag}: use is {record['use']!r}, subject is {subject['use']!r}; "
                          "only same-use comparables are allowed")
        if comp["use"] != record["use"]:
            errors.append(f"{tag}: use should be {record['use']!r}")
        if abs(comp["zone_a_price"] - record["zone_a_price"]) > PRICE_TOL:
            errors.append(f"{tag}: zone_a_price should be {record['zone_a_price']}")
        if abs(comp["distance_m"] - listed["distance_m"]) > DISTANCE_TOL_M:
            errors.append(f"{tag}: distance_m should be {listed['distance_m']}")
        ratio = floor_area(record) / floor_area(subject)
        if not SIZE_RATIO_MIN <= ratio <= SIZE_RATIO_MAX:
            errors.append(f"{tag}: floor area is {ratio:.2f}x the subject's; "
                          f"must be {SIZE_RATIO_MIN}-{SIZE_RATIO_MAX}x")
        comp_prices.append(record["zone_a_price"])
        comp_records.append(record)

    # No cherry-picking: the pack must use every eligible comparable, and may leave
    # one out only if it is more than EXCLUDE_BEYOND_SPREADS from the street median.
    eligible = {c["uarn"]: c for c in eligible_comparables(pack["uarn"], data)}

    # A lower price that only comes from another postcode, while a neighbour in the
    # subject's own postcode is priced like it, is a different building's tone
    # (Seven Sisters Road c_0101), not evidence that the subject is overvalued.
    here = subject.get("postcode")
    supporting = [r for r in comp_records if r["zone_a_price"] < subject["zone_a_price"]]
    twins = [e for e in eligible.values() if here and e.get("postcode") == here
             and abs(e["spreads_from_subject"]) <= SUBJECT_TWIN_SPREADS]
    if here and supporting and twins and all(r.get("postcode") != here for r in supporting):
        errors.append(f"no clear case: every comparable priced below the candidate is in a "
                      f"different postcode, while {twins[0]['uarn']} in its own postcode ({here}) "
                      f"is priced like it (£{twins[0]['zone_a_price']}); the lower price is another "
                      "building's tone: call no_case")
    excluded: dict[str, dict] = {}
    if len(pack.get("exclusions", [])) > MAX_EXCLUSIONS:
        errors.append(f"exclusions: {len(pack['exclusions'])} listed; a pack may leave out at most "
                      f"{MAX_EXCLUSIONS} eligible comparable. If the neighbours you'd leave out are "
                      "priced like the candidate, there is no clear case: call no_case")
    for i, ex in enumerate(pack.get("exclusions", [])):
        tag = f"exclusions[{i}] {ex['uarn']}"
        if ex["uarn"] in seen or ex["uarn"] in excluded:
            errors.append(f"{tag}: listed twice, or both used and excluded")
            continue
        excluded[ex["uarn"]] = ex
        e = eligible.get(ex["uarn"])
        if e is None:
            errors.append(f"{tag}: not an eligible comparable, so it needs no exclusion")
            continue
        if not e["may_exclude"]:
            if abs(e["spreads_from_median"]) <= EXCLUDE_BEYOND_SPREADS:
                errors.append(f"{tag}: is {e['spreads_from_median']} spreads from the street "
                              f"median; only beyond {EXCLUDE_BEYOND_SPREADS:g} may be excluded, "
                              "so use it")
            else:
                errors.append(f"{tag}: is within {SUBJECT_TWIN_SPREADS:g} spread of the "
                              f"candidate's own price ({e['spreads_from_subject']} spreads); a "
                              "neighbour priced like the candidate can never be excluded, so use it")
        if abs(ex["zone_a_price"] - e["zone_a_price"]) > PRICE_TOL:
            errors.append(f"{tag}: zone_a_price should be {e['zone_a_price']}")
        if abs(ex["distance_m"] - e["distance_m"]) > DISTANCE_TOL_M:
            errors.append(f"{tag}: distance_m should be {e['distance_m']}")
    for uarn, e in eligible.items():
        if uarn not in seen and uarn not in excluded:
            errors.append(f"eligible comparable {uarn} (£{e['zone_a_price']}, {e['distance_m']} m, "
                          f"{e['spreads_from_median']} spreads) is missing: use it, or list it in "
                          "exclusions if it is more than 2 spreads from the median")
    for uarn in seen - eligible.keys() - {pack["uarn"]}:
        if uarn in pool:
            errors.append(f"comparable {uarn} is not among the {MAX_COMPARABLES} nearest "
                          "eligible comparables")

    if pack["proposed_rv"] >= pack["current_rv"]:
        errors.append("proposed_rv must be below current_rv")

    # The proposed value must come from a Zone A price inside the comparables' range.
    if comp_prices:
        implied = pack["proposed_rv"] / itza(subject)
        lo, hi = min(comp_prices), max(comp_prices)
        if not lo - 1 <= implied <= hi + 1:
            errors.append(f"proposed_rv implies a Zone A price of £{implied:.2f}/m2, outside "
                          f"the comparables' range £{lo}-£{hi}; proposed_rv = ITZA "
                          f"({itza(subject):.2f} m2) x the Zone A price you argue for")

    expected = rates.annual_saving(subject["rv"], pack["proposed_rv"], rates.use_of(subject))
    if pack["proposed_rv"] < subject["rv"] and pack["annual_saving"] != expected:
        errors.append(f"annual_saving should be {expected} "
                      f"(bill at current_rv minus bill at proposed_rv, 2026 multipliers)")

    return errors
