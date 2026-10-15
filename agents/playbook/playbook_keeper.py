from __future__ import annotations

import hashlib
import json
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any

AGENT_ID = os.environ.get("PLAYBOOK_KEEPER_AGENT_ID", "")
ENVIRONMENT_ID = os.environ.get("MANAGED_AGENT_ENVIRONMENT_ID", "")
MAX_LIST_COST_MINOR_UNITS = os.environ.get("PLAYBOOK_KEEPER_MAX_LIST_COST_MINOR_UNITS", "300")


def _clients() -> tuple[Any, Any]:
    missing = [name for name in ("ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
               if not os.environ.get(name)]
    if not AGENT_ID:
        missing.append("PLAYBOOK_KEEPER_AGENT_ID")
    if not ENVIRONMENT_ID:
        missing.append("MANAGED_AGENT_ENVIRONMENT_ID")
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    from anthropic import Anthropic
    from supabase import create_client
    return (
        Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]),
        create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]),
    )


def validate_proposal(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("Playbook Keeper output must be a JSON object")
    rules, diff, rationale = value.get("rules"), value.get("diff"), value.get("rationale")
    if not isinstance(rules, dict) or not rules:
        raise RuntimeError("Proposal rules must be a non-empty JSON object")
    if not isinstance(diff, dict):
        raise RuntimeError("Proposal diff must be a JSON object")
    if not isinstance(rationale, str) or not rationale.strip():
        raise RuntimeError("Proposal rationale must be non-empty text")
    return {"rules": rules, "diff": diff, "rationale": rationale}


def _session_cost(session: Any) -> tuple[int | None, str | None]:
    usage = getattr(session, "usage", None)
    if usage is None:
        return None, None
    inputs, outputs = getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)
    tokens = inputs + outputs if isinstance(inputs, int) and isinstance(outputs, int) else None
    list_cost = getattr(usage, "list_cost", None)
    amount = getattr(list_cost, "amount", None) if list_cost else None
    if amount is None:
        return tokens, None
    try:
        cost = format(Decimal(str(amount)) / Decimal(100), ".6f")
    except InvalidOperation:
        cost = None
    return tokens, cost


def _model_proposal(anthropic: Any, context: dict[str, Any]) -> tuple[dict[str, Any], int | None, str | None]:
    prompt = """You are Overrated's Playbook Keeper. Produce a small, evidence-led candidate update to the supplied live playbook.
Use only the aggregate held-out evaluation metrics supplied. Do not invent results, causal claims, property facts, or legal rules. Keep the existing rules shape and unrelated keys. If evidence does not support a useful change, return an empty diff and explain that no candidate should be created.

Return exactly one JSON object:
rules: a complete proposed rules object
diff: an object describing only the proposed changes
rationale: concise explanation tied to the supplied evaluation evidence

This output is a candidate only. Never promote it to live, reject another version, or change any existing playbook row."""
    session = anthropic.beta.sessions.create(
        agent=AGENT_ID,
        environment_id=ENVIRONMENT_ID,
        budget={"type": "limit", "max_list_cost": {"amount": MAX_LIST_COST_MINOR_UNITS, "currency": "USD"}},
        initial_events=[{
            "type": "user.message",
            "content": [{"type": "text", "text": prompt + "\n\nInput JSON:\n" + json.dumps(context, ensure_ascii=False, default=str)}],
        }],
        title=f"Overrated Playbook Keeper {context['base_version']}",
    )
    parts: list[str] = []
    with anthropic.beta.sessions.events.stream(session.id) as stream:
        for event in stream:
            if event.type == "agent.message":
                for block in getattr(event, "content", []) or []:
                    if getattr(block, "type", None) == "text":
                        parts.append(block.text)
            elif event.type in {"session.error", "session.retries_exhausted"}:
                detail = getattr(getattr(event, "error", None), "message", event.type)
                raise RuntimeError(f"Managed Agent session failed: {detail}")
            elif event.type == "session.status_idle":
                stop_reason = getattr(getattr(event, "stop_reason", None), "type", None)
                if stop_reason not in (None, "end_turn"):
                    raise RuntimeError(f"Managed Agent stopped before completing: {stop_reason}")
                break
    fence = chr(96) * 3
    raw = re.sub(r"^\s*" + re.escape(fence) + r"(?:json)?\s*|\s*" + re.escape(fence) + r"\s*$", "", "\n".join(parts).strip())
    try:
        proposal = validate_proposal(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Playbook Keeper did not return valid JSON") from exc
    completed = anthropic.beta.sessions.retrieve(session.id)
    tokens, cost = _session_cost(completed)
    return proposal, tokens, cost


def _proposal_identity(base_version: str, eval_ids: list[str]) -> tuple[str, str]:
    seed = base_version + ":" + ",".join(sorted(eval_ids))
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    safe_base = re.sub(r"[^A-Za-z0-9_-]+", "_", base_version)[:40]
    version = f"pb_{safe_base}_{digest}"
    return version, f"playbook-candidate:{version}"


def propose_candidate() -> dict[str, Any]:
    """Create a candidate from the current live rules and completed held-out evals."""
    anthropic, db = _clients()
    live_result = (
        db.table("playbook").select("*").eq("status", "live")
        .order("created_at", desc=True).limit(1).execute()
    )
    if not live_result.data:
        raise RuntimeError("No live playbook exists; seed and approve a baseline before proposing updates")
    live = live_result.data[0]
    eval_result = (
        db.table("eval_runs").select("*").eq("status", "completed").eq("split", "heldout")
        .order("created_at", desc=True).limit(10).execute()
    )
    evaluations = eval_result.data or []
    if not evaluations:
        raise RuntimeError("No completed heldout eval runs are available")
    eval_ids = [str(row["eval_run_id"]) for row in evaluations if row.get("eval_run_id")]
    if not eval_ids:
        raise RuntimeError("Heldout evaluations are missing eval_run_id values")
    version, idempotency_key = _proposal_identity(live["version"], eval_ids)
    previous_event = db.table("events").select("*").eq("idempotency_key", idempotency_key).limit(1).execute()
    if previous_event.data:
        return {"status": "already_proposed", "event": previous_event.data[0]}

    # Pass only aggregate evaluation data; do not send property identifiers or VOA records.
    compact_evals = [{
        "eval_run_id": row["eval_run_id"], "version": row["version"],
        "dataset_snapshot": row["dataset_snapshot"], "split": row["split"],
        "lift_at_50": row.get("lift_at_50"), "pack_pass_rate": row.get("pack_pass_rate"),
        "advocate_accuracy": row.get("advocate_accuracy"), "metrics": row.get("metrics") or {},
    } for row in evaluations]
    context = {"base_version": live["version"], "live_rules": live["rules"], "heldout_evaluations": compact_evals}
    proposal, tokens, cost = _model_proposal(anthropic, context)
    if not proposal["diff"]:
        return {"status": "no_candidate", "rationale": proposal["rationale"]}

    current = (
        db.table("playbook").select("version").eq("status", "live")
        .order("created_at", desc=True).limit(1).execute()
    )
    if not current.data or current.data[0]["version"] != live["version"]:
        raise RuntimeError("Live playbook changed during generation; discard and rerun")

    existing = db.table("playbook").select("playbook_id").eq("version", version).limit(1).execute()
    if existing.data:
        candidate_id = existing.data[0]["playbook_id"]
    else:
        inserted = db.table("playbook").insert({
            "version": version, "rules": proposal["rules"], "status": "candidate",
            "based_on_version": live["version"], "diff": proposal["diff"],
            "eval_run_id": None, "created_by": "playbook_keeper",
        }).execute()
        if not inserted.data:
            raise RuntimeError("Candidate playbook was not persisted")
        candidate_id = inserted.data[0]["playbook_id"]

    event = db.table("events").insert({
        "agent": "playbook", "kind": "playbook_candidate_proposed",
        "message": proposal["rationale"], "tokens": tokens, "cost_usd": cost,
        "payload": {
            "candidate_id": candidate_id, "version": version, "based_on_version": live["version"],
            "evaluation_ids": eval_ids, "diff": proposal["diff"], "status": "candidate",
        },
        "idempotency_key": idempotency_key,
    }).execute()
    return {"status": "candidate_created", "candidate_id": candidate_id, "version": version,
            "event": event.data[0] if event.data else None}


if __name__ == "__main__":
    print(json.dumps(propose_candidate(), ensure_ascii=False, default=str, indent=2))


