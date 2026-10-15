# Architecture

Six hosted Claude agents share one data service and one database, and each person builds two of them against shared contracts.

## At a glance

&#91;embedded content: architecture: six agents, three owners, one database\]

Only the Scout calls other agents directly. Everything else hands off through a new row, which the orchestrator turns into a session.

## The organisers' five steps

We follow the slide step by step, and each step has a concrete job in Overrated.

| Step | Command | What we use it for |
| --- | --- | --- |
| 1. Sign in | `claude auth login --console` | Everyone signs Claude Code into the same Console organisation, so agents, keys and spend sit in one place |
| 2. Hosted agent | `/claude-api managed-agents-onboard` | Run once per agent. It interviews you, writes the agent config, sets up the environment and tools, and generates the session loop code |
| 3. Finish line | `/goal <finish line>` | Claude Code keeps working until a small checker model sees the proof in the transcript. Each person's goals are in the Team prompts tab |
| 4. Build the eval | `/claude-api build-eval` | Scaffolds the eval agent's three layers (below) |
| 5. Hill climb | `/claude-api hillclimb` | Changes prompts and rules, reruns the eval, keeps what scores higher. We chart the score per version for the demo |

The public docs only describe `managed-agents-onboard` so far; `build-eval` and `hillclimb` come from the slide, so check what they generate. A good `/goal` names one end state you can prove, prints the proof, and caps the run ("stop after 30 turns").

## The six agents

Each agent has one job, one owner and a finish line you can check.

| Agent | Owner | How it runs | Started by | Done when |
| --- | --- | --- | --- | --- |
| Scout | Person 1 | Lead agent with multi-agent mode on; fans out case builders in workflow runs | The Run button, or the long run started in the morning | Every shop in the target postcodes is scored and the top candidates are in `candidates` |
| Case builder | Person 2 | Predefined agent inside the Scout's workflow runs, many in parallel | The Scout | Each candidate has a pack that passes every code check |
| VOA advocate | Person 2 | Its own session per pack, with no access to builder notes | A new row in `packs` | Every pack has a verdict, survive or kill, with reasons citing VOA records |
| Caseworker | Person 3 | One short session per wake-up; the case file in the database is its memory | A VOA reply, a deadline or the daily schedule | Every open case has a next step and a deadline |
| Playbook keeper | Person 3 | Scheduled session every night | The nightly schedule | A candidate playbook is written and scored by the eval agent |
| Eval agent | Person 1 | Its own session, run on demand and after every playbook change | Hill climbing, the playbook gate | A row in `eval_runs` with every score for that version |

All six read data only through the data service, so nobody can cite a comparable that isn't in the VOA's list.

## How the long run works

One run sweeps a whole borough for hours, and every step is saved to the database, so nothing is lost if a session stops.

1. **Start:** the orchestrator opens a Scout session with a token budget and a target. For the demo, Hackney, started in the morning.
2. **Sweep in chunks:** the Scout works street by street and saves a checkpoint after each one. A restarted Scout picks up from the last checkpoint.
3. **Fan out:** for each batch of candidates, the Scout starts a workflow run of case builders. Start with batches of 10; they share the Scout's sandbox and data.
4. **Independent check:** each finished pack starts a separate advocate session that sees only the pack and the data service.
5. **Hand-off:** surviving cases wait for the owner's approval in the app, then join the Caseworker's queue. Killed cases keep their reason.
6. **Wake-ups:** the Caseworker never stays running. A VOA reply, a deadline or the daily schedule starts a short session that reads the case file and acts.
7. **Nightly learning:** the playbook keeper proposes a new playbook, and the eval agent reruns the backtest. It goes live only if the score holds, like tests before a deploy.
8. **Spend:** every session has a budget, and the feed shows tokens spent per agent.

## The eval agent

The eval agent scores each version on real outcomes first, then on pack quality, and code does every check that code can do.

1. **Outcome backtest, the headline number.** The Scout gets only the April 2023 values. The answer key is every property whose value was cut later in the 2023 list with the floor area unchanged. The score is lift at 50: the hit rate of the Scout's top 50, divided by the hit rate of 50 random shops on the same streets.
2. **Pack checks, code first.** Code checks that every comparable exists in the VOA data, has the same use, is a similar size, and that the zoning maths and the saving recompute. A model judges only what code can't, like whether the argument is clear. The score is the pass rate.
3. **Advocate accuracy.** The advocate gets the facts of decided Valuation Tribunal cases, with the outcome hidden. The score is how often its verdict matches the tribunal's.

Two rules keep the scores honest:

- **No leaks:** no value from after April 2023 goes into the backtest inputs.
- **Held-out streets:** hill climb on half the streets and report the score on the other half, so we don't just fit the prompts to the test.

Each run writes one row to `eval_runs`, and the app charts the score for each version.

## Stack and shared contracts

Everyone builds against the same tables and pack format, so the three parts plug together without waiting on each other.

**Stack**

| Layer | Choice | Notes |
| --- | --- | --- |
| Dev | Claude Code, signed in with `--console` | One Console organisation for the team |
| Agents | Claude Managed Agents (beta header `managed-agents-2026-04-01`) | Multi-agent mode (`multiagent_20261001`) on the Scout; up to 25 child threads per session; delegation one level deep |
| Orchestrator | FastAPI | Starts sessions when new rows land, runs schedules, holds the owner approval gate |
| Data service | API or MCP server over DuckDB and Parquet | Tools: `search_properties`, `get_valuation`, `comparables`, `street_tone`, and `historic_values` for the eval only |
| Database | Supabase (Postgres, Realtime, Storage) | Tables below; Storage holds the pack PDFs |
| App | Lovable, with a MapLibre or Leaflet map | Map, live feed, case timeline, approvals, eval chart |
| Hosting | Railway, Render or an ngrok tunnel | Hosted agents need a public URL to call the data service |

**Tables**

| Table | Key columns | Written by | Read by |
| --- | --- | --- | --- |
| `candidates` | uarn, address, postcode, street\_key, rv, zone\_a\_price, street\_median, z\_score, flags, est\_saving, status | Scout | Builders, app |
| `packs` | case\_id, uarn, comparables, proposed\_rv, annual\_saving, argument, pdf\_url, version | Builders | Advocate, app |
| `verdicts` | case\_id, verdict, reasons, version | Advocate | Orchestrator, app |
| `cases` | case\_id, uarn, stage, next\_deadline, owner\_approved, history | Caseworker | App |
| `events` | ts, agent, case\_id, kind, message, tokens | Every agent | App feed |
| `playbook` | version, rules, status, eval\_run\_id | Playbook keeper | Scout, builders |
| `eval_runs` | version, lift\_at\_50, pack\_pass\_rate, advocate\_accuracy, split | Eval agent | App chart |

**Evidence pack format** (`contracts/pack.schema.json`)

```json
{
  "case_id": "c_0001",
  "uarn": "<VOA property id>",
  "address": "<address>",
  "current_rv": 40000,
  "proposed_rv": 30800,
  "comparables": [
    {"uarn": "<id>", "zone_a_price": 400, "distance_m": 60, "use": "cafe"}
  ],
  "calc": {"zone_a_price": 520, "street_median": 400, "street_spread": 35},
  "annual_saving": 3500,
  "argument": "<plain-English case>",
  "version": "builder-v1"
}
```

## Who owns what

Three people own two agents each, and the shared contracts let everyone start at once.

| Person | Agents | Also owns | First milestone |
| --- | --- | --- | --- |
| 1: data and proof | Scout, eval agent | Data service, backtest data | Data check passes; the Scout writes 50 candidates for one postcode |
| 2: cases (@Illia) | Case builder, VOA advocate | Orchestrator, pack format | One pack built and judged end to end |
| 3: long run and app | Caseworker, playbook keeper | Supabase schema, Lovable app | Map and feed live on fake rows |

Build order:

1. **First hour:** person 3 creates the tables with fake rows, person 2 fixes the pack format, person 1 runs the data check. Everyone runs `managed-agents-onboard` for their first agent.
2. **By lunch:** one full path works on fake data, from candidate to pack to verdict to case.
3. **Afternoon:** swap in real VOA data, start the long run on Hackney, and get the first backtest score.
4. **Last two hours:** hill climb on half the streets, score on the other half, freeze, rehearse the demo.
