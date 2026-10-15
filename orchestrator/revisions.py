"""Rebuild an existing case's pack as a new version, keeping its case ref.

    python -m orchestrator.revisions c_0103 c_0104 ...

Used when the builder's rules change (e.g. the corrected proposed value). For each
ref: one standalone builder session on the same candidate row (same Scout run),
writing the pack as the next pack_version under the same ref; the case moves with
it (store.insert_pack_version). The watcher then runs the VOA advocate on the new
version and updates the case. If the builder finds no case under the new rules,
the case is closed here (before owner approval) or handed to a person (after).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from agents.builder.runner import build_candidate
from orchestrator.config import anthropic_client, load_env
from orchestrator.data_client import DataClient, make_data_client
from orchestrator.store import Store, get_store

AGENT = "orchestrator"


def revise_case(ref: str, *, client=None, store: Store | None = None,
                data: DataClient | None = None, verbose: bool = False) -> str:
    """Rebuild ref's pack as a new version. Returns the candidate's status afterwards:
    pack_ready (a new version is waiting for the advocate) or no_case."""
    load_env()
    store = store or get_store()
    pack = store.get_pack(ref)
    if pack is None:
        raise ValueError(f"no pack {ref}")
    cand = store._one("candidates", [("candidate_id", "eq", pack["candidate_id"])])
    store.add_event(AGENT, "pack_rebuild_started",
                    f"Rebuilding {ref} (version {pack.get('pack_version') or 1}) with the "
                    "corrected rules", case_id=ref)
    status, result = build_candidate(client or anthropic_client(), store,
                                     data or make_data_client(), pack["uarn"],
                                     verbose=verbose, run_id=cand["run_id"], revise_ref=ref)
    if status == "no_case":
        _close_without_case(store, ref)
    return status


def _close_without_case(store: Store, ref: str) -> None:
    case = store.get_case(ref)
    store.set_pack_status(ref, "killed")
    if case is None:
        return
    stage = "needs_human" if case["owner_approved"] else "closed"
    store.update_case(ref, {"stage": stage, "history": case["history"] + [{
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "type": "rechecked",
        "text": "Rebuilt with the corrected rules: no clear case any more."}]})
    store.add_event(AGENT, "case_closed" if stage == "closed" else "case_needs_human",
                    "Rebuilt with the corrected rules: no clear case", case_id=ref)


if __name__ == "__main__":
    for ref in sys.argv[1:]:
        print(ref, revise_case(ref, verbose=True))
