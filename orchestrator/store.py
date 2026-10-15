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
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parent.parent

# candidates.status values. 'no_case' = dropped by the builder (fewer than 3
# usable comparables), kept apart from 'killed' (killed by the advocate).
CANDIDATE_STATUSES = ("new", "building", "packed", "no_case", "killed", "survived")


class Store(Protocol):
    def upsert_candidate(self, row: dict[str, Any]) -> None: ...
    def get_candidate(self, uarn: str) -> dict[str, Any] | None: ...
    def set_candidate_status(self, uarn: str, status: str) -> None: ...
    def next_case_id(self) -> str: ...
    def insert_pack(self, row: dict[str, Any]) -> None: ...
    def get_pack(self, case_id: str) -> dict[str, Any] | None: ...
    def save_pdf(self, case_id: str, data: bytes) -> str: ...
    def add_event(self, agent: str, kind: str, message: str,
                  case_id: str | None = None, tokens: int = 0) -> None: ...
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
        with self.db:
            self.db.execute(sql, [row[c] for c in cols])

    # -- candidates ------------------------------------------------------
    def upsert_candidate(self, row: dict[str, Any]) -> None:
        self._insert("candidates", row, upsert_key="uarn")

    def get_candidate(self, uarn: str) -> dict[str, Any] | None:
        return self._decode(self.db.execute(
            "select * from candidates where uarn = ?", (uarn,)).fetchone())

    def set_candidate_status(self, uarn: str, status: str) -> None:
        if status not in CANDIDATE_STATUSES:
            raise ValueError(f"unknown candidate status {status!r}")
        with self.db:
            self.db.execute("update candidates set status = ? where uarn = ?", (status, uarn))

    # -- packs -----------------------------------------------------------
    def next_case_id(self) -> str:
        row = self.db.execute(
            "select max(cast(substr(case_id, 3) as integer)) from packs").fetchone()
        return f"c_{(row[0] or 0) + 1:04d}"

    def insert_pack(self, row: dict[str, Any]) -> None:
        self._insert("packs", row)

    def get_pack(self, case_id: str) -> dict[str, Any] | None:
        return self._decode(self.db.execute(
            "select * from packs where case_id = ?", (case_id,)).fetchone())

    def save_pdf(self, case_id: str, data: bytes) -> str:
        path = self.pdf_dir / f"{case_id}.pdf"
        path.write_bytes(data)
        return path.as_uri()

    # -- events ----------------------------------------------------------
    def add_event(self, agent: str, kind: str, message: str,
                  case_id: str | None = None, tokens: int = 0) -> None:
        self._insert("events", {"agent": agent, "kind": kind, "message": message,
                                "case_id": case_id, "tokens": tokens})

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "select * from events order by id desc limit ?", (limit,)).fetchall()
        return [self._decode(r) for r in reversed(rows)]


def get_store() -> Store:
    backend = os.getenv("STORE_BACKEND", "sqlite")
    if backend == "sqlite":
        return SQLiteStore()
    if backend == "supabase":
        raise NotImplementedError("Supabase backend arrives in step 4")
    raise ValueError(f"unknown STORE_BACKEND {backend!r}")
