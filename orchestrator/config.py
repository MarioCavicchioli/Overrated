"""Loads .env into the process environment. Call load_env() at every entry point.

Secrets stay in the environment: never print, log or pass them on a command line.
Values already set in the shell win over .env.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    load_dotenv(ROOT / ".env", override=False)


def anthropic_client():
    """SDK client for the same workspace ant uses.

    An organisation-level API key must name its workspace on every request; ant
    reads ANTHROPIC_WORKSPACE_ID for that, and so does this client.
    """
    import anthropic

    workspace = os.getenv("ANTHROPIC_WORKSPACE_ID")
    headers = {"anthropic-workspace-id": workspace} if workspace else None
    return anthropic.Anthropic(default_headers=headers)


def supabase_url() -> str | None:
    """The project URL supabase-py expects. Accepts the REST URL too
    (https://<ref>.supabase.co/rest/v1/), which is what the dashboard often shows."""
    url = os.getenv("SUPABASE_URL")
    if not url:
        return None
    url = url.rstrip("/")
    return url.removesuffix("/rest/v1")


def trace_url(session_id: str) -> str:
    """Console link to a session, in the workspace the client sends requests to."""
    workspace = os.getenv("ANTHROPIC_WORKSPACE_ID") or "default"
    return f"https://platform.claude.com/workspaces/{workspace}/sessions/{session_id}"


def env_int(name: str, default: int) -> int:
    """An integer setting; an empty value (as in .env.example) means the default."""
    return int(os.getenv(name) or default)
