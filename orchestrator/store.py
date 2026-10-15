"""Database access for the orchestrator and the tool handlers it runs.

One interface, two backends: STORE_BACKEND=sqlite (local copy of the contract)
or STORE_BACKEND=supabase (the live database). Both follow contracts/schema.sql,
which is generated from the live database.

The interface speaks in our readable case refs ("c_0001", the case_id in
pack.schema.json) and UARNs. Underneath, the real keys are UUIDs: packs.case_id,
pack_id, candidate_id, event_id, verdict_id. The ref lives in packs.case_ref.
Rows are ordered by created_at / ts, never by id.

Only the orchestrator process holds a Store. Hosted agents never get a key;
their writes go through tool handlers that call this module.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from orchestrator import contract

ROOT = Path(__file__).resolve().parent.parent

# The allowed values are the live check constraints in contracts/schema.sql.
#
# candidates.status: flagged (Scout) -> building_pack -> pack_ready (builder) ->
# waiting_owner (survived the advocate) -> approved (owner) -> in_case.
# 'no_case' = dropped by the builder: no defensible challenge case was found.
# 'killed' = the advocate rejected a built pack. We write only those words;
# the live check also allows packed, survived and dismissed.
CANDIDATE_STATUSES = ("flagged", "building_pack", "packed", "pack_ready", "no_case", "killed",
                      "survived", "waiting_owner", "approved", "in_case", "dismissed")

# packs.status: built -> reviewing (advocate running) -> survive -> waiting_owner
# -> approved, or killed. 'superseded' when a newer pack_version replaces it.
PACK_STATUSES = ("built", "reviewing", "survive", "killed", "waiting_owner", "approved",
                 "superseded")

# cases.stage. waiting_owner until the owner approves with proof of authority,
# then check_draft, where the Caseworker picks it up to draft the Check.
CASE_STAGES = ("waiting_owner", "check_ready", "check_draft", "check_submitted",
               "waiting_check_decision",
               "challenge_draft", "challenge_submitted", "waiting_challenge_decision",
               "appeal_draft", "appeal_submitted", "waiting_tribunal", "resolved", "closed",
               "needs_human")
FINISHED_STAGES = ("resolved", "closed")

CASE_UPDATABLE = {"stage", "next_deadline", "next_action", "owner_approved",
                  "owner_approved_at", "history"}
LOCAL_RUN_AREA = "orchestrator: candidates without a Scout run"

Where = list[tuple[str, str, Any]]          # (column, op, value); op: eq | in | gte
Order = list[tuple[str, bool]]              # (column, descending)


_clock_lock = threading.Lock()
_last_ts: datetime | None = None


def now_iso() -> str:
    """UTC now, strictly increasing within this process. Rows are ordered by
    created_at / ts, and the Windows clock can return the same value for many
    calls in a row, so a repeat is nudged forward by a microsecond."""
    global _last_ts
    with _clock_lock:
        ts = datetime.now(timezone.utc)
        if _last_ts is not None and ts <= _last_ts:
            ts = _last_ts + timedelta(microseconds=1)
        _last_ts = ts
        return ts.isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


class Store(Protocol):
    def upsert_candidate(self, row: dict[str, Any]) -> None: ...
    def get_candidate(self, uarn: str) -> dict[str, Any] | None: ...
    def set_candidate_status(self, uarn: str, status: str) -> None: ...
    def list_candidates(self, status: str) -> list[dict[str, Any]]: ...
    def has_pack(self, uarn: str) -> bool: ...
    def next_case_id(self) -> str: ...
    def insert_pack(self, row: dict[str, Any]) -> None: ...
    def get_pack(self, case_id: str) -> dict[str, Any] | None: ...
    def set_pack_status(self, case_id: str, status: str) -> None: ...
    def list_case_ids(self) -> list[str]: ...
    def save_pdf(self, case_id: str, data: bytes) -> str: ...
    def insert_verdict(self, row: dict[str, Any]) -> None: ...
    def latest_verdict(self, case_id: str) -> dict[str, Any] | None: ...
    def list_verdicts(self, case_id: str) -> list[dict[str, Any]]: ...
    def insert_case(self, row: dict[str, Any]) -> None: ...
    def get_case(self, case_id: str) -> dict[str, Any] | None: ...
    def update_case(self, case_id: str, changes: dict[str, Any]) -> None: ...
    def list_cases(self, stages: tuple[str, ...]) -> list[dict[str, Any]]: ...
    def add_event(self, agent: str, kind: str, message: str, case_id: str | None = None,
                  tokens: int = 0, ts: str | None = None, cost_usd: float | None = None,
                  idempotency_key: str | None = None, run_id: str | None = None,
                  payload: dict | None = None, candidate_id: str | None = None) -> None: ...
    def count_events(self, kind: str, case_id: str) -> int: ...
    def count_candidate_events(self, kind: str, candidate_id: str) -> int: ...
    def last_event(self, kind: str, case_id: str | None = None) -> dict[str, Any] | None: ...
    def events_since(self, kind: str, since_ts: str | None,
                     limit: int = 200) -> list[dict[str, Any]]: ...
    def list_events(self, limit: int = 50) -> list[dict[str, Any]]: ...


class BaseStore:
    """The Store interface over five table operations a backend provides:
    _insert, _select, _update, _count and _put_file."""

    # -- backend operations ----------------------------------------------------
    def _insert(self, table: str, row: dict[str, Any]) -> None: raise NotImplementedError
    def _select(self, table: str, where: Where = (), order: Order = (),
                limit: int | None = None) -> list[dict[str, Any]]: raise NotImplementedError
    def _update(self, table: str, changes: dict[str, Any], where: Where) -> None:
        raise NotImplementedError
    def _count(self, table: str, where: Where) -> int: raise NotImplementedError
    def _put_file(self, bucket: str, path: str, data: bytes, content_type: str) -> str:
        raise NotImplementedError

    def _one(self, table: str, where: Where, order: Order = ()) -> dict[str, Any] | None:
        rows = self._select(table, where, order, limit=1)
        return rows[0] if rows else None

    # -- case refs <-> keys ----------------------------------------------------
    def _pack_row(self, ref: str) -> dict[str, Any] | None:
        return self._one("packs", [("case_ref", "eq", ref)], [("created_at", True)])

    def _keys(self, ref: str | None) -> dict[str, str | None]:
        """case uuid, pack_id and candidate_id for a readable ref (None if unknown)."""
        if ref is None:
            return {"case_id": None, "pack_id": None, "candidate_id": None}
        if ref not in self._key_cache:
            row = self._pack_row(ref)
            if row is None:
                return {"case_id": None, "pack_id": None, "candidate_id": None}
            self._key_cache[ref] = {"case_id": row["case_id"], "pack_id": row["pack_id"],
                                    "candidate_id": row["candidate_id"]}
        return self._key_cache[ref]

    def _refs_for(self, case_uuids: set[str]) -> dict[str, str]:
        missing = {u for u in case_uuids if u and u not in self._ref_cache}
        if missing:
            for row in self._select("packs", [("case_id", "in", sorted(missing))]):
                if row.get("case_ref"):
                    self._ref_cache[row["case_id"]] = row["case_ref"]
        return {u: self._ref_cache[u] for u in case_uuids if u in self._ref_cache}

    def __init__(self) -> None:
        self._key_cache: dict[str, dict[str, str]] = {}
        self._ref_cache: dict[str, str] = {}
        self._run_id: str | None = None

    # -- candidates --------------------------------------------------------------
    def _local_run_id(self) -> str:
        """scout_runs row for candidates written outside a Scout run (tests, local copies)."""
        if self._run_id is None:
            row = self._one("scout_runs", [("area_name", "eq", LOCAL_RUN_AREA)],
                            [("created_at", True)])
            if row is None:
                row = {"run_id": new_id(), "area_name": LOCAL_RUN_AREA,
                       "source_snapshot": _snapshot(), "status": "running",
                       "config": {"source": "orchestrator"}, "created_at": now_iso(),
                       "updated_at": now_iso()}
                self._insert("scout_runs", row)
            self._run_id = row["run_id"]
        return self._run_id

    def upsert_candidate(self, row: dict[str, Any]) -> None:
        if row.get("status") and row["status"] not in CANDIDATE_STATUSES:
            raise ValueError(f"unknown candidate status {row['status']!r}")
        existing = self.get_candidate(row["uarn"])
        if existing:
            changes = {k: v for k, v in row.items() if k != "uarn"}
            self._update("candidates", {**changes, "updated_at": now_iso()},
                         [("candidate_id", "eq", existing["candidate_id"])])
            return
        self._insert("candidates", {
            "candidate_id": new_id(), "run_id": row.get("run_id") or self._local_run_id(),
            "source_snapshot": _snapshot(), "rv": 0, "flags": [], "evidence_refs": [],
            "status": "flagged", "created_at": now_iso(), "updated_at": now_iso(),
            **row,
        })

    def get_candidate(self, uarn: str) -> dict[str, Any] | None:
        return self._one("candidates", [("uarn", "eq", uarn)], [("created_at", True)])

    def set_candidate_status(self, uarn: str, status: str) -> None:
        if status not in CANDIDATE_STATUSES:
            raise ValueError(f"unknown candidate status {status!r}")
        cand = self.get_candidate(uarn)
        if cand:
            self._update("candidates", {"status": status, "updated_at": now_iso()},
                         [("candidate_id", "eq", cand["candidate_id"])])

    def list_candidates(self, status: str) -> list[dict[str, Any]]:
        if status not in CANDIDATE_STATUSES:
            raise ValueError(f"unknown candidate status {status!r}")
        return self._select("candidates", [("status", "eq", status)], [("created_at", False)])

    def has_pack(self, uarn: str) -> bool:
        """Any pack for this property, from any builder."""
        return bool(self._select("packs", [("uarn", "eq", uarn)], limit=1))

    # -- packs -----------------------------------------------------------------
    # Our case refs start here. Live: c_0101, because Person 1's app uses c_0001-c_0008.
    CASE_REF_START = 1

    def next_case_id(self) -> str:
        start = int(os.getenv("CASE_REF_START") or self.CASE_REF_START)
        n = start - 1
        for row in self._select("packs"):
            ref = row.get("case_ref") or ""
            if ref.startswith("c_") and ref[2:].isdigit() and int(ref[2:]) >= start:
                n = max(n, int(ref[2:]))
        return f"c_{n + 1:04d}"

    def insert_pack(self, row: dict[str, Any]) -> None:
        ref, pack = row["case_id"], row["pack"]
        cand = self.get_candidate(row["uarn"])
        if cand is None:
            raise ValueError(f"no candidate for {row['uarn']}")
        refs = [{"type": "voa_record", "uarn": row["uarn"], "role": "subject"}] + [
            {"type": "voa_record", "uarn": c["uarn"], "role": "comparable"}
            for c in row["comparables"]]
        self._insert("packs", {
            "pack_id": new_id(), "case_id": new_id(), "case_ref": ref,
            "candidate_id": cand["candidate_id"], "uarn": row["uarn"],
            "address": pack.get("address") or cand["address"],
            "current_rv": pack.get("current_rv", cand.get("rv") or 0),
            "proposed_rv": row["proposed_rv"], "comparables": row["comparables"],
            "calc": pack.get("calc", {}), "annual_saving": row["annual_saving"],
            "argument": row["argument"], "evidence_refs": refs,
            # The VOA download the candidate (and so the pack) was read from.
            "source_snapshot": row.get("source_snapshot") or cand.get("source_snapshot") or _snapshot(),
            "pdf_url": row.get("pdf_url"), "status": "built",
            "builder_version": row["version"], "pack": pack, "created_at": now_iso(),
        })

    def get_pack(self, case_id: str) -> dict[str, Any] | None:
        row = self._pack_row(case_id)
        if row is None:
            return None
        return {**row, "case_uuid": row["case_id"], "case_id": row["case_ref"],
                "version": row["builder_version"]}

    def set_pack_status(self, case_id: str, status: str) -> None:
        if status not in PACK_STATUSES:
            raise ValueError(f"unknown pack status {status!r}")
        keys = self._keys(case_id)
        if keys["pack_id"] is None:
            raise ValueError(f"no pack {case_id}")
        self._update("packs", {"status": status}, [("pack_id", "eq", keys["pack_id"])])

    def list_case_ids(self) -> list[str]:
        rows = self._select("packs", order=[("created_at", False)])
        return [r["case_ref"] for r in rows if r.get("case_ref")]

    def save_pdf(self, case_id: str, data: bytes) -> str:
        return self._put_file("packs", f"{case_id}.pdf", data, "application/pdf")

    # -- verdicts --------------------------------------------------------------
    def insert_verdict(self, row: dict[str, Any]) -> None:
        keys = self._keys(row["case_id"])
        if keys["case_id"] is None:
            raise ValueError(f"no pack {row['case_id']}")
        reasons = row["reasons"]
        self._insert("verdicts", {
            "verdict_id": new_id(), "case_id": keys["case_id"], "pack_id": keys["pack_id"],
            "verdict": row["verdict"], "reasons": reasons,
            "cited_records": sorted({r.get("voa_record") for r in reasons if r.get("voa_record")}),
            "advocate_version": row["version"], "created_at": now_iso(),
        })

    def latest_verdict(self, case_id: str) -> dict[str, Any] | None:
        verdicts = self.list_verdicts(case_id)
        return verdicts[-1] if verdicts else None

    def list_verdicts(self, case_id: str) -> list[dict[str, Any]]:
        """Every verdict for a pack, oldest first. reasons = [{reason, voa_record, kind}]."""
        keys = self._keys(case_id)
        if keys["case_id"] is None:
            return []
        rows = self._select("verdicts", [("case_id", "eq", keys["case_id"])],
                            [("created_at", False)])
        return [{**r, "case_id": case_id, "version": r["advocate_version"]} for r in rows]

    # -- cases -----------------------------------------------------------------
    def _case_view(self, row: dict[str, Any], ref: str) -> dict[str, Any]:
        return {**row, "case_uuid": row["case_id"], "case_id": ref,
                "owner_approved": bool(row["owner_approved"])}

    def insert_case(self, row: dict[str, Any]) -> None:
        ref = row["case_id"]
        keys = self._keys(ref)
        if keys["case_id"] is None:
            raise ValueError(f"no pack {ref}")
        if row.get("stage") and row["stage"] not in CASE_STAGES:
            raise ValueError(f"unknown case stage {row['stage']!r}")
        self._insert("cases", {
            "submission_mode": "mock", "history": [], "created_at": now_iso(),
            "updated_at": now_iso(), **row, **keys,
        })

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        keys = self._keys(case_id)
        if keys["case_id"] is None:
            return None
        row = self._one("cases", [("case_id", "eq", keys["case_id"])])
        return self._case_view(row, case_id) if row else None

    def update_case(self, case_id: str, changes: dict[str, Any]) -> None:
        unknown = changes.keys() - CASE_UPDATABLE
        if unknown:
            raise ValueError(f"cannot update cases columns {sorted(unknown)}")
        if "stage" in changes and changes["stage"] not in CASE_STAGES:
            raise ValueError(f"unknown case stage {changes['stage']!r}")
        keys = self._keys(case_id)
        if keys["case_id"] is None:
            raise ValueError(f"no case {case_id}")
        self._update("cases", {**changes, "updated_at": now_iso()},
                     [("case_id", "eq", keys["case_id"])])

    def list_cases(self, stages: tuple[str, ...]) -> list[dict[str, Any]]:
        rows = self._select("cases", [("stage", "in", list(stages))], [("created_at", False)])
        refs = self._refs_for({r["case_id"] for r in rows})
        return [self._case_view(r, refs[r["case_id"]]) for r in rows if r["case_id"] in refs]

    # -- events ----------------------------------------------------------------
    def add_event(self, agent: str, kind: str, message: str, case_id: str | None = None,
                  tokens: int = 0, ts: str | None = None, cost_usd: float | None = None,
                  idempotency_key: str | None = None, run_id: str | None = None,
                  payload: dict | None = None, candidate_id: str | None = None) -> None:
        keys = dict(self._keys(case_id))
        if candidate_id:
            keys["candidate_id"] = candidate_id
        payload = dict(payload or {})
        if case_id and keys["case_id"] is None:
            payload["case_ref"] = case_id      # no pack yet: keep the ref for the audit log
        self._insert("events", {
            "event_id": new_id(), "ts": ts or now_iso(), "agent": agent, "kind": kind,
            "message": message, "tokens": tokens, "cost_usd": cost_usd,
            "idempotency_key": idempotency_key, "run_id": run_id,
            "payload": payload, **keys,
        })

    def count_candidate_events(self, kind: str, candidate_id: str) -> int:
        return self._count("events", [("kind", "eq", kind), ("candidate_id", "eq", candidate_id)])

    def _event_view(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        refs = self._refs_for({r["case_id"] for r in rows if r.get("case_id")})
        return [{**r, "id": r["event_id"], "case_uuid": r.get("case_id"),
                 "case_id": refs.get(r.get("case_id"))} for r in rows]

    def count_events(self, kind: str, case_id: str) -> int:
        keys = self._keys(case_id)
        if keys["case_id"] is None:
            return 0
        return self._count("events", [("kind", "eq", kind), ("case_id", "eq", keys["case_id"])])

    def last_event(self, kind: str, case_id: str | None = None) -> dict[str, Any] | None:
        where: Where = [("kind", "eq", kind)]
        if case_id is not None:
            keys = self._keys(case_id)
            if keys["case_id"] is None:
                return None
            where.append(("case_id", "eq", keys["case_id"]))
        rows = self._select("events", where, [("ts", True)], limit=1)
        return self._event_view(rows)[0] if rows else None

    def events_since(self, kind: str, since_ts: str | None,
                     limit: int = 200) -> list[dict[str, Any]]:
        """Events of this kind at or after since_ts, oldest first (callers skip ids seen)."""
        where: Where = [("kind", "eq", kind)]
        if since_ts:
            where.append(("ts", "gte", since_ts))
        return self._event_view(self._select("events", where, [("ts", False)], limit))

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._select("events", order=[("ts", True)], limit=limit)
        return list(reversed(self._event_view(rows)))


def _snapshot() -> str:
    return os.getenv("SOURCE_SNAPSHOT") or "voa-2026-mock-hackney"


class SQLiteStore(BaseStore):
    def __init__(self, path: str | Path | None = None, pdf_dir: str | Path | None = None):
        super().__init__()
        self.path = Path(path or os.getenv("SQLITE_PATH") or ROOT / "out" / "overrated.db")
        self.pdf_dir = Path(pdf_dir or os.getenv("PDF_DIR") or ROOT / "out" / "pdfs")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma foreign_keys = on")
        self.db.executescript(contract.sqlite_ddl())
        # One connection shared by the watcher and the agents' worker threads.
        self.lock = threading.RLock()
        self.columns = {t: [c.name for c in tbl.columns] for t, tbl in contract.tables().items()}

    def _check(self, table: str, cols) -> None:
        # Table and column names are checked against the contract; values are always bound.
        bad = set(cols) - set(self.columns[table])
        if bad:
            raise ValueError(f"{table} has no columns {sorted(bad)}")

    def _encode(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        js, bools = contract.columns_of(table, "jsonb"), contract.columns_of(table, "boolean")
        return {k: json.dumps(v) if k in js and v is not None
                else int(v) if k in bools and v is not None else v for k, v in row.items()}

    def _decode(self, table: str, row: sqlite3.Row) -> dict[str, Any]:
        js, bools = contract.columns_of(table, "jsonb"), contract.columns_of(table, "boolean")
        out = dict(row)
        for k in js & out.keys():
            if isinstance(out[k], str):
                out[k] = json.loads(out[k])
        for k in bools & out.keys():
            if out[k] is not None:
                out[k] = bool(out[k])
        return out

    def _where(self, table: str, where: Where) -> tuple[str, list]:
        self._check(table, [c for c, _, _ in where])
        parts, params = [], []
        for col, op, val in where:
            if op == "eq":
                parts.append(f"{col} = ?")
                params.append(val)
            elif op == "gte":
                parts.append(f"{col} >= ?")
                params.append(val)
            elif op == "in":
                if not val:
                    parts.append("0")
                    continue
                parts.append(f"{col} in ({', '.join('?' for _ in val)})")
                params.extend(val)
            else:
                raise ValueError(f"unknown op {op}")
        return (" where " + " and ".join(parts)) if parts else "", params

    def _insert(self, table: str, row: dict[str, Any]) -> None:
        self._check(table, row)
        row = self._encode(table, row)
        cols = list(row)
        sql = f"insert into {table} ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})"
        with self.lock, self.db:
            self.db.execute(sql, [row[c] for c in cols])

    def _select(self, table: str, where: Where = (), order: Order = (),
                limit: int | None = None) -> list[dict[str, Any]]:
        clause, params = self._where(table, list(where))
        self._check(table, [c for c, _ in order])
        sql = f"select * from {table}{clause}"
        if order:
            sql += " order by " + ", ".join(f"{c} {'desc' if d else 'asc'}" for c, d in order)
        if limit:
            sql += " limit ?"
            params.append(limit)
        with self.lock:
            rows = self.db.execute(sql, params).fetchall()
        return [self._decode(table, r) for r in rows]

    def _update(self, table: str, changes: dict[str, Any], where: Where) -> None:
        self._check(table, changes)
        changes = self._encode(table, changes)
        clause, params = self._where(table, list(where))
        sets = ", ".join(f"{c} = ?" for c in changes)
        with self.lock, self.db:
            self.db.execute(f"update {table} set {sets}{clause}", [*changes.values(), *params])

    def _count(self, table: str, where: Where) -> int:
        clause, params = self._where(table, list(where))
        with self.lock:
            return self.db.execute(f"select count(*) from {table}{clause}", params).fetchone()[0]

    def _put_file(self, bucket: str, path: str, data: bytes, content_type: str) -> str:
        target = self.pdf_dir / path
        target.write_bytes(data)
        return target.as_uri()


def get_store() -> Store:
    backend = os.getenv("STORE_BACKEND") or "sqlite"
    if backend == "sqlite":
        return SQLiteStore()
    if backend == "supabase":
        from orchestrator.supabase_store import SupabaseStore
        return SupabaseStore()
    raise ValueError(f"unknown STORE_BACKEND {backend!r}")
