# Playbook Keeper

From the repository root, install dependencies with python -m pip install -r agents/caseworker/requirements.txt, copy .env.example to .env, and fill in the required values. The agent loads .env from the repository root for local runs; existing process or CI environment variables take precedence.

Run python -m agents.playbook.playbook_keeper locally or from a nightly scheduler. It reads the current live playbook and the latest completed heldout evaluation runs, sends only aggregate metrics and the current rules to a Managed Agent, and stores a proposed version as status candidate.

The keeper never promotes or rejects a playbook. Person 1 must evaluate the candidate on held-out data, then a human must approve promotion. The eval_run_id on the candidate remains null until the new candidate itself has been evaluated; the older evaluations are input evidence, not proof of the candidate. Because the keeper has already seen the snapshots in its input event, candidate evaluation must use a fresh held-out dataset_snapshot that is not listed in that event's evaluation_ids; otherwise the held-out result leaks into rule tuning.

Required environment variables:
- ANTHROPIC_API_KEY
- PLAYBOOK_KEEPER_AGENT_ID
- MANAGED_AGENT_ENVIRONMENT_ID
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY

PLAYBOOK_KEEPER_MAX_LIST_COST_MINOR_UNITS defaults to 300 (USD $3.00 per session). Dependencies are in agents/caseworker/requirements.txt.
Run unit checks from the repository root with python -m unittest agents.playbook.test_playbook_keeper -v. The test file can also be run directly with python agents/playbook/test_playbook_keeper.py.
