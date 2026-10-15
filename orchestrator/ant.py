"""Run the ant CLI with the keys from .env, without exposing them.

    python -m orchestrator.ant apply --dry-run -v agents/builder/agent.md agents/builder/environment.yaml

The key reaches ant only through its environment. Set ANT_PATH if ant isn't on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from orchestrator.config import ROOT, load_env


def main(args: list[str]) -> int:
    load_env()
    ant = os.getenv("ANT_PATH") or shutil.which("ant")
    if not ant:
        sys.exit("ant not found: put it on PATH or set ANT_PATH")
    if "--debug" in args:
        sys.exit("refusing --debug here: it prints full HTTP requests")
    return subprocess.call([ant, *args], cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
