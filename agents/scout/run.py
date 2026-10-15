"""Start a Scout session on a target borough and follow it until it finishes.

Run: .venv/bin/python agents/scout/run.py [--ba-code 5360 --name Hackney]
Needs `ant apply` done (IDs in claude-lock.json, or SCOUT_AGENT_ID / SCOUT_ENV_ID /
VAULT_ID in .env) and the credentials added with add_credentials.py.
"""
import argparse
import json
import os
from datetime import datetime, timezone

import anthropic
import httpx

SNAPSHOT = "voa-2026-compiled-epoch-0004"  # matches the data service
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOCK_KEYS = {
    "SCOUT_AGENT_ID": "./agents/scout/agent.md",
    "SCOUT_ENV_ID": "./agents/scout/environment.yaml",
    "VAULT_ID": "./agents/scout/vault.yaml",
}


def load_env():
    for line in open(os.path.join(ROOT, ".env")):
        k, _, v = line.strip().partition("=")
        if k and not k.startswith("#") and v:
            os.environ.setdefault(k, v)


def resource_ids():
    lock = {}
    path = os.path.join(ROOT, "claude-lock.json")
    if os.path.exists(path):
        lock = json.load(open(path)).get("resources", {})
    ids = {}
    for var, key in LOCK_KEYS.items():
        ids[var] = os.environ.get(var) or lock.get(key, {}).get("id")
        if not ids[var]:
            raise SystemExit(f"{var} not set and {key} not in claude-lock.json; run ant apply first")
    return ids


def budget_cents():
    """SESSION_TOKEN_BUDGET priced at Opus 5.5's output rate ($20/M), the worst case."""
    if os.environ.get("SESSION_BUDGET_CENTS"):
        return int(os.environ["SESSION_BUDGET_CENTS"])
    tokens = int(os.environ.get("SESSION_TOKEN_BUDGET", "200000"))
    return max(tokens * 20 * 100 // 1_000_000, 50)


def supabase():
    """(base_url, headers) for PostgREST, or None when Supabase isn't configured yet."""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        return None
    return url.rstrip("/"), {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def start_run(ba_code, name):
    """Resume this borough's unfinished scout_runs row, or open a new one. Returns run_id or None."""
    sb = supabase()
    if not sb:
        return None
    base, headers = sb
    found = httpx.get(f"{base}/rest/v1/scout_runs", headers=headers, timeout=10, params={
        "area_code": f"eq.ba{ba_code}", "status": "in.(queued,running,paused)",
        "select": "run_id", "order": "created_at.desc", "limit": "1",
    })
    found.raise_for_status()
    if found.json():
        httpx.patch(f"{base}/rest/v1/scout_runs", headers=headers, timeout=10,
                    params={"run_id": f"eq.{found.json()[0]['run_id']}"}, json={"status": "running"})
        return found.json()[0]["run_id"]
    made = httpx.post(f"{base}/rest/v1/scout_runs", timeout=10,
                      headers={**headers, "Prefer": "return=representation"},
                      json={"area_name": name, "area_code": f"ba{ba_code}", "rating_list": "2026",
                            "source_snapshot": SNAPSHOT, "status": "running",
                            "started_at": datetime.now(timezone.utc).isoformat()})
    made.raise_for_status()
    return made.json()[0]["run_id"]


def log_event(kind, message, run_id=None, tokens=None, cost_usd=None, payload=None):
    """Orchestrator-side events row (session start, spend), when Supabase is configured."""
    sb = supabase()
    if not sb:
        return
    base, headers = sb
    httpx.post(f"{base}/rest/v1/events", timeout=10, headers=headers, json={
        "agent": "scout", "kind": kind, "message": message, "run_id": run_id,
        "tokens": tokens, "cost_usd": cost_usd, "payload": payload or {},
    })


def kickoff(ba_code, name, run_id):
    lines = [
        f"Target: the London Borough of {name}, billing authority code {ba_code}.",
        f"Data service base URL: {os.environ['DATA_SERVICE_URL']}",
    ]
    if run_id:
        lines.append(f"Supabase base URL: {os.environ['SUPABASE_URL']}")
        lines.append(f"run_id: {run_id}")
    else:
        lines.append("Supabase is not set up yet: write rows to /mnt/session/outputs/ instead.")
    lines.append("Run the sweep as your instructions describe, then report.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ba-code", default="5360")
    ap.add_argument("--name", default="Hackney")
    args = ap.parse_args()

    load_env()
    ids = resource_ids()
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")  # needed when the API key isn't scoped to one workspace
    client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws} if ws else None)
    cents = budget_cents()
    run_id = start_run(args.ba_code, args.name)

    session = client.beta.sessions.create(
        agent=ids["SCOUT_AGENT_ID"],
        environment_id=ids["SCOUT_ENV_ID"],
        vault_ids=[ids["VAULT_ID"]],
        title=f"Scout: {args.name}",
        budget={"type": "limit", "max_list_cost": {"amount": str(cents), "currency": "USD"}},
        metadata={"agent": "scout", "target": f"ba{args.ba_code}", "run_id": run_id or ""},
    )
    print(f"session {session.id} (budget ${cents / 100:.2f}, run_id {run_id or 'none: no Supabase'})")
    print(f"watch: https://platform.claude.com/workspaces/default/sessions/{session.id}")
    log_event("session_started", f"Scout session on {args.name}, budget ${cents / 100:.2f}", run_id,
              payload={"session_id": session.id})

    tokens = 0
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(session_id=session.id, events=[
            {"type": "user.message", "content": [{"type": "text", "text": kickoff(args.ba_code, args.name, run_id)}]},
        ])
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        print(block.text, flush=True)
            elif event.type == "agent.tool_use":
                print(f"  [{event.name}]", flush=True)
            elif event.type in ("session.thread_created", "session.thread_status_terminated"):
                print(f"  {event.type}: {event.agent_name}", flush=True)
            elif event.type == "span.model_request_end" and event.model_usage:
                u = event.model_usage
                tokens += (u.input_tokens or 0) + (u.output_tokens or 0) + (u.cache_creation_input_tokens or 0)
            elif event.type == "session.error":
                print(f"  session error: {event}", flush=True)

            if (event.type in ("agent.tool_use", "agent.mcp_tool_use")
                    and getattr(event, "evaluated_permission", None) == "ask"):
                # Unattended run: nothing the Scout needs should be ambiguous, so deny and say why.
                client.beta.sessions.events.send(session_id=session.id, events=[{
                    "type": "user.tool_confirmation", "tool_use_id": event.id, "result": "deny",
                    "deny_message": "Unattended run: this call needs a human. Find another way or report it.",
                }])
            elif event.type == "session.status_terminated":
                break
            elif event.type == "session.status_idle" and event.stop_reason.type != "requires_action":
                print(f"idle: {event.stop_reason.type}")
                break

    usage = client.beta.sessions.retrieve(session.id).usage
    cost = int(usage.list_cost.amount) / 100 if usage and usage.list_cost else None
    print(f"tokens {tokens:,}; list cost ${cost}")
    log_event("session_finished", f"Scout session finished: {tokens:,} tokens, ${cost}", run_id,
              tokens=tokens, cost_usd=cost, payload={"session_id": session.id})


if __name__ == "__main__":
    main()
