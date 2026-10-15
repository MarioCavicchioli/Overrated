"""Reads contracts/schema.sql (generated from the live database).

Gives each table's columns and types, so the stores know which columns hold
JSON or booleans, and builds the local SQLite schema from the same file, so the
local copy can't drift from the contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "contracts" / "schema.sql"
TYPES = ("double precision", "timestamptz", "boolean", "integer", "bigint", "numeric",
         "jsonb", "text", "uuid", "date")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    rest: str          # constraints and default, as written


def _parse_column(line: str) -> Column | None:
    line = line.split("--", 1)[0].strip().rstrip(",")
    if not line:
        return None
    name, after = line.split(None, 1)
    for t in TYPES:
        if after.startswith(t):
            return Column(name, t, after[len(t):].strip())
    raise ValueError(f"unknown column type in schema.sql: {line!r}")


@lru_cache(maxsize=None)
def tables(path: Path = SCHEMA) -> dict[str, list[Column]]:
    sql = path.read_text(encoding="utf-8")
    out: dict[str, list[Column]] = {}
    for name, body in re.findall(r"create table if not exists (\w+) \((.*?)\n\);", sql, re.S):
        out[name] = [c for c in map(_parse_column, body.splitlines()) if c]
    # PENDING columns (agreed, not yet live) are part of the contract too.
    for table, col, typ in re.findall(
            r"^alter table (\w+) add column if not exists (\w+) ([\w ]+?);", sql, re.M):
        out[table].append(Column(col, typ.strip(), ""))
    return out


def columns_of(table: str, kind: str) -> set[str]:
    return {c.name for c in tables()[table] if c.type == kind}


_SQLITE_TYPE = {"uuid": "text", "jsonb": "text", "timestamptz": "text", "date": "text",
                "double precision": "real", "boolean": "integer", "bigint": "integer"}
_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"


def sqlite_ddl() -> str:
    """The contract as SQLite DDL. UUIDs are made in Python, so gen_random_uuid() drops."""
    stmts = []
    for table, cols in tables().items():
        lines = []
        for c in cols:
            rest = (c.rest.replace("default gen_random_uuid()", "")
                    .replace("default now()", f"default {_NOW}")
                    .replace("default true", "default 1").replace("default false", "default 0"))
            lines.append(f"  {c.name} {_SQLITE_TYPE.get(c.type, c.type)} {' '.join(rest.split())}"
                         .rstrip())
        stmts.append(f"create table if not exists {table} (\n" + ",\n".join(lines) + "\n);")
    return "\n\n".join(stmts) + "\n"
