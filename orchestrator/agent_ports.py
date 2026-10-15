"""Entry points the orchestrator calls on person 3's agents.

Person 3 provides, on their branch:

    agents/caseworker/runner.py
        def run_caseworker(case_id: str) -> None:
            # One short Caseworker session for this case. The case row is its
            # memory; it reads the case and recent events (including any
            # voa_reply), acts, and updates the case. Start the session through
            # orchestrator.sessions.run_session so its spend is logged.

    agents/playbook/runner.py
        def run_playbook_keeper() -> None:
            # The nightly session: proposes a candidate playbook and asks the
            # eval agent to score it.

Until those exist, the stubs raise and the scheduler logs that the agent
isn't wired in yet. Triggers still fire and are recorded in events.
"""

from __future__ import annotations


class AgentUnavailable(RuntimeError):
    pass


try:
    from agents.caseworker.runner import run_caseworker  # noqa: F401
    CASEWORKER_AVAILABLE = True
except ImportError:
    CASEWORKER_AVAILABLE = False

    def run_caseworker(case_id: str) -> None:
        raise AgentUnavailable("agents.caseworker.runner is not on this branch yet")


try:
    from agents.playbook.runner import run_playbook_keeper  # noqa: F401
    PLAYBOOK_AVAILABLE = True
except ImportError:
    PLAYBOOK_AVAILABLE = False

    def run_playbook_keeper() -> None:
        raise AgentUnavailable("agents.playbook.runner is not on this branch yet")
