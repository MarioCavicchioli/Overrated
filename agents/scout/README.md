# scout agent

Owner: person 1. Lead agent (multiagent coordinator). Sweeps a borough street by street through the data service, writes `candidates`, checkpoints after each street, and fans out Case builders in batches of 10.

## Files

- `agent.md`: agent config (frontmatter) and system prompt (body), synced with `ant apply`
- `environment.yaml`: cloud sandbox, egress limited to the data service tunnel and Supabase
- `vault.yaml`: the `overrated` vault (container only)
- `add_credentials.py`: puts DATA_SERVICE_KEY and SUPABASE_SERVICE_ROLE_KEY in the vault (header-only, own host only)
- `run.py`: starts a session on a borough, streams it, budgets it, logs spend to `events`

## Set up (once)

1. Fill `YOUR_PROJECT` in `environment.yaml` from SUPABASE_URL.
2. From the repo root:

```
ant apply --dry-run -v agents/scout/agent.md agents/scout/environment.yaml agents/scout/vault.yaml
ant apply agents/scout/agent.md agents/scout/environment.yaml agents/scout/vault.yaml
.venv/bin/python agents/scout/add_credentials.py
```

3. Commit `claude-lock.json`.

When Person 2's Case builder exists, add `../builder/agent.md` to the roster in `agent.md` and run `ant apply` again.

## Run

```
.venv/bin/python agents/scout/run.py --ba-code 5360 --name Hackney
```

Budget: SESSION_TOKEN_BUDGET priced at Opus 5.5's output rate ($20 per million), so 200,000 tokens is a $4.00 cap. Override with SESSION_BUDGET_CENTS.

## Scoring (defaults; the live playbook overrides)

- keep: z_score >= 2 on a street with 5+ shops and est_saving >= £1,000, or just over £51k / £500k
- `segment_cluster` flag: 3+ shops on the street share the same high Zone A price, so probably a separate VOA segment; kept but ranked lower
- Hackney dry run of these rules on the data service: 2,365 shops on 100 streets, 101 candidates (31 clean, 70 segment_cluster) on 21 streets
