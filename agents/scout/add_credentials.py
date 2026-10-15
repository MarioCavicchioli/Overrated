"""Add the Scout's secrets to the overrated vault (run once, after `ant apply`).

Run: .venv/bin/python agents/scout/add_credentials.py
Reads DATA_SERVICE_KEY, DATA_SERVICE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_URL
from .env. Secrets go to Anthropic's vault and are substituted at egress, header only,
for their own host only; the sandbox never sees them.
"""
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


def vault_id():
    if os.environ.get("VAULT_ID"):
        return os.environ["VAULT_ID"]
    lock = json.load(open(os.path.join(ROOT, "claude-lock.json")))
    return lock["resources"]["./agents/scout/vault.yaml"]["id"]


def main():
    load_env()
    client = anthropic.Anthropic()
    vid = vault_id()
    secrets = [
        ("DATA_SERVICE_KEY", "DATA_SERVICE_URL", "Overrated data service key"),
        ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_URL", "Supabase service role key"),
    ]
    for name, url_var, label in secrets:
        value, url = os.environ.get(name), os.environ.get(url_var)
        if not value or not url:
            print(f"skip {name}: set {name} and {url_var} in .env first")
            continue
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
        print(f"added {name} -> {urlparse(url).hostname} ({cred.id})")


if __name__ == "__main__":
    main()
