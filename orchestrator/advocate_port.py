"""Where the orchestrator gets run_advocate(case_id) from.

The VOA advocate is built on the p2-advocate branch. It must provide
agents/advocate/runner.py with:

    def run_advocate(case_id: str) -> None:
        # Starts one advocate session for the pack (orchestrator.sessions.run_session
        # logs its spend), and writes one verdicts row: survive or kill, with
        # reasons [{reason, voa_record}]. Raises on failure; the watcher retries.

Until that module exists, the stub raises AdvocateUnavailable and packs wait.
"""

from __future__ import annotations


class AdvocateUnavailable(RuntimeError):
    pass


try:
    from agents.advocate.runner import run_advocate  # noqa: F401
    ADVOCATE_AVAILABLE = True
except ImportError:
    ADVOCATE_AVAILABLE = False

    def run_advocate(case_id: str) -> None:
        raise AdvocateUnavailable("agents.advocate.runner is not on this branch yet")
