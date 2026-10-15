"""Database access for the orchestrator and the tool handlers it runs.

One interface, two backends. STORE_BACKEND=sqlite (default until person 3's
Supabase project is ready) or STORE_BACKEND=supabase (step 4). Switching is
that one variable plus the Supabase keys in .env.

Only the orchestrator process holds a Store. Hosted agents never get a key;
their writes go through tool handlers that call this module.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parent.parent

# candidates.status values. 'no_case' = dropped by the builder (fewer than 3
# usable comparables), kept apart from 'killed' (killed by the advocate).
CANDIDATE_STATUSES = ("new", "building", "packed", "no_case", "killed", "survived")

# cases.stage values. 'check_ready' = owner approved with proof of authority;
# the Caseworker picks these up to file the Check.
CASE_STAGES = ("awaiting_owner", "check_ready", "check", "challenge", "tribunal",
               "won", "lost", "closed")


class Store(Protocol):
    def upsert_candidate(self, row: dict[str, Any]) -> None: ...
    def get_candidate(self, uarn: str) -> dict[str, Any] | None: ...
    def set_candidate_status(self, uarn: str, status: str) -> None: ...
    def next_case_id(self) -> str: ...
    def insert_pack(self, row: dict[str, Any]) -> None: ...
    def get_pack(self, case_id: str) -> dict[str, Any] | None: ...
    def list_case_ids(self) -> list[str]: ...
    def save_pdf(self, case_id: str, data: bytes) -> str: ...
    def insert_verdict(self, row: dict[str, Any]) -> None: ...
    def latest_verdict(self, case_id: str) -> dict[str, Any] | None: ...
    def insert_case(self, row: dict[str, Any]) -> None: ...
    def get_case(self, case_id: str) -> dict[str, Any] | None: ...
    def update_case(self, case_id: str, changes: dict[str, Any]) -> None: ...
    def list_cases(self, stages: tuple[str, ...]) -> list[dict[str, Any]]: ...
    def add_event(self, agent: str, kind: str, message: str,
                  case_id: str | None = None, tokens: int = 0,
                  ts: str | None = None) -> None: ...
    def count_events(self, kind: str, case_id: str) -> int: ...
    def last_event(self, kind: str, case_id: str | None = None) -> dict[str, Any] | None: ...
    def events_after(self, kind: str, after_id: int, limit: int = 200) -> list[dict[str, Any]]: ...
    def list_events(self, limit: int = 50) -> list[dict[str, Any]]: ...


class SQLiteStore:
    JSON_COLUMNS = {"flags", "pack", "comparables", "reasons", "history", "rules"}

    def __init__(self, path: str | Path | None = None, pdf_dir: str | Path | None = None):
        self.path = Path(path or os.getenv("SQLITE_PATH", ROOT / "out" / "overrated.db"))
        self.pdf_dir = Path(pdf_dir or os.getenv("PDF_DIR", ROOT / "out" / "pdfs"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma foreign_keys = on")
        self.db.executescript((Path(__file__).parent / "sqlite_schema.sql").read_text())
        # One connection shared by the watcher and advocate worker threads.
        self.lock = threading.RLock()

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, params).fetchall()

    def _write(self, sql: str, params: tuple | list = ()) -> None:
        with self.lock, self.db:
            self.db.execute(sql, params)

    # -- helpers ---------------------------------------------------------
    def _encode(self, row: dict[str, Any]) -> dict[str, Any]:
        return {k: json.dumps(v) if k in self.JSON_COLUMNS and not isinstance(v, str) else v
                for k, v in row.items()}

    def _decode(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        for k in self.JSON_COLUMNS & out.keys():
            if isinstance(out[k], str):
                out[k] = json.loads(out[k])
        return out

    def _insert(self, table: str, row: dict[str, Any], upsert_key: str | None = None) -> None:
        row = self._encode(row)
        cols = list(row)
        # Table and column names come from our own code, never from agent input;
        # values are always bound parameters.
        sql = f"insert into {table} ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})"
        if upsert_key:
            updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != upsert_key)
            sql += f" on conflict({upsert_key}) do update set {updates}"
        self._write(sql, [row[c] for c in cols])

    def _one(self, sql: str, params: tuple) -> dict[str, Any] | None:
        rows = self._query(sql, params)
        return self._decode(rows[0]) if rows else None

    # -- candidates ------------------------------------------------------
    def upsert_candidate(self, row: dict[str, Any]) -> None:
        self._insert("candidates", row, upsert_key="uarn")

    def get_candidate(self, uarn: str) -> dict[str, Any] | None:
        return self._one("select * from candidates where uarn = ?", (uarn,))

    def set_candidate_status(self, uarn: str, status: str) -> None:
        if status not in CANDIDATE_STATUSES:
            raise ValueError(f"unknown candidate status {status!r}")
        self._write("update candidates set status = ? where uarn = ?", (status, uarn))

    # -- packs -----------------------------------------------------------
    def next_case_id(self) -> str:
        row = self._query("select max(cast(substr(case_id, 3) as integer)) from packs")[0]
        return f"c_{(row[0] or 0) + 1:04d}"

    def insert_pack(self, row: dict[str, Any]) -> None:
        self._insert("packs", row)

    def get_pack(self, case_id: str) -> dict[str, Any] | None:
        return self._one("select * from packs where case_id = ?", (case_id,))

    def list_case_ids(self) -> list[str]:
        return [r[0] for r in self._query("select case_id from packs order by created_at, case_id")]

    # -- verdicts --------------------------------------------------------
    def insert_verdict(self, row: dict[str, Any]) -> None:
        self._insert("verdicts", row)

    def latest_verdict(self, case_id: str) -> dict[str, Any] | None:
        return self._one("select * from verdicts where case_id = ? order by id desc limit 1",
                         (case_id,))

    # -- cases -----------------------------------------------------------
    CASE_COLUMNS = {"stage", "next_deadline", "owner_approved", "history"}

    def insert_case(self, row: dict[str, Any]) -> None:
        self._insert("cases", row)

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        case = self._one("select * from cases where case_id = ?", (case_id,))
        if case is not None:
            case["owner_approved"] = bool(case["owner_approved"])
        return case

    def update_case(self, case_id: str, changes: dict[str, Any]) -> None:
        unknown = changes.keys() - self.CASE_COLUMNS
        if unknown:
            raise ValueError(f"cannot update cases columns {sorted(unknown)}")
        if "stage" in changes and changes["stage"] not in CASE_STAGES:
            raise ValueError(f"unknown case stage {changes['stage']!r}")
        row = self._encode(changes)
        sets = ", ".join(f"{c} = ?" for c in row)   # column names checked above
        self._write(f"update cases set {sets}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "where case_id = ?", [*row.values(), case_id])

    def list_cases(self, stages: tuple[str, ...]) -> list[dict[str, Any]]:
        marks = ", ".join("?" for _ in stages)
        rows = self._query(f"select * from cases where stage in ({marks}) order by case_id",
                           tuple(stages))
        out = [self._decode(r) for r in rows]
        for c in out:
            c["owner_approved"] = bool(c["owner_approved"])
        return out

    def save_pdf(self, case_id: str, data: bytes) -> str:
        path = self.pdf_dir / f"{case_id}.pdf"
        path.write_bytes(data)
        return path.as_uri()

    # -- events ----------------------------------------------------------
    def add_event(self, agent: str, kind: str, message: str,
                  case_id: str | None = None, tokens: int = 0,
                  ts: str | None = None) -> None:
        row = {"agent": agent, "kind": kind, "message": message,
               "case_id": case_id, "tokens": tokens}
        if ts:   # the scheduler stamps its own events with its clock
            row["ts"] = ts
        self._insert("events", row)

    def count_events(self, kind: str, case_id: str) -> int:
        return self._query("select count(*) from events where kind = ? and case_id = ?",
                           (kind, case_id))[0][0]

    def last_event(self, kind: str, case_id: str | None = None) -> dict[str, Any] | None:
        if case_id is None:
            return self._one("select * from events where kind = ? order by id desc limit 1",
                             (kind,))
        return self._one("select * from events where kind = ? and case_id = ? "
                         "order by id desc limit 1", (kind, case_id))

    def events_after(self, kind: str, after_id: int, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._query("select * from events where kind = ? and id > ? order by id limit ?",
                           (kind, after_id, limit))
        return [self._decode(r) for r in rows]

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._query("select * from events order by id desc limit ?", (limit,))
        return [self._decode(r) for r in reversed(rows)]


def get_store() -> Store:
    backend = os.getenv("STORE_BACKEND") or "sqlite"
    if backend == "sqlite":
        return SQLiteStore()
    if backend == "supabase":
        from orchestrator.supabase_store import SupabaseStore
        return SupabaseStore()
    raise ValueError(f"unknown STORE_BACKEND {backend!r}")
