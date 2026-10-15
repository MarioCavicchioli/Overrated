"""Compare Person 1's live data service with our fixture, by field names only.

    python -m orchestrator.check_voa_shape <uarn>

Fetches get_valuation, comparables and street_tone for one real UARN and prints
the field paths that the live responses have and the fixture lacks, and the
reverse. It prints no values (they're VOA data) and never prints the API key.
Exit code 1 if the fixture is missing a field the live service has.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from orchestrator.config import load_env
from orchestrator.data_client import VoaDataClient, segment

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "voa_service_responses.json"


def paths(value, prefix: str = "") -> set[str]:
    """Every field path in a JSON value; lists are described by their first item."""
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{prefix}.{k}" if prefix else k
            out.add(p)
            out |= paths(v, p)
    elif isinstance(value, list) and value:
        out |= paths(value[0], prefix + "[]")
    return out


def main(uarn: str) -> int:
    load_env()
    client = VoaDataClient()
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))["responses"]
    sample = {"get_valuation": fixture["/get_valuation/900000001"],
              "comparables": fixture["/comparables/900000001"],
              "street_tone": next(v for k, v in fixture.items() if k.startswith("/street_tone"))}
    live_val = client._get(f"/get_valuation/{segment(uarn)}")
    segment_key = (live_val.get("shop") or {}).get("segment_key")
    live = {"get_valuation": live_val,
            "comparables": client._get(f"/comparables/{segment(uarn)}", n=20),
            "street_tone": (client._get("/street_tone", segment_key=segment_key)
                            if segment_key else {})}
    missing_any = False
    for name in sample:
        have, want = paths(live[name]), paths(sample[name])
        missing, extra = sorted(have - want), sorted(want - have)
        print(f"== {name}: {'same fields' if not (missing or extra) else ''}")
        for p in missing:
            print(f"   live only (add to the fixture): {p}")
        for p in extra:
            print(f"   fixture only (not live, or empty live): {p}")
        missing_any |= bool(missing)
    return 1 if missing_any else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m orchestrator.check_voa_shape <uarn>")
    sys.exit(main(sys.argv[1]))
