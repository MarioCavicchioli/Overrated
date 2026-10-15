"""One loop for every Managed Agents session the orchestrator runs.

- Every session gets a hard dollar cap (budget.platform_budget) plus a client-side
  token budget and turn cap; past either, the session is interrupted.
- Custom tool calls (including those from a workflow run's threads, which arrive
  on the primary stream) go to `tool_handler(name, input) -> str`.
- Tool calls that pause for approval are denied: nobody watches these sessions,
  and no agent may file, send or submit anything.
- When the session stops, its spend is written to events from session.usage,
  which covers every thread, workflow runs included.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

import anthropic

from orchestrator import budget
from orchestrator.config import trace_url
from orchestrator.store import Store

log = logging.getLogger(__name__)

ToolHandler = Callable[[str, dict[str, Any]], str]


@dataclass
class SessionResult:
    session_id: str
    stop_reason: str
    tokens: int            # all threads, from session.usage
    list_cost_cents: int


def usage_tokens(usage: Any) -> int:
    """Total tokens in a session.usage object: input, output, cache reads and writes."""
    total = sum(getattr(usage, k, 0) or 0 for k in
                ("input_tokens", "output_tokens", "cache_read_input_tokens"))
    cache_creation = getattr(usage, "cache_creation", None)
    if cache_creation is not None:
        total += sum(getattr(cache_creation, k, 0) or 0 for k in
                     ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"))
    return total


def run_session(
    client: anthropic.Anthropic,
    store: Store,
    *,
    agent: str,
    environment_id: str,
    model: str,
    agent_name: str,           # events.agent: scout | builder | advocate | ...
    kickoff: str,
    tool_handler: ToolHandler | None = None,
    case_id: str | None = None,
    title: str | None = None,
    on_text: Callable[[str], None] | None = None,
) -> SessionResult:
    session = client.beta.sessions.create(
        agent=agent, environment_id=environment_id, title=title,
        budget=budget.platform_budget(model),
    )
    store.add_event(agent_name, "session_started", f"{title or agent_name}: {trace_url(session.id)}",
                    case_id=case_id)

    meter = budget.SpendMeter()
    open_runs: set[str] = set()
    stop_reason = "unknown"
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(session_id=session.id, events=[{
            "type": "user.message", "content": [{"type": "text", "text": kickoff}]}])
        for event in stream:
            kind = event.type
            if kind == "agent.message" and on_text:
                for block in event.content:
                    if block.type == "text":
                        on_text(block.text)
            elif kind == "agent.custom_tool_use":
                _answer_tool(client, session.id, event, tool_handler)
            elif kind in ("agent.tool_use", "agent.mcp_tool_use") \
                    and getattr(event, "evaluated_permission", None) == "ask":
                store.add_event(agent_name, "tool_denied",
                                f"denied {getattr(event, 'name', kind)}: unattended session",
                                case_id=case_id)
                _send(client, session.id, {"type": "user.tool_confirmation",
                                           "tool_use_id": event.id, "result": "deny"})
            elif kind == "workflow_run.created":
                open_runs.add(event.workflow_run_id)
            elif kind == "workflow_run.status_ended":
                open_runs.discard(event.workflow_run_id)
            elif kind == "span.model_request_end":
                meter.add(event.model_usage)
                if reason := meter.exceeded:
                    store.add_event(agent_name, "budget_stop", reason, case_id=case_id,
                                    tokens=meter.tokens)
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

    final = client.beta.sessions.retrieve(session.id)
    tokens = usage_tokens(final.usage)
    cents = int(final.usage.list_cost.amount) if final.usage.list_cost else 0
    store.add_event(agent_name, "session_spend",
                    f"{title or agent_name}: {tokens:,} tokens, {cents} cents list cost "
                    f"({stop_reason})", case_id=case_id, tokens=tokens)
    return SessionResult(session.id, stop_reason, tokens, cents)


def _answer_tool(client, session_id: str, event, handler: ToolHandler | None) -> None:
    try:
        if handler is None:
            raise LookupError("this session has no custom tool handler")
        text, is_error = handler(event.name, dict(event.input)), False
    except Exception as e:   # the agent sees a short error; details go to the log
        log.exception("custom tool %s failed", event.name)
        text, is_error = f"ERROR: {type(e).__name__}: tool failed, try again or stop", True
    try:
        _send(client, session_id, {"type": "user.custom_tool_result",
                                   "custom_tool_use_id": event.id,
                                   "content": [{"type": "text", "text": text}],
                                   **({"is_error": True} if is_error else {})})
    except anthropic.BadRequestError as e:
        # A workflow run can archive the calling thread before the answer arrives.
        log.warning("answer to %s refused: %s", event.name, e.message)


def _send(client, session_id: str, event: dict) -> None:
    client.beta.sessions.events.send(session_id=session_id, events=[event])
