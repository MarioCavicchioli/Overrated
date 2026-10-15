"""Cases: created when a pack survives the advocate, approved by the owner.

Approval is the gate before anything is filed. It needs proof of ratepayer
authority (for now: a rates bill account reference, present and non-empty) and
the owner's confirmation that the facts are true. Only then does the case move
to check_ready, where the Caseworker picks it up.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from orchestrator.store import Store

AGENT = "orchestrator"

# Shown on every case in the app, before the owner approves.
RISK_LINE = ("Risk: when the VOA reviews a valuation it can raise it as well as lower it, "
             "which would increase your bill. Giving false information in a Check or "
             "Challenge can lead to a penalty.")


class ApprovalError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def risk_line(case: dict[str, Any]) -> str:
    for entry in case.get("history", []):
        if entry.get("type") == "risk":
            return entry["text"]
    return RISK_LINE


def create_case(store: Store, case_id: str, uarn: str) -> None:
    store.insert_case({
        "case_id": case_id, "uarn": uarn, "stage": "waiting_owner", "owner_approved": False,
        "history": [
            {"ts": _now(), "type": "created",
             "text": "Survived the VOA advocate. Waiting for the owner's approval."},
            {"ts": _now(), "type": "risk", "text": RISK_LINE},
        ],
    })


def _mask(reference: str) -> str:
    """Never store or log the full account reference."""
    return f"ending {reference[-4:]}" if len(reference) > 6 else "provided"


def approve_case(store: Store, case_id: str, account_reference: str | None,
                 facts_confirmed: bool) -> dict[str, Any]:
    case = store.get_case(case_id)
    if case is None:
        raise ApprovalError(404, f"no case {case_id}")
    if case["owner_approved"] or case["stage"] != "waiting_owner":
        raise ApprovalError(409, f"case {case_id} is at stage {case['stage']}, "
                                 "not waiting_owner")
    reference = (account_reference or "").strip()
    if not reference:
        raise ApprovalError(422, "proof of ratepayer authority is required: "
                                 "the rates bill account reference")
    if facts_confirmed is not True:
        raise ApprovalError(422, "the owner must confirm the facts in the pack are true")

    history = case["history"] + [{
        "ts": _now(), "type": "owner_approved",
        "text": "Owner confirmed the facts are true and gave the rates bill account "
                f"reference ({_mask(reference)}). Queued for the Caseworker to file the Check.",
    }]
    store.update_case(case_id, {"owner_approved": True, "owner_approved_at": _now(),
                                "stage": "check_ready", "history": history})
    store.add_event(AGENT, "owner_approved",
                    "Owner approved with proof of authority; case ready for the Check",
                    case_id=case_id)
    return store.get_case(case_id)
