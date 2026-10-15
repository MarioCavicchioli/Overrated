"""Supabase backend for the Store interface (STORE_BACKEND=supabase).

Uses the service role key, which only the orchestrator holds (CLAUDE.md). Pack
PDFs go to the private Storage bucket "packs"; packs.pdf_url holds the object
path ("packs/c_0001.pdf"), and readers create a short-lived signed URL with
signed_pdf_url(). The bucket must stay private: the PDFs contain VOA data,
which we may not republish.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from postgrest.types import CountMethod
from supabase import Client, create_client

from orchestrator.config import supabase_url
from orchestrator.store import CANDIDATE_STATUSES, CASE_STAGES

PDF_BUCKET = "packs"


class SupabaseStore:
    def __init__(self, client: Client | None = None):
        if client is None:
            url, key = supabase_url(), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            if not url or not key:
                raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
            client = create_client(url, key)
        self.sb = client

    def _t(self, table: str):
        return self.sb.table(table)

    @staticmethod
    def _first(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        return rows[0] if rows else None

    # -- candidates ------------------------------------------------------
    def upsert_candidate(self, row: dict[str, Any]) -> None:
        self._t("candidates").upsert(row, on_conflict="uarn").execute()

    def get_candidate(self, uarn: str) -> dict[str, Any] | None:
        return self._first(self._t("candidates").select("*").eq("uarn", uarn)
                           .limit(1).execute().data)

    def set_candidate_status(self, uarn: str, status: str) -> None:
        if status not in CANDIDATE_STATUSES:
            raise ValueError(f"unknown candidate status {status!r}")
        self._t("candidates").update({"status": status}).eq("uarn", uarn).execute()

    # -- packs -----------------------------------------------------------
    def next_case_id(self) -> str:
        # case_id is zero-padded (c_0001), so text order is numeric order up to c_9999.
        last = self._first(self._t("packs").select("case_id").order("case_id", desc=True)
                           .limit(1).execute().data)
        n = int(last["case_id"][2:]) if last else 0
        return f"c_{n + 1:04d}"

    def insert_pack(self, row: dict[str, Any]) -> None:
        self._t("packs").insert(row).execute()

    def get_pack(self, case_id: str) -> dict[str, Any] | None:
        return self._first(self._t("packs").select("*").eq("case_id", case_id)
                           .limit(1).execute().data)

    def list_case_ids(self) -> list[str]:
        rows = self._t("packs").select("case_id").order("created_at").order("case_id") \
            .execute().data
        return [r["case_id"] for r in rows]

    def save_pdf(self, case_id: str, data: bytes) -> str:
        path = f"{case_id}.pdf"
        self.sb.storage.from_(PDF_BUCKET).upload(
            path, data, {"content-type": "application/pdf", "upsert": "true"})
        return f"{PDF_BUCKET}/{path}"

    def signed_pdf_url(self, pdf_url: str, expires_s: int = 600) -> str:
        bucket, _, path = pdf_url.partition("/")
        res = self.sb.storage.from_(bucket).create_signed_url(path, expires_s)
        return res.get("signedURL") or res.get("signedUrl")

    # -- verdicts --------------------------------------------------------
    def insert_verdict(self, row: dict[str, Any]) -> None:
        self._t("verdicts").insert(row).execute()

    def latest_verdict(self, case_id: str) -> dict[str, Any] | None:
        return self._first(self._t("verdicts").select("*").eq("case_id", case_id)
                           .order("id", desc=True).limit(1).execute().data)

    # -- cases -----------------------------------------------------------
    CASE_COLUMNS = {"stage", "next_deadline", "owner_approved", "history"}

    def insert_case(self, row: dict[str, Any]) -> None:
        self._t("cases").insert(row).execute()

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        return self._first(self._t("cases").select("*").eq("case_id", case_id)
                           .limit(1).execute().data)

    def update_case(self, case_id: str, changes: dict[str, Any]) -> None:
        unknown = changes.keys() - self.CASE_COLUMNS
        if unknown:
            raise ValueError(f"cannot update cases columns {sorted(unknown)}")
        if "stage" in changes and changes["stage"] not in CASE_STAGES:
            raise ValueError(f"unknown case stage {changes['stage']!r}")
        now = datetime.now(timezone.utc).isoformat()
        self._t("cases").update({**changes, "updated_at": now}) \
            .eq("case_id", case_id).execute()

    def list_cases(self, stages: tuple[str, ...]) -> list[dict[str, Any]]:
        return self._t("cases").select("*").in_("stage", list(stages)) \
            .order("case_id").execute().data

    # -- events ----------------------------------------------------------
    def add_event(self, agent: str, kind: str, message: str,
                  case_id: str | None = None, tokens: int = 0,
                  ts: str | None = None) -> None:
        row = {"agent": agent, "kind": kind, "message": message,
               "case_id": case_id, "tokens": tokens}
        if ts:
            row["ts"] = ts
        self._t("events").insert(row).execute()

    def count_events(self, kind: str, case_id: str) -> int:
        res = self._t("events").select("id", count=CountMethod.exact) \
            .eq("kind", kind).eq("case_id", case_id).execute()
        return res.count or 0

    def last_event(self, kind: str, case_id: str | None = None) -> dict[str, Any] | None:
        q = self._t("events").select("*").eq("kind", kind)
        if case_id is not None:
            q = q.eq("case_id", case_id)
        return self._first(q.order("id", desc=True).limit(1).execute().data)

    def events_after(self, kind: str, after_id: int, limit: int = 200) -> list[dict[str, Any]]:
        return self._t("events").select("*").eq("kind", kind).gt("id", after_id) \
            .order("id").limit(limit).execute().data

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._t("events").select("*").order("id", desc=True).limit(limit).execute().data
        return list(reversed(rows))
