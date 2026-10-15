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
PRICE_TOL = 0.5                              # GBP/m2, for rounding
DISTANCE_TOL_M = 0.5


def itza(valuation: dict) -> float:
    """Area 'in terms of Zone A': each zone's area at its fraction of the Zone A price."""
    za = valuation["zone_a_price"]
    return sum(z["area_m2"] * z["price_per_m2"] / za for z in valuation["zones"])


def floor_area(valuation: dict) -> float:
    return sum(z["area_m2"] for z in valuation["zones"])


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
    if abs(pack["calc"]["street_spread"] - tone["spread_mad"]) > PRICE_TOL:
        errors.append(f"calc.street_spread is {pack['calc']['street_spread']}, "
                      f"street_tone says {tone['spread_mad']}")

    # Everything on the subject's street segment, any use, as the data service sees it.
    pool = {c["uarn"]: c for c in data.comparables(
        pack["uarn"], max_distance=COMP_MAX_DISTANCE_M, same_use=False, limit=200)}

    seen: set[str] = set()
    comp_prices: list[float] = []
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
        if record["use"] != subject["use"]:
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

    expected = rates.annual_saving(pack["current_rv"], pack["proposed_rv"], subject["use"])
    if abs(pack["annual_saving"] - expected) > 1:
        errors.append(f"annual_saving should be {expected} "
                      f"(bill at current_rv minus bill at proposed_rv, 2026 multipliers)")

    return errors
