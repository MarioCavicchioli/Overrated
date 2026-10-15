# advocate agent

Owner: person 2. Argues the VOA's side against one evidence pack and returns survive or kill, with reasons that each cite a VOA record.

- `agent.md`: hosted Managed Agent on `claude-opus-5-5`. Its tools are the four read-only data tools (the same definitions as the builder's) plus `submit_verdict`. It has no bash, files or web.
- `environment.yaml`: its own environment, separate from the Scout's, so it never sees builder notes. Each pack gets a fresh session.
- `handlers.py`: answers its tool calls. Before a verdict is saved, code checks that every cited uarn exists in the data service and was read through a data tool in that session. It writes a `verdicts` row (`reasons` = `[{reason, voa_record, kind}]`) and `events` rows.
- `runner.py`: `run_advocate(case_id)`, the orchestrator contract: loads the pack, runs one session through `orchestrator.sessions.run_session`, gives the advocate the pack plus code-computed facts (spreads from the street median, gap to the nearest same-use property, same-use median within 100 m), writes exactly one verdicts row and returns it, or raises `AdvocateError`. Budget per session: a dollar cap (`ADVOCATE_MAX_CENTS`, default 50) enforced by the platform and the meter, the token meter (`SESSION_TOKEN_BUDGET`) and a turn cap (`ADVOCATE_TURN_CAP`, default 25).
- `run_local.py`: runs the four fixtures in `fixtures/` (`ADVOCATE_RUN_MAX_CENTS`, default 200, caps the whole run).

Create or update the hosted resources with the advocate's own lockfile, so the builder's resources aren't touched:

```
python -m orchestrator.ant apply --lock-file agents/advocate/claude-lock.json agents/advocate/agent.md agents/advocate/environment.yaml
```
