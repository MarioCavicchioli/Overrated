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

import anthropic
from dotenv import load_dotenv

from agents.builder.handlers import AGENT, BuilderHandlers
from orchestrator import budget
from orchestrator.data_client import DataClient
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
    load_dotenv(ROOT / ".env")
    client = anthropic.Anthropic()
    store, data = get_store(), DataClient()
    handlers = BuilderHandlers(store, data)
    ensure_candidate(store, data, uarn)

    session = client.beta.sessions.create(
        agent=resource_id("BUILDER_AGENT_ID", "./agents/builder/agent.md"),
        environment_id=resource_id("BUILDER_ENV_ID", "./agents/builder/environment.yaml"),
        title=f"builder test {uarn}",
        budget=budget.platform_budget(MODEL),
    )
    store.add_event(AGENT, "session_started", f"test session for {uarn}")
    print(f"Session {session.id}\n"
          f"Trace: https://platform.claude.com/workspaces/default/sessions/{session.id}\n")

    meter = budget.SpendMeter()
    stop_reason = "unknown"
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(session_id=session.id, events=[{
            "type": "user.message",
            "content": [{"type": "text", "text": f"Candidate UARN: {uarn}"}],
        }])
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        print(block.text, flush=True)
            elif event.type == "agent.custom_tool_use":
                print(f"  -> {event.name} {json.dumps(event.input)[:160]}")
                result = handlers.handle(event.name, event.input)
                print(f"  <- {result.splitlines()[0][:160]}")
                client.beta.sessions.events.send(session_id=session.id, events=[{
                    "type": "user.custom_tool_result",
                    "custom_tool_use_id": event.id,
                    "content": [{"type": "text", "text": result}],
                }])
            elif event.type == "span.model_request_end":
                meter.add(event.model_usage)
                if reason := meter.exceeded:
                    store.add_event(AGENT, "budget_stop", f"{uarn}: {reason}", tokens=meter.tokens)
                    client.beta.sessions.events.send(
                        session_id=session.id, events=[{"type": "user.interrupt"}])
                    stop_reason = reason
                    break
            elif event.type == "session.status_idle":
                if event.stop_reason.type != "requires_action":
                    stop_reason = event.stop_reason.type
                    break
            elif event.type == "session.status_terminated":
                stop_reason = "terminated"
                break

    store.add_event(AGENT, "session_spend",
                    f"{uarn}: {meter.tokens:,} tokens over {meter.turns} model requests "
                    f"({stop_reason})", tokens=meter.tokens)
    cand = store.get_candidate(uarn)
    print(f"\nStopped: {stop_reason}. Tokens: {meter.tokens:,}. "
          f"Candidate status: {cand['status'] if cand else '?'}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "1001008")
