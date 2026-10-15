-- Reconcile the live Supabase schema with contracts/schema.sql at d7ecfd8.
-- Additive and safe to rerun; no existing columns are dropped or renamed and no rows are updated.
begin;

alter table public.scout_runs
    add column if not exists candidates_no_case integer not null default 0;
alter table public.scout_runs
    drop constraint if exists scout_runs_candidates_no_case_check;
alter table public.scout_runs
    add constraint scout_runs_candidates_no_case_check
    check (candidates_no_case >= 0);

alter table public.candidates
    drop constraint if exists candidates_status_check;
alter table public.candidates
    add constraint candidates_status_check
    check (status in (
        'flagged','building_pack','packed','pack_ready','no_case','killed',
        'survived','waiting_owner','approved','in_case','dismissed'
    ));
comment on column public.candidates.status is
    'no_case means no defensible challenge case was found; killed means the advocate rejected a built pack.';

alter table public.packs
    add column if not exists case_ref text unique;
alter table public.packs
    add column if not exists pack jsonb;
comment on column public.packs.case_ref is
    'Readable case identifier, for example c_0001; case_id remains the internal UUID.';
comment on column public.packs.pack is
    'Exact validated evidence pack JSON passed to the advocate.';

alter table public.cases
    drop constraint if exists cases_stage_check;
alter table public.cases
    add constraint cases_stage_check
    check (stage in (
        'waiting_owner','check_ready','check_draft','check_submitted','waiting_check_decision',
        'challenge_draft','challenge_submitted','waiting_challenge_decision',
        'appeal_draft','appeal_submitted','waiting_tribunal','resolved','closed','needs_human'
    ));
comment on column public.verdicts.reasons is
    'JSON array of advocate reasons; each item has reason, voa_record, and kind fields.';

commit;
