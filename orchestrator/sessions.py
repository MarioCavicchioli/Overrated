"""Run one Managed Agents session to the end, answering its custom tool calls.

Shared by every agent whose tools are all custom tools answered here (builder
test sessions, the advocate). Writes session_started, budget_stop and
session_spend events.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable

from orchestrator import budget
from orchestrator.config import ROOT, trace_url
from orchestrator.store import Store


def resource_id(env_var: str, path: str, lock_file: str = "claude-lock.json") -> str:
    """An agent or environment ID: from env_var, else from the lockfile
    (lock_file relative to the repo root; path relative to the lockfile)."""
    if os.getenv(env_var):
        return os.environ[env_var]
    lock = json.loads((ROOT / lock_file).read_text())
    return lock["resources"][path]["id"]


@dataclass
class SessionResult:
    session_id: str
    stop_reason: str
    meter: budget.SpendMeter


def run_session(client, store: Store, *, agent: str, agent_id: str, environment_id: str,
                title: str, model: str, message: str, handle: Callable[[str, dict], str],
                meter: budget.SpendMeter, max_usd: float | None = None,
                case_id: str | None = None, label: str = "", verbose: bool = True,
                ) -> SessionResult:
    """Start a session, send one user message, answer custom tool calls with
    handle(name, input) until the agent goes idle or a budget stops it."""
    say = print if verbose else (lambda *a, **k: None)
    session = client.beta.sessions.create(
        agent=agent_id, environment_id=environment_id, title=title,
        budget=budget.platform_budget(model, max_usd=max_usd),
    )
    store.add_event(agent, "session_started", title, case_id=case_id)
    say(f"Session {session.id}\nTrace: {trace_url(session.id)}\n")

    stop_reason = "unknown"
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(session_id=session.id, events=[{
            "type": "user.message", "content": [{"type": "text", "text": message}],
        }])
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        say(block.text, flush=True)
            elif event.type == "agent.custom_tool_use":
                say(f"  -> {event.name} {json.dumps(event.input)[:160]}")
                try:
                    result = handle(event.name, event.input)
                except Exception as e:   # never leave the session waiting on a tool
                    store.add_event(agent, "tool_error", f"{event.name}: {type(e).__name__}",
                                    case_id=case_id)
                    result = f"ERROR: {event.name} failed ({type(e).__name__}); try again later"
                say(f"  <- {result.splitlines()[0][:160]}")
                client.beta.sessions.events.send(session_id=session.id, events=[{
                    "type": "user.custom_tool_result",
                    "custom_tool_use_id": event.id,
                    "content": [{"type": "text", "text": result}],
                }])
            elif event.type == "span.model_request_end":
                meter.add(event.model_usage)
                if reason := meter.exceeded:
                    store.add_event(agent, "budget_stop", f"{label}: {reason}",
                                    case_id=case_id, tokens=meter.tokens)
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

    cost = f", ~${meter.cost_usd:.2f}" if meter.model else ""
    store.add_event(agent, "session_spend",
                    f"{label}: {meter.tokens:,} tokens over {meter.turns} model requests"
                    f"{cost} ({stop_reason})", case_id=case_id, tokens=meter.tokens)
    return SessionResult(session.id, stop_reason, meter)
