"""Run the Case builder on one candidate from the command line.

    python -m agents.builder.run_local 1001008

The orchestrator's watcher does the same through agents.builder.runner.run_builder.
If the candidate isn't in the store yet (mock data), a test candidate row is made.

Needs: DATA_SERVICE_URL (and DATA_SERVICE_KEY with DATA_BACKEND=http), and the
agent and environment created with ant apply (IDs read from
agents/builder/claude-lock.json, or BUILDER_AGENT_ID / BUILDER_ENV_ID).
"""

from __future__ import annotations

import sys

from agents.builder.runner import build_candidate
from orchestrator.config import anthropic_client, load_env
from orchestrator.data_client import DataClient, make_data_client
from orchestrator.store import Store, get_store


def ensure_candidate(store: Store, data: DataClient, uarn: str) -> None:
    """Test only: the Scout (or the app) writes candidates in production."""
    if store.get_candidate(uarn):
        return
    v = data.get_valuation(uarn)
    tone = data.street_tone(v["street_key"])
    store.upsert_candidate({
        "uarn": uarn, "address": v["address"], "postcode": v["postcode"],
        "street_key": v["street_key"], "rv": v["rv"], "zone_a_price": v["zone_a_price"],
        "street_median": tone["median_zone_a"],
        "z_score": round((v["zone_a_price"] - tone["median_zone_a"]) / tone["spread"], 2),
        "flags": ["zone_a_above_street"], "status": "building_pack",
    })


def run(uarn: str) -> None:
    load_env()
    store, data = get_store(), make_data_client()
    ensure_candidate(store, data, uarn)
    status, result = build_candidate(anthropic_client(), store, data, uarn)
    print(f"\nStopped: {result.stop_reason}. Tokens: {result.tokens:,}. Candidate status: {status}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "1001008")
