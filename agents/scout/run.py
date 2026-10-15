"""Start a Scout session on a target borough and follow it until it finishes.

Run: .venv/bin/python agents/scout/run.py [--ba-code 5360 --name Hackney] [--handoff-limit 2]

Where it runs:
- 'overrated' (default when OVERRATED_API_KEY is in .env): Person 2's workspace, next to their Case
  builder, with ids from agents/scout/claude-lock.json and the key + workspace id from .env.
- 'legacy': the first deployment in the 'Claude Code' workspace (root claude-lock.json, `ant` profile).

The Case builder runs as threads inside the Scout's session. Its tools are custom tools, so their
calls arrive on this stream and are answered here with Person 2's ScoutToolRouter (real data service
via DATA_BACKEND=http, Supabase via STORE_BACKEND=supabase), exactly as orchestrator/sessions.py does.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import anthropic
import httpx

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)  # for Person 2's orchestrator package

SNAPSHOT = "voa-2026-compiled-epoch-0004"  # matches the data service
LOCKS = {
    # workspace: (lockfile, {env var: resource key in that lockfile})
    "overrated": (os.path.join(ROOT, "agents", "scout", "claude-lock.json"),
                  {"SCOUT_AGENT_ID": "./agent.md", "SCOUT_ENV_ID": "./environment.yaml", "VAULT_ID": "./vault.yaml"}),
    "legacy": (os.path.join(ROOT, "claude-lock.json"),
               {"SCOUT_AGENT_ID": "./agents/scout/agent.md", "SCOUT_ENV_ID": "./agents/scout/environment.yaml",
                "VAULT_ID": "./agents/scout/vault.yaml"}),
}


def load_env():
    for line in open(os.path.join(ROOT, ".env")):
        k, _, v = line.strip().partition("=")
        if k and not k.startswith("#") and v:
            os.environ.setdefault(k, v)


def resource_ids(workspace):
    path, keys = LOCKS[workspace]
    lock = json.load(open(path)).get("resources", {}) if os.path.exists(path) else {}
    ids = {}
    for var, key in keys.items():
        ids[var] = lock.get(key, {}).get("id") or (os.environ.get(var) if workspace == "legacy" else None)
        if not ids[var]:
            raise SystemExit(f"{var}: {key} not in {os.path.relpath(path, ROOT)}; run ant apply first (see README)")
    return ids


def anthropic_client(workspace):
    if workspace == "overrated":
        key, ws = os.environ.get("OVERRATED_API_KEY"), os.environ.get("OVERRATED_WORKSPACE_ID")
        if not (key and ws):
            raise SystemExit("OVERRATED_API_KEY and OVERRATED_WORKSPACE_ID must be in .env")
        return anthropic.Anthropic(api_key=key, default_headers={"anthropic-workspace-id": ws})
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")  # needed when the API key isn't scoped to one workspace
    return anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws} if ws else None)


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


def kickoff(ba_code, name, run_id, handoff_limit):
    lines = [
        f"Target: the London Borough of {name}, billing authority code {ba_code}.",
        f"Data service base URL: {os.environ['DATA_SERVICE_URL']}",
    ]
    if run_id:
        lines.append(f"Supabase base URL: {os.environ['SUPABASE_URL']}")
        lines.append(f"run_id: {run_id}")
    else:
        lines.append("Supabase is not set up yet: write rows to /mnt/session/outputs/ instead.")
    if handoff_limit is not None:
        lines.append(f"Hand-off limit: give the Case builder at most {handoff_limit} candidates this run, "
                     "highest score first; leave the rest flagged.")
    lines.append("Run the sweep as your instructions describe, then report.")
    return "\n".join(lines)


def builder_router(run_id):
    """Person 2's router for the Case builder's custom tools (real data service, Supabase store).
    run_id ties the packs it writes to this Scout run."""
    os.environ.setdefault("DATA_BACKEND", "http")
    os.environ.setdefault("STORE_BACKEND", "supabase")
    from orchestrator.data_client import make_data_client
    from orchestrator.scout_tools import ScoutToolRouter
    from orchestrator.store import get_store
    store = get_store()
    return store, ScoutToolRouter(store, make_data_client(), run_id=run_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ba-code", default="5360")
    ap.add_argument("--name", default="Hackney")
    ap.add_argument("--handoff-limit", type=int, default=None,
                    help="hand at most this many candidates to the Case builder (for test runs)")
    ap.add_argument("--workspace", choices=["overrated", "legacy"], default=None,
                    help="default: overrated when OVERRATED_API_KEY is in .env, else legacy")
    args = ap.parse_args()

    load_env()
    workspace = args.workspace or ("overrated" if os.environ.get("OVERRATED_API_KEY") else "legacy")
    ids = resource_ids(workspace)
    client = anthropic_client(workspace)
    cents = budget_cents()
    run_id = start_run(args.ba_code, args.name)
    store, router = builder_router(run_id)
    # Person 2's own answer function, so the reply payload is identical to their orchestrator's.
    from orchestrator.sessions import _answer_tool
    session = client.beta.sessions.create(
        agent=ids["SCOUT_AGENT_ID"],
        environment_id=ids["SCOUT_ENV_ID"],
        vault_ids=[ids["VAULT_ID"]],
        title=f"Scout: {args.name}",
        budget={"type": "limit", "max_list_cost": {"amount": str(cents), "currency": "USD"}},
        metadata={"agent": "scout", "target": f"ba{args.ba_code}", "run_id": run_id or ""},
    )
    print(f"session {session.id} in {workspace} (budget ${cents / 100:.2f}, run_id {run_id or 'none: no Supabase'})")
    log_event("session_started", f"Scout session on {args.name}, budget ${cents / 100:.2f}", run_id,
              payload={"session_id": session.id, "workspace": workspace})

    tokens, open_runs, stop = 0, set(), "unknown"
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(session_id=session.id, events=[
            {"type": "user.message", "content": [{"type": "text", "text": kickoff(
                args.ba_code, args.name, run_id, args.handoff_limit)}]},
        ])
        for event in stream:
            kind = event.type
            if kind == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        print(block.text, flush=True)
            elif kind == "agent.tool_use":
                print(f"  [{event.name}]", flush=True)
            elif kind == "agent.custom_tool_use":
                # A Case builder thread calling one of its data tools, submit_pack or no_case.
                print(f"  builder -> {event.name} {json.dumps(event.input)[:120]}", flush=True)
                text = _answer_tool(client, store, session.id, event, router, "builder", None)
                print(f"  builder <- {text.splitlines()[0][:120] if text else ''}", flush=True)
            elif kind in ("session.thread_created", "session.thread_status_terminated"):
                print(f"  {kind}: {event.agent_name}", flush=True)
            elif kind == "workflow_run.created":
                open_runs.add(event.workflow_run_id)
            elif kind == "workflow_run.status_ended":
                open_runs.discard(event.workflow_run_id)
            elif kind == "span.model_request_end" and event.model_usage:
                u = event.model_usage
                tokens += (u.input_tokens or 0) + (u.output_tokens or 0) + (u.cache_creation_input_tokens or 0)
            elif kind == "session.error":
                print(f"  session error: {event}", flush=True)

            if kind in ("agent.tool_use", "agent.mcp_tool_use") and getattr(event, "evaluated_permission", None) == "ask":
                # Unattended run: nothing the Scout needs should be ambiguous, so deny and say why.
                client.beta.sessions.events.send(session_id=session.id, events=[{
                    "type": "user.tool_confirmation", "tool_use_id": event.id, "result": "deny",
                    "deny_message": "Unattended run: this call needs a human. Find another way or report it.",
                }])
            elif kind == "session.status_terminated":
                stop = "terminated"
                break
            elif kind == "session.status_idle":
                reason = event.stop_reason.type
                if reason == "requires_action":
                    continue
                if reason == "end_turn" and open_runs:
                    continue  # the Scout takes a turn on each run's ending notice
                stop = reason
                print(f"idle: {reason}")
                break

    usage = client.beta.sessions.retrieve(session.id).usage  # covers every thread, builders included
    cost = int(usage.list_cost.amount) / 100 if usage and usage.list_cost else None
    print(f"stopped: {stop}; tokens {tokens:,} on the Scout's own thread; session list cost ${cost}")
    log_event("session_finished", f"Scout session finished ({stop}): ${cost} incl. builder threads", run_id,
              tokens=tokens, cost_usd=cost, payload={"session_id": session.id, "stop_reason": stop})


if __name__ == "__main__":
    main()
