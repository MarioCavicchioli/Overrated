"""Run the Case builder on one candidate in a standalone test session.

In production the Scout starts builders inside its workflow runs. This script
is for testing the builder before the Scout exists: it runs the builder as the
session's own agent and answers its tool calls the same way the orchestrator will.

    python -m agents.builder.run_local 1001008

Needs: DATA_SERVICE_URL, and the agent and environment created with
`ant apply agents/builder/agent.md agents/builder/environment.yaml`
(IDs read from claude-lock.json, or BUILDER_AGENT_ID / BUILDER_ENV_ID).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from agents.builder.handlers import AGENT, BuilderHandlers
from orchestrator.config import anthropic_client, load_env, trace_url
from orchestrator.data_client import DataClient
from orchestrator.sessions import run_session
from orchestrator.store import Store, get_store

ROOT = Path(__file__).resolve().parents[2]
MODEL = "claude-sonnet-5-5"   # keep in step with agent.md


def resource_id(env_var: str, path: str) -> str:
    if os.getenv(env_var):
        return os.environ[env_var]
    lock = json.loads((ROOT / "claude-lock.json").read_text())
    return lock["resources"][path]["id"]


def ensure_candidate(store: Store, data: DataClient, uarn: str) -> None:
    """Test only: the Scout writes candidates in production."""
    if store.get_candidate(uarn):
        store.set_candidate_status(uarn, "building")
        return
    v = data.get_valuation(uarn)
    tone = data.street_tone(v["street_key"])
    store.upsert_candidate({
        "uarn": uarn, "address": v["address"], "postcode": v["postcode"],
        "street_key": v["street_key"], "rv": v["rv"], "zone_a_price": v["zone_a_price"],
        "street_median": tone["median_zone_a"],
        "z_score": round((v["zone_a_price"] - tone["median_zone_a"]) / tone["spread_mad"], 2),
        "flags": ["zone_a_above_street"], "status": "building",
    })


def run(uarn: str) -> None:
    load_env()
    client = anthropic_client()   # key and workspace from the environment
    store, data = get_store(), DataClient()
    handlers = BuilderHandlers(store, data)
    ensure_candidate(store, data, uarn)

    def tool(name: str, args: dict) -> str:
        print(f"  -> {name} {json.dumps(args)[:160]}")
        result = handlers.handle(name, args)
        print(f"  <- {result.splitlines()[0][:160]}")
        return result

    result = run_session(
        client, store,
        agent=resource_id("BUILDER_AGENT_ID", "./agents/builder/agent.md"),
        environment_id=resource_id("BUILDER_ENV_ID", "./agents/builder/environment.yaml"),
        model=MODEL, agent_name=AGENT, kickoff=f"Candidate UARN: {uarn}",
        tool_handler=tool, title=f"builder test {uarn}", on_text=print,
    )
    cand = store.get_candidate(uarn)
    print(f"\nSession {result.session_id}: {trace_url(result.session_id)}\n"
          f"Stopped: {result.stop_reason}. Tokens: {result.tokens:,}, "
          f"{result.list_cost_cents} cents. Candidate status: {cand['status'] if cand else '?'}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "1001008")
