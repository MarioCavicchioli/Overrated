# Caseworker

The orchestrator calls orchestrator.caseworker_dispatch.on_persisted_event(event_id) after committing a voa_reply_received or fake_voa_reply event. Real replies are processed only for owner-approved cases. fake_voa_reply always runs in simulation mode: it produces a test event and never changes the case row.

A periodic deadline job calls orchestrator.caseworker_dispatch.run_deadline_sweep() (or python -m orchestrator.caseworker_dispatch --deadlines-once) on a schedule such as every 15 minutes. It processes overdue, owner-approved cases in active Check, Challenge, and Tribunal stages. It writes a human-review draft and an audit event. The agent does not invent or change legal deadlines and never submits a filing.

The handler writes its output event with a unique idempotency key before changing the case. If the case update fails, a retry reuses the saved decision and attempts to reconcile it without starting a second model session. A concurrent unrelated stage change fails closed for human review. Simulations never mutate case state.

Each output event stores session input/output token totals and list cost when returned by the Managed Agents API. Events include a draft reply, rationale, action, stage recommendation, source identifier, and simulation marker.

Required server-side environment variables:
- ANTHROPIC_API_KEY
- CASEWORKER_AGENT_ID
- MANAGED_AGENT_ENVIRONMENT_ID
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY (never expose this to the browser)

CASEWORKER_MAX_LIST_COST_MINOR_UNITS defaults to 200 (USD $2.00 per session). Install dependencies with pip install -r agents/caseworker/requirements.txt.

The repository has no FastAPI application source yet. Person 2's event handler should call on_persisted_event(event_id) after the event insert succeeds. A host scheduler should invoke run_deadline_sweep() periodically; Supabase Realtime alone does not schedule future deadlines.

Run unit checks with python -m unittest agents.caseworker.test_caseworker
