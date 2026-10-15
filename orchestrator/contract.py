"""Reads contracts/schema.sql (Person 3's DDL for the live Supabase database).

Gives each table's columns and types, so the stores know which columns hold
JSON or booleans, and builds the local SQLite schema from the same file, so the
local copy can't drift from the contract. Simple value checks (status in (...),
numeric ranges) are kept in SQLite, so tests reject values the live database
would reject. Postgres-only parts (casts, functions, triggers, RLS, indexes,
the realtime publication) are dropped from the SQLite copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "contracts" / "schema.sql"

# Columns agreed with Person 3 but not yet in the live database or schema.sql,
# as (table, column, type, constraints). Remove each one once it is in schema.sql.
PENDING: list[tuple[str, str, str, str]] = []

_TYPES = ("double precision", "timestamp with time zone", "timestamptz", "boolean", "integer",
          "bigint", "numeric", "jsonb", "text", "uuid", "date", "int", "bool")
_NORM = {"timestamp with time zone": "timestamptz", "int": "integer", "bool": "boolean"}
_TABLE_LEVEL = ("primary", "unique", "check", "constraint", "foreign", "exclude")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    rest: str          # constraints and default, Postgres syntax


@dataclass(frozen=True)
class Table:
    columns: tuple[Column, ...]
    constraints: tuple[str, ...]   # table-level: unique (...), check (...), primary key (...)


def _split_top(body: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [" ".join(p.split()) for p in parts if p.strip()]


@lru_cache(maxsize=None)
def tables(path: Path = SCHEMA) -> dict[str, Table]:
    sql = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
    out: dict[str, Table] = {}
    for name, body in re.findall(
            r"create table if not exists (?:public\.)?(\w+)\s*\((.*?)\n\s*\);", sql, re.S | re.I):
        cols, cons = [], []
        for part in _split_top(body):
            first = part.split()[0].lower()
            if first in _TABLE_LEVEL:
                cons.append(part)
                continue
            col, after = part.split(None, 1)
            low = after.lower()
            typ = next((t for t in _TYPES if low.startswith(t)), None)
            if typ is None:
                raise ValueError(f"unknown column type in schema.sql: {part!r}")
            rest = after[len(typ):]
            rest = re.sub(r"^\(\d+(,\s*\d+)?\)", "", rest.strip())   # numeric(14,2) -> numeric
            cols.append(Column(col, _NORM.get(typ, typ), rest.strip()))
        out[name] = Table(tuple(cols), tuple(cons))
    for table, col, typ, rest in PENDING:
        t = out[table]
        if col not in {c.name for c in t.columns}:
            out[table] = Table(t.columns + (Column(col, typ, rest),), t.constraints)
    return out


def columns_of(table: str, kind: str) -> set[str]:
    return {c.name for c in tables()[table].columns if c.type == kind}


# -- SQLite copy ---------------------------------------------------------------

_SQLITE_TYPE = {"uuid": "text", "jsonb": "text", "timestamptz": "text", "date": "text",
                "double precision": "real", "boolean": "integer", "bigint": "integer"}
_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"


def _sqlite_ok(check: str) -> bool:
    """Keep checks SQLite can run as written: no casts, functions or regexes."""
    return "::" not in check and "~" not in check and not re.search(r"\w\(", check[6:])


def _checks(text: str) -> tuple[str, list[str]]:
    """Pull 'check (...)' clauses (balanced parens) out of a column definition."""
    found, out, i = [], "", 0
    while i < len(text):
        m = re.compile(r"check\s*\(", re.I).match(text, i)
        if not m:
            out += text[i]
            i += 1
            continue
        depth, j = 0, m.end() - 1
        while j < len(text):
            depth += text[j] == "("
            depth -= text[j] == ")"
            j += 1
            if depth == 0:
                break
        found.append(text[i:j])
        i = j
    return " ".join(out.split()), found


def _sqlite_rest(rest: str) -> str:
    rest, checks = _checks(rest)
    rest = re.sub(r"default\s+gen_random_uuid\(\)", "", rest, flags=re.I)
    rest = re.sub(r"default\s+now\(\)", f"default {_NOW}", rest, flags=re.I)
    rest = re.sub(r"('[^']*')::\w+", r"\1", rest)                    # '{}'::jsonb -> '{}'
    rest = re.sub(r"default\s+true\b", "default 1", rest, flags=re.I)
    rest = re.sub(r"default\s+false\b", "default 0", rest, flags=re.I)
    rest = re.sub(r"references\s+public\.", "references ", rest, flags=re.I)
    rest = re.sub(r"on (delete|update) (cascade|restrict|set null|no action)", "", rest,
                  flags=re.I)
    rest += "".join(" " + c for c in checks if _sqlite_ok(c))
    return " ".join(rest.split())


def sqlite_ddl() -> str:
    """The contract as SQLite DDL. UUIDs are made in Python, so gen_random_uuid() drops."""
    stmts = []
    for name, table in tables().items():
        lines = [f"  {c.name} {_SQLITE_TYPE.get(c.type, c.type)} {_sqlite_rest(c.rest)}".rstrip()
                 for c in table.columns]
        for con in table.constraints:
            low = con.lower()
            if low.startswith(("unique", "primary key")) or (
                    low.startswith("check") and _sqlite_ok(con)):
                lines.append(f"  {con}")
        stmts.append(f"create table if not exists {name} (\n" + ",\n".join(lines) + "\n);")
    return "\n\n".join(stmts) + "\n"
