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


def env_int(name: str, default: int) -> int:
    """An integer setting; an empty value (as in .env.example) means the default."""
    return int(os.getenv(name) or default)
