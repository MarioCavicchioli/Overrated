"""Run the Case builder on one candidate in a standalone session (outside the Scout).

Contract with the orchestrator:

    from agents.builder.runner import run_builder
    outcome = run_builder(uarn)      # "pack_ready" or "no_case"

The Scout builds its own candidates inside its workflow runs. This is for a single
property handed to the pipeline on its own, such as a customer's lookup in the app.
It starts one builder session through orchestrator.sessions.run_session (so its
spend is logged), answers its tool calls with BuilderHandlers, and raises
BuilderError if the session ends without a pack or a no_case.
"""

from __future__ import annotations

from agents.builder.handlers import AGENT, BuilderHandlers
from orchestrator import budget
from orchestrator.config import anthropic_client, load_env
from orchestrator.data_client import DataClient, make_data_client
from orchestrator.sessions import SessionResult, resource_id, run_session
from orchestrator.store import Store, get_store

MODEL = "claude-sonnet-5-5"   # keep in step with agent.md
# Own lockfile (the root one belongs to the Scout's workspace):
#   python -m orchestrator.ant apply --lock-file agents/builder/claude-lock.json \
#       agents/builder/agent.md agents/builder/environment.yaml
LOCK_FILE = "agents/builder/claude-lock.json"
OUTCOMES = ("pack_ready", "no_case")


class BuilderError(RuntimeError):
    """The builder session ended without a pack or a no_case."""


def build_candidate(client, store: Store, data: DataClient, uarn: str,
                    verbose: bool = True, run_id: str | None = None,
                    revise_ref: str | None = None) -> tuple[str, SessionResult]:
    """One builder session for a candidate already in the store. Returns the
    candidate's status afterwards and the session result. With revise_ref, the pack
    is written as a new version of that case's pack."""
    if store.get_candidate(uarn, run_id) is None:
        raise BuilderError(f"{uarn} is not in candidates")
    store.set_candidate_status(uarn, "building_pack", run_id)
    result = run_session(
        client, store, agent=AGENT,
        agent_id=resource_id("BUILDER_AGENT_ID", "./agent.md", LOCK_FILE),
        environment_id=resource_id("BUILDER_ENV_ID", "./environment.yaml", LOCK_FILE),
        title=f"builder {uarn}", model=MODEL, message=f"Candidate UARN: {uarn}",
        handle=BuilderHandlers(store, data, run_id=run_id, revise_ref=revise_ref).handle,
        meter=budget.SpendMeter(model=MODEL), label=uarn, verbose=verbose,
    )
    return store.get_candidate(uarn, run_id)["status"], result


def run_builder(uarn: str, *, client=None, store: Store | None = None,
                data: DataClient | None = None, verbose: bool = False) -> str:
    """Build one candidate. Returns "pack_ready" or "no_case"; raises BuilderError otherwise."""
    load_env()
    store = store or get_store()
    status, result = build_candidate(client or anthropic_client(), store,
                                     data or make_data_client(), uarn, verbose=verbose)
    if status not in OUTCOMES:
        raise BuilderError(f"{uarn}: no pack and no no_case (session {result.session_id} "
                           f"stopped: {result.stop_reason}; status {status})")
    return status
