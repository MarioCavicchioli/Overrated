# Team prompts

Each person pastes the shared context plus their own prompt into Claude Code; each prompt builds two agents end to end.

## How to use them

Paste the shared context and then your own prompt into Claude Code, on your own branch.

1. Sign in once with `claude auth login --console`, everyone on the same Console organisation.
2. Open Claude Code in the team repo, on your own branch.
3. Paste the shared context, then your prompt.
4. When a part is ready, paste its `/goal` line as its own command, so Claude Code keeps going until the proof shows.
5. Merge to main at each milestone in the Architecture tab.

Person 3 builds the app in Lovable instead, connected to the same Supabase project.

## Shared context

Every prompt starts with this block, so all three Claude Code sessions build to the same plan.

```text
We are building Overrated at the Accel hackathon (Claude Managed Agents, Lovable, Supabase).

What it does: finds UK shops whose rateable value (the VOA's estimate of yearly rent, which sets business rates) is higher than similar shops on the same street. It builds the evidence, files a Check and Challenge as the owner's agent (mocked today), and runs the case for up to 18 months. We take a share of the saving.

How shops are valued: zoning. Zone A is the first 6.1 m from the window, at a full price per m²; the next strips count at a half, a quarter and an eighth. Similar shops on the same street segment should have a similar Zone A price. Flags: a Zone A price above the street's normal spread; a floor area far from the EPC floor area; a value just over £51,000 or £500,000, where a higher rate applies to the whole value. Bill = value x multiplier (2026: 38.2p, 43.2p, 43p, 48p or 50.8p per £1).

Architecture (tell the team before changing it):
- Six hosted agents on Claude Managed Agents (beta header managed-agents-2026-04-01):
  Scout (lead; multiagent mode multiagent_20261001; fans out Case builders in workflow runs),
  Case builder,
  VOA advocate (its own session per pack; sees only the pack and the data service),
  Caseworker (short sessions woken by a reply, a deadline or a schedule; its memory is the case row),
  Playbook keeper (nightly),
  Eval agent.
- Data service: DuckDB and Parquet over the VOA 2026 list and summary valuations, plus the 2023 list with historic entries. Tools: search_properties, get_valuation, comparables, street_tone, historic_values (eval only). Public URL, so hosted agents can call it.
- Supabase tables: candidates, packs, verdicts, cases, events, playbook, eval_runs (contracts/schema.sql). Every agent writes an events row for each meaningful step; that is the live feed.
- Orchestrator: FastAPI. Starts sessions when new rows land, runs schedules, holds the owner approval gate before filing.
- App: Lovable on Supabase: map, live feed, case timeline, approvals, eval chart.
- Repo: /contracts, /data, /agents/<name>, /orchestrator. Work on your branch; merge at milestones.
- Contracts first, in the first 30 minutes: person 3 writes contracts/schema.sql, person 2 writes contracts/pack.schema.json.

Rules: VOA data is for rating purposes only; never republish it. No real filings: mock the VOA API. Give every session a token budget. Never invent a comparable: each one must come from the data service.
```

## Person 1: data service, Scout and eval agent

Person 1 owns the data and the proof, so this prompt starts with the data check that decides the day.

```text
I'm person 1. Help me build the data service, the Scout agent and the Eval agent.

1. Data service (/data)
- Load the VOA 2026 list entries and summary valuations, and the 2023 list with its historic entries, into DuckDB and Parquet. The files are asterisk-delimited and very large, so filter to the London Borough of Hackney (its billing authority code) for today.
- First, tell me whether the summary valuations include zoned areas and Zone A prices for shops. If they don't, stop and say so.
- Expose five tools as API endpoints or an MCP server with a public URL: search_properties, get_valuation, comparables, street_tone (median Zone A price and spread for a street segment) and historic_values (eval only).

2. Scout agent (/agents/scout): run /claude-api managed-agents-onboard
- Lead agent with multiagent mode on. Score every shop in the target area: its Zone A price against its street segment (in units of the street's spread), the EPC floor-area gap, and closeness to the £51,000 and £500,000 thresholds. Estimate the annual saving.
- Write the top candidates to candidates, then start workflow runs of person 2's Case builder in batches of 10.
- Save a checkpoint after each street, so a restarted session resumes. Read the live playbook at the start. Write an events row for each step.

3. Eval agent (/agents/eval): run /claude-api build-eval
- Layer 1, backtest: run the Scout's scoring on April 2023 values only (nothing later, or the answer leaks). Answer key: properties whose value was cut later in the 2023 list with the floor area unchanged. Score: lift at 50 = hit rate of the top 50 / hit rate of 50 random shops from the same streets.
- Split the streets in half: hill climb on one half, report the score on the other.
- Layer 2, pack checks in code: comparables exist, same use, similar size, and the zoning maths and the saving recompute. Score: pass rate.
- Layer 3: advocate accuracy on decided Valuation Tribunal cases, with the outcome hidden.
- Write one eval_runs row per version. Then run /claude-api hillclimb on the Scout against layer 1 and chart the score per version.
```

Then, one at a time:

```text
/goal the data service returns results from all five tools for three sample shops and prints them; stop after 25 turns
/goal the Scout writes at least 50 scored Hackney candidates to candidates and the events table shows each step; stop after 30 turns
/goal the eval prints lift at 50 on the held-out streets for the current Scout version and writes an eval_runs row; stop after 30 turns
```

## Person 2: case builder, VOA advocate and orchestrator

Person 2 owns the cases, so this prompt fixes the pack format first, because everyone else reads it.

```text
I'm person 2. Help me build the Case builder agent, the VOA advocate agent and the orchestrator.

1. Pack format first (/contracts/pack.schema.json), in the first 30 minutes: case_id, uarn, address, current_rv, proposed_rv, comparables (uarn, zone_a_price, distance_m, use), calc (zone_a_price, street_median, street_spread), annual_saving, argument, version.

2. Case builder (/agents/builder): run /claude-api managed-agents-onboard
- A predefined agent that the Scout runs in workflow runs, one per candidate.
- Using only the data service tools: pick 3 to 6 comparables on the same street segment (same use, similar size), recompute the Zone A value, propose a new rateable value, compute the annual saving (value drop x multiplier), and write the argument in plain English.
- Write a row to packs, a PDF to Supabase Storage, and events rows. Never invent a comparable.

3. VOA advocate (/agents/advocate): run /claude-api managed-agents-onboard
- Runs as its own session per pack, never as a subagent, because subagents share the Scout's sandbox and could read the builders' notes.
- Input: the pack JSON and the data service tools only. Argue the VOA's side: weaker comparables, a different use or size, corner or frontage differences, maths errors. Return survive or kill, with reasons that cite VOA records.
- Write a row to verdicts, and events rows.

4. Orchestrator (/orchestrator, FastAPI)
- Watch Supabase. A new pack starts an advocate session. A survive verdict marks the case as waiting for the owner. Owner approval hands the case to the Caseworker queue.
- Give every session a token budget, and log spend to events.
```

Then, one at a time:

```text
/goal five packs are built for real Hackney candidates, all pass the eval's pack checks, and their PDFs are in Storage; stop after 30 turns
/goal every new pack gets an advocate verdict through the orchestrator with no manual step, and a deliberately weak test pack is killed with reasons; stop after 25 turns
```

## Person 3: Caseworker, playbook keeper and the app

Person 3 owns the long run and what the judges see, so this prompt starts with the tables everyone else writes to.

```text
I'm person 3. Help me build the database, the Caseworker agent and the Playbook keeper. I'll build the app in Lovable.

1. Supabase, first 30 minutes (/contracts/schema.sql)
- Create candidates, packs, verdicts, cases, events, playbook and eval_runs with the columns in the Architecture tab. Seed 20 fake rows so everyone can build against them. Turn on Realtime for events, cases and verdicts.

2. Caseworker (/agents/caseworker): run /claude-api managed-agents-onboard
- Never stays running. A VOA reply, a deadline or the daily schedule starts a short session that reads the case row, decides the next step (accept, move to Challenge, appeal to the Valuation Tribunal), drafts the reply, updates next_deadline and writes events rows.
- Mock the VOA: a "fake VOA reply" button in the app inserts a reply row.

3. Playbook keeper (/agents/playbook): nightly schedule
- Read the outcomes (verdicts, case results) and Valuation Tribunal decisions. Propose playbook version N+1 as a diff, with status candidate. It goes live only if person 1's eval agent scores it at least as high on the held-out streets.

4. The app, in Lovable, connected to the same Supabase project
- Map of candidates coloured by status; live agent feed from events; case timeline over 18 months; owner approval button; eval chart (score per version); run counter (properties checked, cases built, cases killed since the run started).
```

Then, one at a time:

```text
/goal schema.sql is applied, each table has 20 seed rows, and a test insert into events arrives over Realtime; stop after 20 turns
/goal inserting a fake VOA reply wakes the Caseworker, which drafts a reply and moves the case to its next stage in one session; stop after 25 turns
```
