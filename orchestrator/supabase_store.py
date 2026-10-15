"""Supabase backend for the Store interface (STORE_BACKEND=supabase).

Uses the service role key, which only the orchestrator holds (CLAUDE.md). The
mapping between our readable case refs and the live UUID keys lives in
BaseStore; this file only talks to PostgREST and Storage.

Pack PDFs go to the private Storage bucket "packs"; packs.pdf_url holds the
object path ("packs/c_0001.pdf"), and readers create a short-lived signed URL
with signed_pdf_url(). The bucket must stay private: the PDFs contain VOA data,
which we may not republish.
"""

from __future__ import annotations

import os
from typing import Any

from postgrest.types import CountMethod
from supabase import Client, create_client

from orchestrator.config import supabase_url
from orchestrator.store import BaseStore, Order, Where

PDF_BUCKET = "packs"


class SupabaseStore(BaseStore):
    CASE_REF_START = 101   # c_0001-c_0008 are Person 1's app's refs

    def __init__(self, client: Client | None = None):
        super().__init__()
        if client is None:
            url, key = supabase_url(), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            if not url or not key:
                raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
            client = create_client(url, key)
        self.sb = client

    @staticmethod
    def _filter(q, where: Where):
        for col, op, val in where:
            if op == "eq":
                q = q.eq(col, val)
            elif op == "gte":
                q = q.gte(col, val)
            elif op == "in":
                q = q.in_(col, list(val))
            else:
                raise ValueError(f"unknown op {op}")
        return q

    def _insert(self, table: str, row: dict[str, Any]) -> None:
        self.sb.table(table).insert(row).execute()

    def _select(self, table: str, where: Where = (), order: Order = (),
                limit: int | None = None) -> list[dict[str, Any]]:
        if any(op == "in" and not val for _, op, val in where):
            return []
        q = self._filter(self.sb.table(table).select("*"), where)
        for col, desc in order:
            q = q.order(col, desc=desc)
        if limit:
            q = q.limit(limit)
        return q.execute().data

    def _update(self, table: str, changes: dict[str, Any], where: Where) -> None:
        self._filter(self.sb.table(table).update(changes), where).execute()

    def _count(self, table: str, where: Where) -> int:
        q = self._filter(self.sb.table(table).select("*", count=CountMethod.exact, head=True),
                         where)
        return q.execute().count or 0

    def _put_file(self, bucket: str, path: str, data: bytes, content_type: str) -> str:
        self.sb.storage.from_(bucket).upload(
            path, data, {"content-type": content_type, "upsert": "true"})
        return f"{bucket}/{path}"

    def signed_pdf_url(self, pdf_url: str, expires_s: int = 600) -> str:
        bucket, _, path = pdf_url.partition("/")
        res = self.sb.storage.from_(bucket).create_signed_url(path, expires_s)
        return res.get("signedURL") or res.get("signedUrl")
