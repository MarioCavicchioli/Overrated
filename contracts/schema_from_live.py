"""Check contracts/schema.sql against the live Supabase database. Read-only.

    python -m contracts.schema_from_live          # exit 1 if any column or type differs

contracts/schema.sql is Person 3's DDL for the live database. This never writes
it; it reports drift between the file and the live columns and types.

information_schema isn't exposed through PostgREST (only public and
graphql_public are), so the live side comes from PostgREST's OpenAPI
description of the public schema. That shows columns, types, NOT NULL,
defaults and keys, but not check or unique constraints, indexes, RLS or the
realtime publication, so those aren't compared.
"""

from __future__ import annotations

import os
import sys

import httpx

from orchestrator import contract
from orchestrator.config import load_env, supabase_url

LIVE_TYPE = {"int32": "integer", "int64": "bigint", "timestamp with time zone": "timestamptz"}


def fetch_spec() -> dict:
    load_env()
    url, key = supabase_url(), os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = httpx.get(f"{url}/rest/v1/", headers={"apikey": key, "Authorization": f"Bearer {key}"},
                  timeout=20)
    r.raise_for_status()
    return r.json()


def live_columns(spec: dict) -> dict[str, dict[str, str]]:
    return {t: {c: LIVE_TYPE.get(p.get("format"), p.get("format") or p.get("type"))
                for c, p in d["properties"].items()}
            for t, d in spec["definitions"].items()}


def differences(spec: dict) -> tuple[list[str], list[str]]:
    """(drift, pending): drift = file vs live; pending = agreed columns not yet live."""
    live = live_columns(spec)
    pending_cols = {(t, c) for t, c, _, _ in contract.PENDING}
    file = {t: {c.name: c.type for c in tbl.columns if (t, c.name) not in pending_cols}
            for t, tbl in contract.tables().items()}
    drift, pending = [], []
    for t in sorted(set(live) | set(file)):
        if t not in file:
            drift.append(f"table {t}: live, missing from schema.sql")
        elif t not in live:
            drift.append(f"table {t}: in schema.sql, not live")
        else:
            for c in sorted(set(live[t]) | set(file[t])):
                a, b = file[t].get(c), live[t].get(c)
                if a is None:
                    drift.append(f"{t}.{c}: live ({b}), missing from schema.sql")
                elif b is None:
                    drift.append(f"{t}.{c}: in schema.sql ({a}), not live")
                elif a != b:
                    drift.append(f"{t}.{c}: schema.sql {a}, live {b}")
    for t, c, _, _ in contract.PENDING:
        state = "now live: add it to schema.sql and drop it from PENDING" \
            if c in live.get(t, {}) else "not live yet"
        pending.append(f"{t}.{c}: {state}")
    return drift, pending


def main() -> int:
    drift, pending = differences(fetch_spec())
    for line in drift:
        print("DRIFT  ", line)
    for line in pending:
        print("PENDING", line)
    print("schema.sql matches the live database" if not drift else f"{len(drift)} difference(s)")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
