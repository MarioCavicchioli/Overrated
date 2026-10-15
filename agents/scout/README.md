# scout agent

Owner: person 1. Lead agent (multiagent coordinator). Sweeps a borough street by street through the data service, writes `candidates`, checkpoints after each street, and fans out Case builders in batches of 10.

## Files

- `agent.md`: agent config (frontmatter) and system prompt (body), synced with `ant apply`
- `environment.yaml`: cloud sandbox, egress limited to the data service tunnel and Supabase
- `vault.yaml`: the `overrated` vault (container only)
- `add_credentials.py`: puts DATA_SERVICE_KEY and SUPABASE_SERVICE_ROLE_KEY in the vault (header-only, own host only)
- `run.py`: starts a session on a borough, streams it, budgets it, logs spend to `events`

## Set up (once)

1. Sign in once with `ant auth login` (team org, Claude Code workspace). The scripts and `ant` use that profile; leave ANTHROPIC_API_KEY unset.
2. From the repo root:

```
ant apply --dry-run -v agents/scout/agent.md agents/scout/environment.yaml agents/scout/vault.yaml
ant apply agents/scout/agent.md agents/scout/environment.yaml agents/scout/vault.yaml
.venv/bin/python agents/scout/add_credentials.py
```

3. Commit `claude-lock.json`. Re-run `add_credentials.py` after adding Supabase to .env.

Deployed 15 Oct in team63728 / Claude Code: agent, environment and vault IDs are in `claude-lock.json`.

When Person 2's Case builder exists, add `../builder/agent.md` to the roster in `agent.md` and run `ant apply` again.

## Run

```
.venv/bin/python agents/scout/run.py --ba-code 5360 --name Hackney
```

Budget: SESSION_TOKEN_BUDGET priced at Opus 5.5's output rate ($20 per million), so 200,000 tokens is a $4.00 cap. Override with SESSION_BUDGET_CENTS.

## Scoring (defaults; the live playbook overrides)

- tone is per segment (street + postcode sector + VOA scheme), from the data service
- keep: z_score >= 2 in a segment with 5+ shops and est_saving >= £1,000; or just over £51k / £500k with z_score >= 1
- `segment_cluster` flag: 3+ shops in the segment share the same high Zone A price (usually neighbouring addresses), so probably priced deliberately; kept but score halved
- writes Person 3's schema: run.py creates or resumes the `scout_runs` row and passes run_id; candidates are unique per (run_id, uarn, rating_list); events carry run_id and an idempotency key

History on Hackney (2,365 shops):
- scout-v1, tone per street: 101 candidates, 82 above tone (~£750k), of which 74 were whole VOA schemes priced higher
- data service by segment: 2,336 shops in 95 segments, 34 above tone (~£124k), 28 of them in same-price runs inside one scheme
