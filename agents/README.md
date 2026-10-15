# Agent operations and dashboard security

## Supabase browser access

Apply supabase/migrations/20261015143000_authenticated_read_rls.sql in Supabase after the dashboard has Supabase Auth sign-in ready. It enables RLS on all eight application tables, revokes table access from anon, and allows authenticated users to read. The orchestrator must use the server-side SUPABASE_SERVICE_ROLE_KEY; never put this key in Lovable or browser code.

Create the judges' demo user in Supabase Auth, then make the dashboard sign in with that account before reading tables or subscribing to Realtime. This repository does not contain the Lovable dashboard source, so that frontend login change must be made in the Lovable project. Test logged-out reads, logged-in reads, and Realtime after applying the migration.

## Scheduled jobs

The Playbook Keeper runs nightly at 02:00 UTC. The Caseworker deadline sweep runs every 15 minutes. Both workflows can also be started manually from GitHub Actions. Add these repository Actions secrets before enabling runs:

- ANTHROPIC_API_KEY
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY
- MANAGED_AGENT_ENVIRONMENT_ID
- PLAYBOOK_KEEPER_AGENT_ID
- CASEWORKER_AGENT_ID

Scheduled workflows run from the default branch; merge these workflow files to main before expecting scheduled runs. The Lovable app writes fake_voa_reply events to Supabase. Persisting that row does not by itself call the Python Caseworker. The server-side consumer must invoke orchestrator.caseworker_dispatch.on_persisted_event(event_id) after the insert; connect this through the orchestrator or a Supabase Database Webhook/Edge Function.
