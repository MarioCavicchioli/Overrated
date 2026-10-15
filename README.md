# Overrated

AI agents that find businesses overpaying business rates, prove it, and run the case until the bill comes down. Built at the Accel AI Innovate hackathon, 15 Oct 2026.

## Layout

```
contracts/      shared contracts everyone builds against
  pack.schema.json   evidence pack format (person 2)
  schema.sql         Supabase tables (person 3)
data/           data service: DuckDB + Parquet over VOA files, exposes the tools (person 1)
agents/
  scout/        lead agent, fans out case builders (person 1)
  builder/      case builder, one pack per candidate (person 2)
  advocate/     argues the VOA side, own session per pack (person 2)
  caseworker/   short sessions woken by replies/deadlines (person 3)
  playbook/     nightly playbook keeper (person 3)
  eval/         backtest, pack checks, advocate accuracy (person 1)
orchestrator/   FastAPI: starts sessions on new rows, budgets, owner approval gate (person 2)
```

## Rules

- VOA data is for rating purposes only. Never commit raw VOA files or republish them (`data/raw/` and `*.parquet` are gitignored).
- No real filings. The VOA API is mocked.
- Every session gets a token budget.
- Never invent a comparable. Every comparable comes from the data service.

## Workflow

1. `claude auth login --console`, same Console organisation for everyone.
2. Work on your own branch (`p1-data`, `p2-cases`, `p3-app`), merge to `main` at each milestone.
3. Contracts change only after telling the team.

## Setup

```bash
cp .env.example .env   # fill in keys, never commit .env
```
