# scout agent

Owner: person 1. Lead agent (multiagent coordinator). Sweeps a borough street by street through the data service, writes `candidates`, checkpoints after each street, and fans out Case builders in batches of 10.

## Files

- `agent.md`: agent config (frontmatter) and system prompt (body), synced with `ant apply`
- `environment.yaml`: cloud sandbox, egress limited to the data service tunnel and Supabase
- `vault.yaml`: the `overrated` vault (container only)
- `add_credentials.py`: puts DATA_SERVICE_KEY and SUPABASE_SERVICE_ROLE_KEY in the vault (header-only, own host only)
- `run.py`: starts a session on a borough, streams it, budgets it, logs spend to `events`

## Where it runs

Person 2's `overrated` workspace (org Overrated), next to their Case builder (`agent_01Qr1incdn1w6ubyuzymtWU1`), which is on the Scout's roster. The ids are in `agents/scout/claude-lock.json`. The key and workspace id are `OVERRATED_API_KEY` / `OVERRATED_WORKSPACE_ID` in `.env` (from Person 2; never commit them). The first deployment in the `Claude Code` workspace (root `claude-lock.json`) still works with `--workspace legacy`.

## Set up (once)

From the repo root:

```
export ANTHROPIC_API_KEY=$(grep ^OVERRATED_API_KEY= .env | cut -d= -f2-) ANTHROPIC_WORKSPACE_ID=$(grep ^OVERRATED_WORKSPACE_ID= .env | cut -d= -f2-)
ant apply --dry-run -v --lock-file agents/scout/claude-lock.json agents/scout/agent.md agents/scout/environment.yaml agents/scout/vault.yaml
ant apply --yes --lock-file agents/scout/claude-lock.json agents/scout/agent.md agents/scout/environment.yaml agents/scout/vault.yaml
.venv/bin/python agents/scout/add_credentials.py      # data service + Supabase keys into the vault
```

Commit `agents/scout/claude-lock.json`.

## Run

```
.venv/bin/python agents/scout/run.py --ba-code 5360 --name Hackney --handoff-limit 2   # test: 2 builder threads
.venv/bin/python agents/scout/run.py --ba-code 5360 --name Hackney                     # full sweep
```

The Case builder runs as threads inside the Scout's session; its custom tools (data tools, eligible_comparables, submit_pack, no_case) are answered by `run.py` with Person 2's `orchestrator.scout_tools.ScoutToolRouter` (real data service via `DATA_BACKEND=http`, Supabase via `STORE_BACKEND=supabase`). New packs then go to Person 2's orchestrator, which starts the VOA advocate.

Budget: SESSION_TOKEN_BUDGET priced at Opus 5.5's output rate ($20 per million), so 200,000 tokens is a $4.00 cap for the whole session, builder threads included. Override with SESSION_BUDGET_CENTS.

## Scoring (defaults; the live playbook overrides)

- tone is per segment (street + postcode sector + VOA scheme), from the data service
- scout-v4: a shop must also be above its 4 nearest same-use neighbours on the street (`local_z >= 2`); shops that match their neighbours (`matches_neighbours`) are dropped, e.g. 225-227 Old Street, priced like 221-233
- keep: z_score >= 2 in a segment with 5+ shops, est_saving >= £1,000 and local_z >= 2; or just over £51k / £500k with z_score >= 1 and local_z >= 1
- writes Person 3's schema: run.py creates or resumes the `scout_runs` row and passes run_id; candidates are unique per (run_id, uarn, rating_list); events carry run_id and an idempotency key

History on Hackney (2,365 shops):
- scout-v1, tone per street: 101 candidates, 82 above tone (~£750k), of which 74 were whole VOA schemes priced higher
- data service by segment: 2,336 shops in 95 segments, 34 above tone (~£124k), 28 of them in same-price runs inside one scheme
