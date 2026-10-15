# Playbook Keeper

Run python -m agents.playbook.playbook_keeper from a nightly scheduler. It reads the current live playbook and the latest completed heldout evaluation runs, sends only aggregate metrics and the current rules to a Managed Agent, and stores a proposed version as status candidate.

The keeper never promotes or rejects a playbook. Person 1 must evaluate the candidate on held-out data, then a human must approve promotion. The eval_run_id on the candidate remains null until the new candidate itself has been evaluated; the older evaluations are input evidence, not proof of the candidate.

Required environment variables:
- ANTHROPIC_API_KEY
- PLAYBOOK_KEEPER_AGENT_ID
- MANAGED_AGENT_ENVIRONMENT_ID
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY

PLAYBOOK_KEEPER_MAX_LIST_COST_MINOR_UNITS defaults to 300 (USD $3.00 per session). Dependencies are in agents/caseworker/requirements.txt.
Run unit checks with python -m unittest agents.playbook.test_playbook_keeper
