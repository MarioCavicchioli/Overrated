"""Generate contracts/schema.sql from the live Supabase database. Read-only.

    python -m contracts.schema_from_live            # writes contracts/schema.sql
    python -m contracts.schema_from_live --check    # exit 1 if schema.sql differs from live

information_schema isn't exposed through PostgREST (only public and
graphql_public are), so this reads PostgREST's OpenAPI description of the
public schema, which comes from the same catalog. It shows column types, NOT
NULL, defaults, primary keys and foreign keys. It cannot show check or unique
constraints, indexes, RLS policies or the realtime publication; the header of
the generated file says so.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx

from orchestrator.config import load_env, supabase_url

OUT = Path(__file__).with_name("schema.sql")
TABLE_ORDER = ["scout_runs", "candidates", "packs", "verdicts", "cases", "events",
               "eval_runs", "playbook"]
TYPE = {"int32": "integer", "int64": "bigint", "timestamp with time zone": "timestamptz"}

# Columns agreed with person 3 but not yet in the live database. Kept here so the
# contract and the local SQLite copy have them; remove each once it is live.
PENDING = [
    ("packs", "case_ref", "text", "readable case id, e.g. c_0001 (pack.schema.json case_id)"),
    ("packs", "pack", "jsonb", "the full pack, validated against pack.schema.json"),
]


def fetch_spec() -> dict:
    load_env()
    url, key = supabase_url(), os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = httpx.get(f"{url}/rest/v1/", headers={"apikey": key, "Authorization": f"Bearer {key}"},
                  timeout=20)
    r.raise_for_status()
    return r.json()


def _default(value, sql_type: str) -> str:
    text = str(value)
    if "(" in text or sql_type not in ("text",):
        return {"True": "true", "False": "false"}.get(text, text)
    return "'" + text.replace("'", "''") + "'"


def render(spec: dict) -> str:
    defs = spec["definitions"]
    referenced: set[tuple[str, str]] = set()
    for d in defs.values():
        for p in d["properties"].values():
            m = re.search(r"<fk table='(\w+)' column='(\w+)'", p.get("description") or "")
            if m:
                referenced.add((m.group(1), m.group(2)))

    lines = [
        "-- Overrated: Supabase schema, GENERATED from the live database by",
        "-- contracts/schema_from_live.py (read-only). The live database is the source of truth;",
        "-- person 3 owns it. Do not edit by hand: change the database, then regenerate.",
        "--",
        "-- Read through PostgREST's OpenAPI description (information_schema isn't exposed).",
        "-- Shown: types, NOT NULL, defaults, primary and foreign keys.",
        "-- Not visible from there, so NOT in this file: check constraints (allowed status",
        "-- and stage values), unique constraints other than those implied by foreign keys,",
        "-- indexes, RLS policies, and the realtime publication.",
        "",
    ]
    names = [t for t in TABLE_ORDER if t in defs] + sorted(set(defs) - set(TABLE_ORDER))
    for table in names:
        props, required = defs[table]["properties"], set(defs[table].get("required", []))
        cols: list[tuple[str, str]] = []
        for col, p in props.items():
            sql_type = TYPE.get(p.get("format"), p.get("format") or p.get("type"))
            parts, note = [f"  {col:<24}", sql_type], ""
            if col in required:
                parts.append("not null")
            if "default" in p:
                parts.append(f"default {_default(p['default'], sql_type)}")
            desc = p.get("description") or ""
            if "<pk/>" in desc:
                parts.append("primary key")
            elif (table, col) in referenced:
                parts.append("unique")
                note = "  -- implied: referenced by a foreign key"
            m = re.search(r"<fk table='(\w+)' column='(\w+)'", desc)
            if m:
                parts.append(f"references {m.group(1)}({m.group(2)})")
            cols.append((" ".join(parts), note))
        lines.append(f"create table if not exists {table} (")
        for i, (sql, note) in enumerate(cols):
            lines.append(sql + ("," if i < len(cols) - 1 else "") + note)
        lines.append(");")
        lines.append("")

    lines.append("-- PENDING: agreed, not yet live (person 3 is adding them).")
    for table, col, sql_type, note in PENDING:
        live = col in defs.get(table, {}).get("properties", {})
        prefix = "-- now live, drop from PENDING: " if live else ""
        lines.append(f"{prefix}alter table {table} add column if not exists {col} {sql_type};"
                     f"  -- {note}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    text = render(fetch_spec())
    if "--check" in argv:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("schema.sql matches the live database" if same else
              "schema.sql differs from the live database: regenerate it")
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
