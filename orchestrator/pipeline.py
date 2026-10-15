"""Watches the store and moves cases along.

- A customer request starts a standalone builder session (outside the Scout):
  a candidate with status building_pack, "customer_request" in its flags and no
  pack yet -> run_builder(uarn). The Scout also marks its own batches
  building_pack, but without that flag, so they're never built twice.
- A packs row with no verdict starts an advocate session: run_advocate(case_id).
- A survive verdict creates a cases row at stage waiting_owner.
- A kill verdict sets the candidate to killed, with the reasons in events.

Polling works the same over SQLite and Supabase. Every step is idempotent, so a
restarted orchestrator picks up where it stopped.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable

from orchestrator import advocate_port
from orchestrator.cases import create_case
from orchestrator.config import env_int
from orchestrator.store import Store

log = logging.getLogger(__name__)
AGENT = "orchestrator"
CUSTOMER_FLAG = "customer_request"


def _default_run_builder(uarn: str) -> str:
    from agents.builder.runner import run_builder
    return run_builder(uarn)


class Pipeline:
    def __init__(self, store: Store,
                 run_advocate: Callable[[str], None] | None = None,
                 advocate_available: bool | None = None,
                 max_attempts: int = 3, max_concurrent: int | None = None,
                 run_builder: Callable[[str], str] | None = None,
                 max_concurrent_builds: int | None = None):
        self.store = store
        self.run_advocate = run_advocate or advocate_port.run_advocate
        self.run_builder = run_builder or _default_run_builder
        self.available = (advocate_port.ADVOCATE_AVAILABLE if advocate_available is None
                           else advocate_available)
        self.max_attempts = max_attempts
        self.max_concurrent = max_concurrent or env_int("MAX_CONCURRENT_ADVOCATES", 4)
        self.max_builds = max_concurrent_builds or env_int("MAX_CONCURRENT_BUILDERS", 2)
        self.in_flight: set[str] = set()
        self.building: set[str] = set()
        self.lock = threading.Lock()

    def our_case_ids(self) -> list[str]:
        """Packs in our case ref range only (c_0101 and up live); others are left alone."""
        return [ref for ref in self.store.list_case_ids() if self.store.is_ours(ref)]

    # -- customer requests -> builder ----------------------------------------
    def candidates_to_build(self) -> list[dict]:
        out = []
        for cand in self.store.list_candidates("building_pack"):
            if CUSTOMER_FLAG not in (cand.get("flags") or []):
                continue                       # a Scout batch: its own builders handle it
            with self.lock:
                if cand["uarn"] in self.building:
                    continue
            if self.store.has_pack(cand["uarn"]):
                continue
            if self.store.count_candidate_events("builder_started",
                                                 cand["candidate_id"]) >= self.max_attempts:
                continue
            out.append(cand)
        return out

    def build(self, uarn: str, candidate_id: str) -> None:
        """Run one standalone builder session for a customer request. Blocking."""
        self.store.add_event(AGENT, "builder_started",
                             f"Customer request {uarn}: starting the Case builder",
                             candidate_id=candidate_id)
        try:
            outcome = self.run_builder(uarn)
            self.store.add_event(AGENT, "builder_finished", f"Customer request {uarn}: {outcome}",
                                 candidate_id=candidate_id)
        except Exception as e:
            log.exception("builder failed on %s", uarn)
            self.store.add_event(AGENT, "builder_failed", f"Builder failed: {type(e).__name__}",
                                 candidate_id=candidate_id)

    def tick_builds(self) -> list[dict]:
        """Customer requests to start builders on now (marked in flight)."""
        started = []
        for cand in self.candidates_to_build():
            with self.lock:
                if len(self.building) >= self.max_builds:
                    break
                self.building.add(cand["uarn"])
            started.append(cand)
        return started

    # -- packs -> advocate -------------------------------------------------
    def packs_to_judge(self) -> list[str]:
        out = []
        for case_id in self.our_case_ids():
            if self.store.latest_verdict(case_id) is not None:
                continue
            with self.lock:
                if case_id in self.in_flight:
                    continue
            if not self.available:
                if self.store.count_events("advocate_unavailable", case_id) == 0:
                    self.store.add_event(AGENT, "advocate_unavailable",
                                         "Pack waiting: the VOA advocate isn't wired in yet",
                                         case_id=case_id)
                continue
            if self.store.count_events("advocate_started", case_id) >= self.max_attempts:
                continue
            out.append(case_id)
        return out

    def judge(self, case_id: str) -> None:
        """Run one advocate session for a pack. Blocking; call from a worker thread."""
        self.store.add_event(AGENT, "advocate_started", "Starting the VOA advocate on this pack",
                             case_id=case_id)
        self.store.set_pack_status(case_id, "reviewing")
        try:
            self.run_advocate(case_id)
        except Exception as e:
            log.exception("advocate failed on %s", case_id)
            self.store.add_event(AGENT, "advocate_failed", f"Advocate failed: {type(e).__name__}",
                                 case_id=case_id)
        if self.store.latest_verdict(case_id) is None:
            self.store.add_event(AGENT, "advocate_no_verdict",
                                 "Advocate session ended without a verdict", case_id=case_id)
        self.apply_verdicts()

    # -- verdicts -> cases / killed ----------------------------------------
    def apply_verdicts(self) -> None:
        for case_id in self.our_case_ids():
            verdict = self.store.latest_verdict(case_id)
            if verdict is None:
                continue
            pack = self.store.get_pack(case_id)
            uarn = pack["uarn"]
            if verdict["verdict"] == "survive":
                if self.store.get_case(case_id) is None:
                    self.store.set_pack_status(case_id, "survive")
                    create_case(self.store, case_id, uarn)
                    self.store.set_pack_status(case_id, "waiting_owner")
                    self.store.set_candidate_status(uarn, "waiting_owner")
                    self.store.add_event(AGENT, "case_opened",
                                         "Survived the VOA advocate; waiting for the owner",
                                         case_id=case_id)
            elif verdict["verdict"] == "kill":
                candidate = self.store.get_candidate(uarn)
                if candidate and candidate["status"] != "killed" \
                        and self.store.get_case(case_id) is None:
                    self.store.set_candidate_status(uarn, "killed")
                    self.store.set_pack_status(case_id, "killed")
                    self.store.add_event(AGENT, "killed",
                                         "Killed by the VOA advocate: " + _reasons(verdict),
                                         case_id=case_id)

    # -- the loop ------------------------------------------------------------
    def tick(self) -> list[str]:
        """Apply verdicts and return the packs to start advocates on (marked in flight)."""
        self.apply_verdicts()
        started = []
        for case_id in self.packs_to_judge():
            with self.lock:
                if len(self.in_flight) >= self.max_concurrent:
                    break
                self.in_flight.add(case_id)
            started.append(case_id)
        return started

    async def watch(self, interval_s: float | None = None) -> None:
        interval_s = interval_s or env_int("WATCH_INTERVAL_S", 5)
        while True:
            try:
                for cand in self.tick_builds():
                    asyncio.create_task(self._build_async(cand["uarn"], cand["candidate_id"]))
                for case_id in self.tick():
                    asyncio.create_task(self._judge_async(case_id))
            except Exception:
                log.exception("watch tick failed")
            await asyncio.sleep(interval_s)

    async def _build_async(self, uarn: str, candidate_id: str) -> None:
        try:
            await asyncio.to_thread(self.build, uarn, candidate_id)
        finally:
            with self.lock:
                self.building.discard(uarn)

    async def _judge_async(self, case_id: str) -> None:
        try:
            await asyncio.to_thread(self.judge, case_id)
        finally:
            with self.lock:
                self.in_flight.discard(case_id)


def _reasons(verdict: dict) -> str:
    reasons = verdict.get("reasons") or []
    parts = [f"{r.get('reason', '').strip()} [{r.get('voa_record', '?')}]" for r in reasons]
    return "; ".join(parts)[:900] or "no reasons given"
