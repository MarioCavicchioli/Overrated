"""Write tests/fixtures/voa_service_responses.json: responses in the exact shapes
Person 1's data service returns (data/service.py, data/build.py), with INVENTED
data. Real responses are VOA data and must never be committed (licence), so this
file stands in for a recording. Check the shapes against the live service with
`python -m orchestrator.check_voa_shape <uarn>`, which prints field names only.

    python tests/fixtures/make_voa_fixture.py
"""

import json
import statistics
from pathlib import Path

OUT = Path(__file__).with_name("voa_service_responses.json")
STREET = "TEST STREET|E8 1"
SEGMENT = STREET + "#SCH1"
OTHER_SEGMENT = STREET + "#SCH2"
BLOCK = "BLOCK STREET|EC1 9#SCH9"

# uarn: (zone_a_price, zone A / B / C areas, total_area, postcode, segment, desc_text)
SHOPS = {
    900000001: (600, (40, 30, 20), 95, "E8 1AA", SEGMENT, "SHOP AND PREMISES"),   # subject
    900000002: (420, (38, 28, 0), 70, "E8 1AA", SEGMENT, "SHOP AND PREMISES"),
    900000003: (400, (36, 26, 0), 66, "E8 1AB", SEGMENT, "SHOP"),
    900000004: (440, (42, 30, 10), 88, "E8 1AB", SEGMENT, "SHOP AND PREMISES"),
    900000005: (410, (40, 30, 0), 72, "E8 1AC", SEGMENT, "SHOP AND PREMISES"),
    900000006: (430, (90, 80, 60), 260, "E8 1AC", SEGMENT, "SHOP AND PREMISES"),  # too big
    900000007: (900, (30, 20, 0), 55, "E8 1AD", OTHER_SEGMENT, "SHOP AND PREMISES"),  # other segment
    900000008: (415, (39, 29, 0), 71, "E8 9ZZ", SEGMENT, "SHOP AND PREMISES"),    # no geocode
    # BLOCK STREET, built like 221-233 Old Street: the subject and four neighbours in
    # one postcode all at the same high price, about 2 spreads above the segment.
    900000101: (1030, (24, 12, 0), 40, "EC1 9AA", BLOCK, "SHOP AND PREMISES"),     # subject
    900000102: (1030, (23, 12, 0), 39, "EC1 9AA", BLOCK, "SHOP AND PREMISES"),
    900000103: (1030, (24, 11, 0), 39, "EC1 9AA", BLOCK, "SHOP AND PREMISES"),
    900000104: (1030, (25, 12, 0), 41, "EC1 9AA", BLOCK, "SHOP AND PREMISES"),
    900000105: (1030, (24, 13, 0), 41, "EC1 9AA", BLOCK, "SHOP AND PREMISES"),
    900000106: (700, (24, 12, 0), 40, "EC1 9AB", BLOCK, "SHOP AND PREMISES"),
    900000107: (720, (23, 12, 0), 39, "EC1 9AB", BLOCK, "SHOP AND PREMISES"),
    900000108: (740, (24, 12, 0), 40, "EC1 9AB", BLOCK, "SHOP AND PREMISES"),
    900000109: (750, (25, 12, 0), 41, "EC1 9AB", BLOCK, "SHOP AND PREMISES"),
    900000110: (760, (24, 11, 0), 39, "EC1 9AB", BLOCK, "SHOP AND PREMISES"),
    900000111: (780, (24, 12, 0), 40, "EC1 9AB", BLOCK, "SHOP AND PREMISES"),
    900000112: (800, (23, 13, 0), 40, "EC1 9AB", BLOCK, "SHOP AND PREMISES"),
}
# Distance from each postcode centre to the subject's (same postcode = 0 m).
DISTANCE = {"E8 1AA": 0, "E8 1AB": 60, "E8 1AC": 140, "E8 1AD": 90, "E8 9ZZ": None,
            "EC1 9AA": 0, "EC1 9AB": 150}
LINES = ("Retail Zone A", "Retail Zone B", "Retail Zone C")
FRACTION = (1, 0.5, 0.25)


def shop(uarn: int) -> dict:
    za, areas, total, postcode, segment, desc = SHOPS[uarn]
    itza = sum(a * f for a, f in zip(areas, FRACTION))
    return {
        "uarn": uarn, "address": f"{uarn % 100}, {segment.split('|')[0]}, LONDON",
        "postcode": postcode, "postcode_sector": postcode[:-2].strip(),
        "street_key": segment.split("#")[0], "street": segment.split("|")[0],
        "town": "LONDON", "ba_code": "0000", "scat_code": "249", "desc_text": desc,
        "rv": int(round(za * itza, -2)), "total_area": total, "adopted_rv": int(round(za * itza, -2)),
        "scheme_ref": segment.split("#")[1], "zone_a_price": za, "zone_a_area": areas[0],
        "itza": itza, "segment_key": segment,
    }


def tone(segment: str) -> dict:
    prices = [SHOPS[u][0] for u in SHOPS if SHOPS[u][4] == segment]
    med = statistics.median(prices)
    mad = statistics.median(abs(p - med) for p in prices)
    return {"n": len(prices), "median": med, "p25": sorted(prices)[len(prices) // 4],
            "p75": sorted(prices)[(3 * len(prices)) // 4], "mean": statistics.mean(prices),
            "mad": mad, "segment_key": segment, "spread": max(1.4826 * mad, 0.05 * med)}


def valuation(uarn: int) -> dict:
    s = shop(uarn)
    za, areas = SHOPS[uarn][0], SHOPS[uarn][1]
    lines = [{"floor": "Ground", "description": d, "area": a, "price": za * f, "value": a * za * f}
             for d, a, f in zip(LINES, areas, FRACTION) if a]
    lines.append({"floor": "Ground", "description": "Store", "area": 12, "price": 40, "value": 480})
    t = tone(s["segment_key"])
    z = (za - t["median"]) / t["spread"]
    return {
        "entry": {"ba_code": "0000", "ba_ref": f"REF{uarn}", "desc_code": "CS",
                  "desc_text": s["desc_text"], "uarn": uarn, "full_address": s["address"],
                  "postcode": s["postcode"], "street_key": STREET, "rv": s["rv"],
                  "scat_code": "249", "scat": "249G", "assessment_ref": f"A{uarn}",
                  "composite": "", "county": "", "effective_date": "2026-04-01",
                  "firm_name": "", "from_date": "2026-04-01", "number_name": str(uarn % 100),
                  "postal_district": "", "postcode_sector": s["postcode_sector"],
                  "settlement_code": "", "street": s["street"], "town": "LONDON"},
        "summary": {"assessment_ref": f"A{uarn}", "uarn": uarn, "scheme_ref": s["scheme_ref"],
                    "primary_desc": "CS", "total_area": s["total_area"], "total_value": s["rv"],
                    "adopted_rv": s["rv"], "unit": "m2", "scat_code": "249", "ba_code": "0000",
                    "ba_name": "TEST", "from_date": "2026-04-01", "unadjusted_price": za},
        "lines": lines, "shop": s, "street_tone": t, "z_score": z,
        "flags": ["zone_a_above_tone"] if z > 2 else [],
        "proposal": {"proposed_rv": int(t["median"] * s["itza"]) // 50 * 50, "annual_saving": 0,
                     "est_saving": 0, "years_left": 2.46} if z > 2 else None,
        "latlng": None if DISTANCE[s["postcode"]] is None else [51.5, -0.07],
    }


def comparables(uarn: int) -> dict:
    subject = shop(uarn)
    rows = []
    for other in SHOPS:
        if other == uarn or shop(other)["street_key"] != subject["street_key"]:
            continue
        s = shop(other)
        rows.append({"uarn": other, "address": s["address"], "postcode": s["postcode"],
                     "street_key": STREET, "segment_key": s["segment_key"],
                     "use": s["desc_text"], "rv": s["rv"], "zone_a_price": s["zone_a_price"],
                     "itza": s["itza"], "same_segment": s["segment_key"] == subject["segment_key"],
                     "same_street": True, "distance_m": DISTANCE[s["postcode"]]})
    rows.sort(key=lambda r: (not r["same_segment"], r["distance_m"] if r["distance_m"] is not None
                             else 1e9))
    return {"subject": subject, "comparables": rows}


def street_tone(segment: str) -> dict:
    shops = [{"uarn": u, "address": shop(u)["address"], "postcode": shop(u)["postcode"],
              "rv": shop(u)["rv"], "zone_a_price": SHOPS[u][0], "itza": shop(u)["itza"],
              "use": SHOPS[u][5], "latitude": 51.5, "longitude": -0.07}
             for u in SHOPS if SHOPS[u][4] == segment]
    t = tone(segment)
    for s in shops:
        s["z_score"] = round((s["zone_a_price"] - t["median"]) / t["spread"], 2)
    return {"street_key": STREET, "scat_code": "249", "year": "2026",
            "segments": [{"segment_key": segment, "tone": t, "shops": shops}]}


def main() -> None:
    responses = {f"/get_valuation/{u}": valuation(u) for u in SHOPS}
    responses.update({f"/comparables/{u}": comparables(u) for u in SHOPS})
    responses.update({f"/street_tone?segment_key={seg}": street_tone(seg)
                      for seg in (SEGMENT, OTHER_SEGMENT, BLOCK)})
    responses["/search_properties?q=TEST STREET"] = [
        {"uarn": u, "address": shop(u)["address"], "postcode": shop(u)["postcode"],
         "street_key": STREET, "use": SHOPS[u][5], "scat_code": "249", "rv": shop(u)["rv"],
         "zone_a_price": SHOPS[u][0]} for u in SHOPS]
    OUT.write_text(json.dumps({"_note": "Invented data in the real service's shapes; see "
                                        "make_voa_fixture.py.", "responses": responses},
                              indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(responses)} responses)")


if __name__ == "__main__":
    main()
