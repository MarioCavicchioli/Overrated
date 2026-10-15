"""Add the Scout's secrets to the overrated vault (run once, after `ant apply`).

Run: .venv/bin/python agents/scout/add_credentials.py [--workspace overrated|legacy]
Defaults to Person 2's 'overrated' workspace when OVERRATED_API_KEY is in .env (vault id from
agents/scout/claude-lock.json), else the first deployment (root claude-lock.json, `ant` profile).
Reads DATA_SERVICE_KEY, DATA_SERVICE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_URL
from .env. Secrets go to Anthropic's vault and are substituted at egress, header only,
for their own host only; the sandbox never sees them.
"""
import argparse
import json
import os
from urllib.parse import urlparse

import anthropic

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_env():
    for line in open(os.path.join(ROOT, ".env")):
        k, _, v = line.strip().partition("=")
        if k and not k.startswith("#") and v:
            os.environ.setdefault(k, v)


def vault_id(workspace):
    if workspace == "overrated":
        lock = json.load(open(os.path.join(ROOT, "agents", "scout", "claude-lock.json")))
        return lock["resources"]["./vault.yaml"]["id"]
    if os.environ.get("VAULT_ID"):
        return os.environ["VAULT_ID"]
    lock = json.load(open(os.path.join(ROOT, "claude-lock.json")))
    return lock["resources"]["./agents/scout/vault.yaml"]["id"]


def client_for(workspace):
    if workspace == "overrated":
        return anthropic.Anthropic(api_key=os.environ["OVERRATED_API_KEY"],
                                   default_headers={"anthropic-workspace-id": os.environ["OVERRATED_WORKSPACE_ID"]})
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")  # needed when the API key isn't scoped to one workspace
    return anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws} if ws else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", choices=["overrated", "legacy"], default=None)
    args = ap.parse_args()
    load_env()
    workspace = args.workspace or ("overrated" if os.environ.get("OVERRATED_API_KEY") else "legacy")
    client = client_for(workspace)
    vid = vault_id(workspace)
    print(f"workspace {workspace}, vault {vid}")
    secrets = [
        ("DATA_SERVICE_KEY", "DATA_SERVICE_URL", "Overrated data service key"),
        ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_URL", "Supabase service role key"),
    ]
    for name, url_var, label in secrets:
        value, url = os.environ.get(name), os.environ.get(url_var)
        if not value or not url:
            print(f"skip {name}: set {name} and {url_var} in .env first")
            continue
        try:
            cred = client.beta.vaults.credentials.create(
                vid,
                display_name=label,
                auth={
                    "type": "environment_variable",
                    "secret_name": name,
                    "secret_value": value,
                    "networking": {"type": "limited", "allowed_hosts": [urlparse(url).hostname]},
                    "injection_location": {"header": True},
                },
            )
        except anthropic.ConflictError:
            print(f"skip {name}: already in the vault (archive it in the Console to replace it)")
            continue
        print(f"added {name} -> {urlparse(url).hostname} ({cred.id})")


if __name__ == "__main__":
    main()
