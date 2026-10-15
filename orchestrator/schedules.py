"""Wake-ups for the Caseworker and the nightly playbook run.

The Caseworker never stays running. A short session starts for a case when:
- a VOA reply arrives: an events row with kind 'voa_reply' and the case_id
  (written by the mocked VOA API, and later the real one);
- its next_deadline is within 3 days (at most one deadline wake per day);
- the daily sweep runs (every open case, once a day from DAILY_SWEEP_HOUR_UTC,
  unless something already woke it that day).

The playbook keeper runs once a night from NIGHTLY_PLAYBOOK_HOUR_UTC.

Every trigger is written to events before the agent runs, which is also how the
scheduler remembers what it has done: restarts don't repeat a wake.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from orchestrator import agent_ports
from orchestrator.config import env_int
from orchestrator.store import Store

log = logging.getLogger(__name__)
AGENT = "orchestrator"

OPEN_STAGES = ("check_ready", "check", "challenge", "tribunal")
DEADLINE_WINDOW = timedelta(days=3)
WAKE_KIND = {"voa_reply": "caseworker_wake_reply",
             "deadline": "caseworker_wake_deadline",
             "daily": "caseworker_wake_daily"}


def parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


class Scheduler:
    def __init__(self, store: Store, *,
                 run_caseworker: Callable[[str], None] | None = None,
                 run_playbook_keeper: Callable[[], None] | None = None,
                 now: Callable[[], datetime] | None = None,
                 daily_hour: int | None = None, nightly_hour: int | None = None,
                 max_concurrent: int | None = None):
        self.store = store
        self.run_caseworker = run_caseworker or agent_ports.run_caseworker
        self.run_playbook_keeper = run_playbook_keeper or agent_ports.run_playbook_keeper
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.daily_hour = env_int("DAILY_SWEEP_HOUR_UTC", 7) if daily_hour is None else daily_hour
        self.nightly_hour = (env_int("NIGHTLY_PLAYBOOK_HOUR_UTC", 2) if nightly_hour is None
                             else nightly_hour)
        self.max_concurrent = max_concurrent or env_int("MAX_CONCURRENT_CASEWORKERS", 4)
        self.in_flight: set[str] = set()
        self.queued: list[str] = []      # recorded wakes waiting for a free slot
        self.rerun: set[str] = set()     # replies that came in while the case was running
        self.lock = threading.Lock()
        self.cursor: int | None = None   # last voa_reply event id dealt with
        self.seen_reply: int | None = None

    # -- decisions ---------------------------------------------------------
    def _reply_cursor(self) -> int:
        """Id of the last voa_reply event already handled (kept in the wake event's message)."""
        last = self.store.last_event(WAKE_KIND["voa_reply"])
        if last is None:
            return 0
        head = last["message"].split(":", 1)[0]          # "voa_reply #123"
        return int(head.rsplit("#", 1)[1]) if "#" in head else 0

    def _woken_since(self, case_id: str, since: datetime, kinds=tuple(WAKE_KIND.values())) -> bool:
        for kind in kinds:
            last = self.store.last_event(kind, case_id)
            if last and parse_ts(last["ts"]) >= since:
                return True
        return False

    def due_wakes(self) -> list[tuple[str, str, str, int | None]]:
        """(case_id, reason, detail, reply_id) for every wake due now, at most one per case."""
        now = self.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        due: dict[str, tuple[str, str, int | None]] = {}

        if self.cursor is None:
            self.cursor = self._reply_cursor()
        for ev in self.store.events_after("voa_reply", self.cursor):
            case = self.store.get_case(ev["case_id"]) if ev.get("case_id") else None
            if case and case["stage"] not in ("won", "lost", "closed"):
                due.setdefault(ev["case_id"],
                               ("voa_reply", f"voa_reply #{ev['id']}: VOA replied", ev["id"]))
            self.seen_reply = ev["id"]   # replies needing no wake are dealt with

        open_cases = self.store.list_cases(OPEN_STAGES)
        for case in open_cases:
            cid, deadline = case["case_id"], case.get("next_deadline")
            if cid in due or not deadline:
                continue
            if parse_ts(deadline) <= now + DEADLINE_WINDOW and not self._woken_since(
                    cid, now - timedelta(days=1), (WAKE_KIND["deadline"],)):
                due[cid] = ("deadline", f"deadline {deadline} is within 3 days", None)

        if now.hour >= self.daily_hour:
            for case in open_cases:
                cid = case["case_id"]
                if cid not in due and not self._woken_since(cid, today):
                    due[cid] = ("daily", "daily sweep", None)

        return [(cid, *rest) for cid, rest in due.items()]

    def _stamp(self) -> str:
        """Trigger events carry the scheduler's own clock, which the dedupe checks compare to."""
        return self.now().isoformat()

    def playbook_due(self) -> bool:
        now = self.now()
        if now.hour < self.nightly_hour:
            return False
        last = self.store.last_event("playbook_triggered")
        return last is None or parse_ts(last["ts"]).date() != now.date()

    # -- one pass ----------------------------------------------------------
    def tick(self) -> list[Callable[[], None]]:
        """Record every trigger that is due and return the jobs to run."""
        self.seen_reply = None
        for case_id, reason, detail, _ in self.due_wakes():
            with self.lock:
                running, waiting = case_id in self.in_flight, case_id in self.queued
            if running or waiting:
                if reason == "voa_reply":   # don't lose a reply to a session already running
                    self.store.add_event(AGENT, WAKE_KIND[reason], detail, case_id=case_id,
                                         ts=self._stamp())
                    with self.lock:
                        if running:
                            self.rerun.add(case_id)
                continue
            self.store.add_event(AGENT, WAKE_KIND[reason], detail, case_id=case_id, ts=self._stamp())
            with self.lock:
                self.queued.append(case_id)
        if self.seen_reply is not None:
            self.cursor = self.seen_reply

        jobs: list[Callable[[], None]] = []
        with self.lock:
            while self.queued and len(self.in_flight) < self.max_concurrent:
                case_id = self.queued.pop(0)
                self.in_flight.add(case_id)
                jobs.append(lambda cid=case_id: self._run_caseworker(cid))
        if self.playbook_due():
            self.store.add_event(AGENT, "playbook_triggered",
                                 f"nightly playbook run for {self.now().date()}",
                                 ts=self._stamp())
            jobs.append(self._run_playbook)
        return jobs

    def _run_caseworker(self, case_id: str) -> None:
        while True:
            try:
                self.run_caseworker(case_id)
            except agent_ports.AgentUnavailable:
                self.store.add_event(AGENT, "caseworker_unavailable",
                                     "Wake-up recorded, but the Caseworker isn't wired in yet",
                                     case_id=case_id)
            except Exception as e:
                log.exception("caseworker failed on %s", case_id)
                self.store.add_event(AGENT, "caseworker_failed",
                                     f"Caseworker failed: {type(e).__name__}", case_id=case_id)
            with self.lock:
                if case_id in self.rerun:   # a reply arrived mid-session: run once more
                    self.rerun.discard(case_id)
                    continue
                self.in_flight.discard(case_id)
                return

    def _run_playbook(self) -> None:
        try:
            self.run_playbook_keeper()
        except agent_ports.AgentUnavailable:
            self.store.add_event(AGENT, "playbook_unavailable",
                                 "Nightly trigger recorded, but the playbook keeper isn't wired in yet")
        except Exception as e:
            log.exception("playbook keeper failed")
            self.store.add_event(AGENT, "playbook_failed",
                                 f"Playbook keeper failed: {type(e).__name__}")

    # -- the loop ------------------------------------------------------------
    async def watch(self, interval_s: float | None = None) -> None:
        interval_s = interval_s or env_int("SCHEDULE_INTERVAL_S", 30)
        while True:
            try:
                for job in self.tick():
                    asyncio.create_task(asyncio.to_thread(job))
            except Exception:
                log.exception("schedule tick failed")
            await asyncio.sleep(interval_s)
