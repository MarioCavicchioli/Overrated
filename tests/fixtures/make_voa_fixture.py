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
TWIN = "TWIN STREET|N4 1#SCH5"      # like Seven Sisters Road c_0101: MAD 0, two buildings
PAIR = "PAIR STREET|N5 1#SCH6"      # two buildings priced apart, but a real spread
ARCADE = "ARCADE STREET|E9 1#SCH7"  # an adult gaming centre under the shop code, and a
                                    # subject with value outside its zones
# Value outside the zones (stores, upper floors), included in the list value.
EXTRA_VALUE = {900000401: 12000}

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
    # TWIN STREET: subject and its twin at £290 in one building, three at £245 in the
    # next building 42 m away. Five shops, MAD 0, spread = the 5% floor.
    900000201: (290, (32, 26, 0), 60, "N4 1AA", TWIN, "SHOP AND PREMISES"),        # subject
    900000202: (290, (40, 30, 0), 73, "N4 1AA", TWIN, "SHOP AND PREMISES"),
    900000203: (245, (31, 25, 0), 58, "N4 1FS", TWIN, "SHOP AND PREMISES"),
    900000204: (245, (33, 26, 0), 61, "N4 1FS", TWIN, "SHOP AND PREMISES (DABW)"),
    900000205: (245, (30, 27, 0), 59, "N4 1FS", TWIN, "SHOP AND PREMISES (DABW)"),
    # PAIR STREET: subject and twin at £300 in one postcode, cheaper shops in the
    # next postcode 50 m away, more shops 400 m off. MAD 15, so the spread is real.
    900000301: (300, (30, 20, 0), 52, "N5 1AA", PAIR, "SHOP AND PREMISES"),        # subject
    900000302: (300, (31, 20, 0), 53, "N5 1AA", PAIR, "SHOP AND PREMISES"),
    900000303: (240, (30, 21, 0), 52, "N5 1FS", PAIR, "SHOP AND PREMISES"),
    900000304: (250, (29, 20, 0), 50, "N5 1FS", PAIR, "SHOP AND PREMISES"),
    900000305: (255, (30, 20, 0), 52, "N5 1FS", PAIR, "SHOP AND PREMISES"),
    900000306: (270, (30, 20, 0), 52, "N5 1ZZ", PAIR, "SHOP AND PREMISES"),
    900000307: (230, (30, 20, 0), 52, "N5 1ZZ", PAIR, "SHOP AND PREMISES"),
    # ARCADE STREET: the nearest property, in the subject's own postcode, is an adult
    # gaming centre under the same shop code 249: never a shop comparable.
    900000401: (600, (30, 20, 0), 55, "E9 1AA", ARCADE, "SHOP AND PREMISES"),       # subject
    900000402: (420, (30, 20, 0), 55, "E9 1AA", ARCADE, "ADULT GAMING CENTRE AND PREMISES"),
    900000403: (400, (30, 21, 0), 56, "E9 1AB", ARCADE, "SHOP AND PREMISES"),
    900000404: (410, (29, 20, 0), 54, "E9 1AB", ARCADE, "SHOP AND PREMISES (DABW)"),
    900000405: (420, (30, 20, 0), 55, "E9 1AB", ARCADE, "SHOP"),
    900000406: (450, (31, 20, 0), 56, "E9 1AB", ARCADE, "SHOP AND PREMISES"),
    900000407: (380, (30, 19, 0), 54, "E9 1AB", ARCADE, "SHOP AND PREMISES"),
}
# Distance from each postcode centre to the subject's (same postcode = 0 m).
DISTANCE = {"E8 1AA": 0, "E8 1AB": 60, "E8 1AC": 140, "E8 1AD": 90, "E8 9ZZ": None,
            "EC1 9AA": 0, "EC1 9AB": 150, "N4 1AA": 0, "N4 1FS": 42,
            "N5 1AA": 0, "N5 1FS": 50, "N5 1ZZ": 400, "E9 1AA": 0, "E9 1AB": 40}
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
        "rv": int(round(za * itza + EXTRA_VALUE.get(uarn, 0), -2)), "total_area": total,
        "adopted_rv": int(round(za * itza + EXTRA_VALUE.get(uarn, 0), -2)),
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
                      for seg in (SEGMENT, OTHER_SEGMENT, BLOCK, TWIN, PAIR, ARCADE)})
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
