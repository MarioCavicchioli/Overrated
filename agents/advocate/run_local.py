"""Run the VOA advocate on the test packs in agents/advocate/fixtures.

    python -m agents.advocate.run_local                 # all four fixtures
    python -m agents.advocate.run_local c_0001 c_0004   # some of them

Each fixture's pack is loaded into the local store (SQLITE_PATH) under its own
case_id, then reviewed in its own session. Stops before a session that could
take the run past ADVOCATE_RUN_MAX_CENTS (default 200 = $2.00).

Needs DATA_SERVICE_URL, and the agent and environment created with
`python -m orchestrator.ant apply --lock-file agents/advocate/claude-lock.json
agents/advocate/agent.md agents/advocate/environment.yaml` (IDs read from that
lockfile, or ADVOCATE_AGENT_ID / ADVOCATE_ENV_ID).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agents.advocate import runner
from agents.builder.checks import schema_errors
from orchestrator.config import anthropic_client, env_int, load_env
from orchestrator.data_client import DataClient
from orchestrator.store import Store, get_store

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPECTED = {"c_0001": "kill", "c_0002": "survive", "c_0003": "?", "c_0004": "kill"}


def load_fixtures() -> dict[str, dict]:
    packs = {}
    for path in sorted(FIXTURES.glob("pack_*.json")):
        pack = json.loads(path.read_text(encoding="utf-8"))["pack"]
        packs[pack["case_id"]] = pack
    return packs


def ensure_pack(store: Store, data: DataClient, pack: dict) -> None:
    """Test only: builders write packs in production."""
    if errors := schema_errors(pack):
        raise ValueError(f"{pack['case_id']} fails pack.schema.json: {errors[0]}")
    if store.get_pack(pack["case_id"]):
        return
    if not store.get_candidate(pack["uarn"]):
        v = data.get_valuation(pack["uarn"])
        store.upsert_candidate({"uarn": pack["uarn"], "address": v["address"],
                                "postcode": v["postcode"], "street_key": v["street_key"],
                                "rv": v["rv"], "zone_a_price": v["zone_a_price"],
                                "status": "pack_ready"})
    store.insert_pack({
        "case_id": pack["case_id"], "uarn": pack["uarn"], "pack": pack,
        "comparables": pack["comparables"], "proposed_rv": pack["proposed_rv"],
        "annual_saving": pack["annual_saving"], "argument": pack["argument"],
        "version": pack["version"],
    })


def main(case_ids: list[str]) -> None:
    load_env()
    client, store, data = anthropic_client(), get_store(), DataClient()
    packs = load_fixtures()
    case_ids = case_ids or list(packs)
    run_cap = env_int("ADVOCATE_RUN_MAX_CENTS", 200) / 100
    spent, summary = 0.0, []
    for case_id in case_ids:
        if spent + runner.max_usd() > run_cap:
            print(f"\nStopping before {case_id}: run could pass ${run_cap:.2f} "
                  f"(spent ~${spent:.2f}, session cap ${runner.max_usd():.2f})")
            break
        ensure_pack(store, data, packs[case_id])
        print(f"\n===== {case_id} ({packs[case_id]['address']}) =====")
        verdict, result = runner.review_pack(client, store, data, case_id)
        spent += result.meter.cost_usd
        summary.append((case_id, verdict, result))

    print("\n===== Verdicts =====")
    for case_id, verdict, result in summary:
        got = verdict["verdict"] if verdict else f"none ({result.stop_reason})"
        print(f"\n{case_id}: {got.upper()}  (expected {EXPECTED.get(case_id, '?')}; "
              f"{result.meter.turns} turns, {result.meter.tokens:,} tokens, "
              f"~${result.meter.cost_usd:.2f})")
        for r in (verdict or {}).get("reasons", []):
            print(f"  - [{r['kind']}] {r['voa_record']}: {r['reason']}")
    print(f"\nTotal ~${spent:.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
