"""Run the Case builder on one candidate in a standalone test session.

In production the Scout starts builders inside its workflow runs. This script
is for testing the builder before the Scout exists: it runs the builder as the
session's own agent and answers its tool calls the same way the orchestrator will.

    python -m agents.builder.run_local 1001008

Needs: DATA_SERVICE_URL, and the agent and environment created with ant apply
(IDs read from agents/builder/claude-lock.json, or BUILDER_AGENT_ID / BUILDER_ENV_ID).
"""

from __future__ import annotations

import sys

from agents.builder.handlers import AGENT, BuilderHandlers
from orchestrator import budget
from orchestrator.config import anthropic_client, load_env
from orchestrator.data_client import DataClient
from orchestrator.sessions import resource_id, run_session
from orchestrator.store import Store, get_store

MODEL = "claude-sonnet-5-5"   # keep in step with agent.md
# Own lockfile (the root one belongs to the Scout's workspace):
#   python -m orchestrator.ant apply --lock-file agents/builder/claude-lock.json \
#       agents/builder/agent.md agents/builder/environment.yaml
LOCK_FILE = "agents/builder/claude-lock.json"


def ensure_candidate(store: Store, data: DataClient, uarn: str) -> None:
    """Test only: the Scout writes candidates in production."""
    if store.get_candidate(uarn):
        store.set_candidate_status(uarn, "building_pack")
        return
    v = data.get_valuation(uarn)
    tone = data.street_tone(v["street_key"])
    store.upsert_candidate({
        "uarn": uarn, "address": v["address"], "postcode": v["postcode"],
        "street_key": v["street_key"], "rv": v["rv"], "zone_a_price": v["zone_a_price"],
        "street_median": tone["median_zone_a"],
        "z_score": round((v["zone_a_price"] - tone["median_zone_a"]) / tone["spread_mad"], 2),
        "flags": ["zone_a_above_street"], "status": "building_pack",
    })


def run(uarn: str) -> None:
    load_env()
    client = anthropic_client()   # key and workspace from the environment
    store, data = get_store(), DataClient()
    handlers = BuilderHandlers(store, data)
    ensure_candidate(store, data, uarn)

    result = run_session(
        client, store, agent=AGENT,
        agent_id=resource_id("BUILDER_AGENT_ID", "./agent.md", LOCK_FILE),
        environment_id=resource_id("BUILDER_ENV_ID", "./environment.yaml", LOCK_FILE),
        title=f"builder test {uarn}", model=MODEL,
        message=f"Candidate UARN: {uarn}", handle=handlers.handle,
        meter=budget.SpendMeter(model=MODEL), label=uarn,
    )
    cand = store.get_candidate(uarn)
    print(f"\nStopped: {result.stop_reason}. Tokens: {result.meter.tokens:,}. "
          f"Candidate status: {cand['status'] if cand else '?'}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "1001008")
