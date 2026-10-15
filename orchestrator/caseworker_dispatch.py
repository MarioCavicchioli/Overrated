"""Integration surface for the FastAPI orchestrator and a periodic scheduler."""

from __future__ import annotations

import argparse
import json
from typing import Any


def on_persisted_event(event_id: str) -> dict[str, Any]:
    """Call after the orchestrator commits a VOA reply event."""
    from agents.caseworker.caseworker import process_reply_event
    return process_reply_event(event_id)


def run_deadline_sweep(limit: int = 100) -> list[dict[str, Any]]:
    """Call periodically (for example, every 15 minutes) from the host scheduler."""
    from agents.caseworker.caseworker import process_due_deadlines
    return process_due_deadlines(limit=limit)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Overrated Caseworker dispatch.")
    parser.add_argument("--deadlines-once", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not args.deadlines_once:
        parser.error("use --deadlines-once for a scheduled deadline sweep")
    print(json.dumps(run_deadline_sweep(args.limit), ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
