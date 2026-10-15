# Overrated

Hackathon project for Accel AI Innovate London (15 Oct 2026), built on Claude Managed Agents, Supabase and Lovable.
Team doc with the pitch, architecture and each person's prompt: https://claude.ai/code/artifact/78d45d48-8230-4835-9b17-bd9ff8900e62

## What we are building

Every shop, café and office in England pays business rates. The bill is its rateable value (the VOA's estimate of its yearly rent) times a multiplier. The VOA sets values for about 2 million properties at once, so some are too high.

Overrated finds shops valued higher than similar shops on the same street, builds the evidence, files a Check and Challenge with the VOA as the owner's agent, and runs the case for up to 18 months. If the value drops, the council refunds the overpayment and we take a share. If not, the owner pays nothing.

The customer types their business address. Seconds later they see their street on a map, their price per m2 against similar shops, and the likely saving. They tap "Challenge it", approve each filing, and follow the case like a parcel tracker.

## How shops are valued

- Zoning: Zone A is the first 6.1 m from the shop window, at a full price per m2. The next strips count at a half, a quarter and an eighth.
- Similar shops on the same street segment should have a similar Zone A price (the "tone of the list").
- Flags: a Zone A price above the street's normal spread; a floor area far from the EPC floor area (different measuring bases, so only big gaps); a value just over £51,000 or £500,000, where a higher multiplier applies to the whole value.
- 2026 multipliers: 38.2p (retail, hospitality and leisure under £51,000), 43.2p (other under £51,000), 43p (retail, hospitality and leisure £51,000 to £499,999), 48p (other £51,000 to £499,999), 50.8p (£500,000 and over).
- Saving = drop in value x multiplier x years left on the 2026 list (it runs to 31 March 2029), before reliefs.

## Architecture

Six hosted agents on Claude Managed Agents:

1. Scout (lead, multiagent mode on): scores every shop in the target area, writes candidates, fans out Case builders in workflow runs (batches of 10), saves a checkpoint after each street.
2. Case builder (runs inside the Scout's workflow runs): one evidence pack per candidate.
3. VOA advocate (its own session per pack, never a subagent): sees only the pack and the data service, argues the VOA's side, returns survive or kill with reasons.
4. Caseworker (short sessions woken by a VOA reply, a deadline or the daily schedule): reads the case row, decides the next step, drafts the reply.
5. Playbook keeper (nightly): proposes the next playbook version; it goes live only if the eval score on held-out streets holds.
6. Eval agent: backtest on April 2023 values (lift at 50 against random shops on the same streets), pack checks in code, advocate accuracy on decided Valuation Tribunal cases.

Only the Scout calls other agents directly. Everything else hands off through a new database row, which the orchestrator turns into a session.

- Data service: DuckDB and Parquet over the VOA 2026 list and summary valuations, plus the 2023 list with historic entries. Tools: search_properties, get_valuation, comparables, street_tone, historic_values (eval only). Needs a public URL (Railway, Render or ngrok) so hosted agents can call it.
- Orchestrator: FastAPI. Starts sessions when new rows land, runs schedules, holds the owner approval gate.
- Database: Supabase (Postgres, Realtime, Storage for pack PDFs).
- App: built in Lovable on the same Supabase project: address search, street map, saving, "Challenge it", case tracker, live agent feed, eval chart.

## Repo layout

- /contracts: schema.sql, pack.schema.json (agreed in the first 30 minutes; tell the team before changing them)
- /data: data service; raw VOA files go in data/raw and are never committed
- /agents/scout, /agents/builder, /agents/advocate, /agents/caseworker, /agents/playbook, /agents/eval
- /orchestrator

## Who owns what

- Person 1 (Illia), data and proof, branch p1-data: data service, Scout, eval agent
- Person 2, cases, branch p2-cases: orchestrator, Case builder, VOA advocate, pack format
- Person 3, long run and app, branch p3-app: Supabase schema, Caseworker, Playbook keeper, Lovable app

Work on your own branch and merge at milestones.

## Contracts

Tables (contracts/schema.sql; created_at/updated_at and identity ids omitted):

- candidates: uarn (PK), address, postcode, street_key, rv, zone_a_price, street_median, z_score, flags, est_saving, status (new | building | packed | killed | survived)
- packs: case_id (PK), uarn, pack (full pack jsonb, validated against pack.schema.json), comparables, proposed_rv, annual_saving, argument, pdf_url, version
- verdicts: id, case_id, verdict (survive | kill), reasons ([{reason, voa_record}]), version
- cases: case_id (PK), uarn, stage (awaiting_owner | check | challenge | tribunal | won | lost | closed), next_deadline, owner_approved, history
- events: id, ts, agent (scout | builder | advocate | caseworker | playbook | eval | orchestrator), case_id, kind, message, tokens
- playbook: version (PK), rules, status (candidate | live | rejected), eval_run_id
- eval_runs: id, version, lift_at_50, pack_pass_rate, advocate_accuracy, split (train | heldout)

Realtime is on for events, cases and verdicts.

Evidence pack fields (contracts/pack.schema.json, no extra fields allowed): case_id (c_0001 format), uarn, address, current_rv, proposed_rv (below current_rv), comparables (3 to 6 of: uarn, zone_a_price, distance_m, use), calc (zone_a_price, street_median, street_spread), annual_saving, argument (at least 50 chars), version (e.g. builder-v1).

## Claude Managed Agents notes

- Beta header: managed-agents-2026-04-01. Multi-agent block type: multiagent_20261001 (subagents, workflows, optional advisor).
- At most 25 child threads per session; delegation is one level deep; every thread in a session shares one sandbox. That is why the advocate runs in its own session.
- Set up each agent with /claude-api managed-agents-onboard. Use /goal for finish lines (one provable end state, print the proof, "stop after N turns"). Use /claude-api build-eval and /claude-api hillclimb for the eval loop.

## Rules

- VOA data is licensed for rating purposes only. Never commit it or publish it anywhere.
- No real filings: mock the VOA Business Rates API (it is a private trial).
- Every comparable must come from the data service. Never invent one.
- Every agent writes an events row for each meaningful step; that row is the live feed.
- Give every Managed Agents session a token budget (default SESSION_TOKEN_BUDGET=200000 in .env).
- Secrets live in .env (copy from .env.example), which is never committed.
