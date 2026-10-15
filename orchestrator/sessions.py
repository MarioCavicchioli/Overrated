"""Run one Managed Agents session to the end, answering its custom tool calls.

One loop for every session the orchestrator runs (builder test sessions, the
advocate, the Scout):

- Every session gets a hard dollar cap (budget.platform_budget) plus a client
  meter with a token budget, a turn cap and an optional dollar stop; past any
  of them, the session is interrupted and a budget_stop event is written.
- Custom tool calls, including those from a workflow run's threads (which
  arrive on the primary stream), go to handle(name, input). A failing handler
  gets an is_error result, so the session is never left waiting on a tool.
- Tool calls that pause for approval are denied: nobody watches these
  sessions, and no agent may file, send or submit anything.
- An end_turn idle with a workflow run still open isn't the end: the agent
  takes a turn on each run's ending notice.
- When the session stops, its spend is written to events from session.usage,
  which covers every thread, workflow runs included, with cost_usd.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

import anthropic

from orchestrator import budget
from orchestrator.config import ROOT, trace_url
from orchestrator.store import Store

log = logging.getLogger(__name__)

ToolHandler = Callable[[str, dict[str, Any]], str]


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
    tokens: int = 0               # all threads, from session.usage (meter's count if unavailable)
    list_cost_cents: int = 0


def usage_tokens(usage: Any) -> int:
    """Total tokens in a session.usage object: input, output, cache reads and writes."""
    total = sum(getattr(usage, k, 0) or 0 for k in
                ("input_tokens", "output_tokens", "cache_read_input_tokens"))
    cache_creation = getattr(usage, "cache_creation", None)
    if cache_creation is not None:
        total += sum(getattr(cache_creation, k, 0) or 0 for k in
                     ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"))
    return total


def run_session(client, store: Store, *, agent: str, agent_id: str, environment_id: str,
                title: str, model: str, message: str, handle: ToolHandler | None,
                meter: budget.SpendMeter | None = None, max_usd: float | None = None,
                case_id: str | None = None, run_id: str | None = None, label: str = "",
                verbose: bool = True) -> SessionResult:
    """Start a session, send one user message, and answer custom tool calls with
    handle(name, input) until the agent is done or a budget stops it.

    agent is the events.agent name (builder, advocate, scout, ...)."""
    say = print if verbose else (lambda *a, **k: None)
    meter = meter or budget.SpendMeter(model=model)
    label = label or title
    session = client.beta.sessions.create(
        agent=agent_id, environment_id=environment_id, title=title,
        budget=budget.platform_budget(model, max_usd=max_usd),
    )
    store.add_event(agent, "session_started", f"{title}: {trace_url(session.id)}",
                    case_id=case_id, run_id=run_id, payload={"session_id": session.id})
    say(f"Session {session.id}\nTrace: {trace_url(session.id)}\n")

    open_runs: set[str] = set()
    stop_reason = "unknown"
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        _send(client, session.id, {"type": "user.message",
                                   "content": [{"type": "text", "text": message}]})
        for event in stream:
            kind = event.type
            if kind == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        say(block.text, flush=True)
            elif kind == "agent.custom_tool_use":
                say(f"  -> {event.name} {json.dumps(event.input)[:160]}")
                text = _answer_tool(client, store, session.id, event, handle, agent, case_id)
                say(f"  <- {text.splitlines()[0][:160] if text else ''}")
            elif kind in ("agent.tool_use", "agent.mcp_tool_use") \
                    and getattr(event, "evaluated_permission", None) == "ask":
                store.add_event(agent, "tool_denied",
                                f"denied {getattr(event, 'name', kind)}: unattended session",
                                case_id=case_id, run_id=run_id)
                _send(client, session.id, {"type": "user.tool_confirmation",
                                           "tool_use_id": event.id, "result": "deny"})
            elif kind == "workflow_run.created":
                open_runs.add(event.workflow_run_id)
            elif kind == "workflow_run.status_ended":
                open_runs.discard(event.workflow_run_id)
            elif kind == "span.model_request_end":
                meter.add(event.model_usage)
                if reason := meter.exceeded:
                    store.add_event(agent, "budget_stop", f"{label}: {reason}",
                                    case_id=case_id, run_id=run_id, tokens=meter.tokens)
                    _send(client, session.id, {"type": "user.interrupt"})
                    stop_reason = reason
                    break
            elif kind == "session.status_idle":
                reason = event.stop_reason.type
                if reason == "requires_action":
                    continue
                if reason == "end_turn" and open_runs:
                    continue   # the agent takes a turn on each run's ending notice
                stop_reason = reason
                break
            elif kind == "session.status_terminated":
                stop_reason = "terminated"
                break

    tokens, cents = meter.tokens, None
    try:   # session.usage covers every thread; the meter only sees the primary stream
        usage = client.beta.sessions.retrieve(session.id).usage
        tokens = usage_tokens(usage) or tokens
        cents = int(usage.list_cost.amount) if usage.list_cost else None
    except Exception:
        log.warning("could not read usage for %s; logging the meter's count", session.id)
    cost_usd = cents / 100 if cents is not None else round(meter.cost_usd, 4) or None
    cost_text = f", {cents} cents list cost" if cents is not None else (
        f", ~${meter.cost_usd:.2f}" if meter.model else "")
    store.add_event(agent, "session_spend",
                    f"{label}: {tokens:,} tokens over {meter.turns} model requests"
                    f"{cost_text} ({stop_reason})",
                    case_id=case_id, run_id=run_id, tokens=tokens, cost_usd=cost_usd,
                    idempotency_key=f"spend:{session.id}",
                    payload={"session_id": session.id, "stop_reason": stop_reason,
                             "model": model})
    return SessionResult(session.id, stop_reason, meter, tokens, cents or 0)


def _answer_tool(client, store: Store, session_id: str, event, handle: ToolHandler | None,
                 agent: str, case_id: str | None) -> str:
    try:
        if handle is None:
            raise LookupError("this session has no custom tool handler")
        text, is_error = handle(event.name, dict(event.input)), False
    except Exception as e:   # never leave the session waiting on a tool
        log.exception("custom tool %s failed", event.name)
        store.add_event(agent, "tool_error", f"{event.name}: {type(e).__name__}",
                        case_id=case_id)
        text, is_error = f"ERROR: {event.name} failed ({type(e).__name__}); try again later", True
    try:
        _send(client, session_id, {"type": "user.custom_tool_result",
                                   "custom_tool_use_id": event.id,
                                   "content": [{"type": "text", "text": text}],
                                   **({"is_error": True} if is_error else {})})
    except anthropic.BadRequestError as e:
        # A workflow run can archive the calling thread before the answer arrives.
        log.warning("answer to %s refused: %s", event.name, e.message)
    return text


def _send(client, session_id: str, event: dict) -> None:
    client.beta.sessions.events.send(session_id=session_id, events=[event])
