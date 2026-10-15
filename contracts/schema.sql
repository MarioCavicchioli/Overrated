-- Overrated: Supabase schema
-- DRAFT from the Architecture tab so everyone can start. Person 3 owns this file;
-- change columns only after telling the team.

-- Stores real VOA property identifiers and source provenance; it does not seed
-- demo properties or fabricated outcomes. The authoritative VOA files stay in
-- the DuckDB/Parquet data service. Agents fetch and cite facts through that
-- service, then persist case workflow artifacts here.

-- Append-only app feed/audit trail. Keep references and concise facts here,
-- never a republished copy of the VOA data.

begin;
create extension if not exists pgcrypto;

-- Tracks real-data sweeps and powers per-run counters in the app.
create table if not exists public.scout_runs (
    run_id uuid primary key default gen_random_uuid(),
    area_name text not null,
    area_code text,
    rating_list text not null default '2026',
    source_snapshot text not null,
    source_as_of date,
    status text not null default 'queued' check (status in ('queued','running','paused','completed','failed','cancelled')),
    started_at timestamptz,
    finished_at timestamptz,
    properties_checked integer not null default 0 check (properties_checked >= 0),
    candidates_flagged integer not null default 0 check (candidates_flagged >= 0),
    candidates_no_case integer not null default 0 check (candidates_no_case >= 0),
    packs_built integer not null default 0 check (packs_built >= 0),
    packs_killed integer not null default 0 check (packs_killed >= 0),
    config jsonb not null default '{}'::jsonb,
    error_message text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (finished_at is null or started_at is null or finished_at >= started_at)
);

-- One real VOA hereditament (UARN) scored during a particular Scout run.
-- Source vintage/hash makes results reproducible without copying the VOA dump.
create table if not exists public.candidates (
    candidate_id uuid primary key default gen_random_uuid(),
    run_id uuid not null references public.scout_runs(run_id) on delete cascade,
    uarn text not null,
    rating_list text not null default '2026',
    source_snapshot text not null,
    source_record_hash text,
    address text not null,
    postcode text,
    street_key text,
    latitude double precision,
    longitude double precision,
    property_use text,
    rv numeric(14,2) not null check (rv >= 0),
    floor_area_m2 numeric(12,3) check (floor_area_m2 is null or floor_area_m2 >= 0),
    zone_a_area_m2 numeric(12,3) check (zone_a_area_m2 is null or zone_a_area_m2 >= 0),
    zone_a_price numeric(14,4) check (zone_a_price is null or zone_a_price >= 0),
    street_median numeric(14,4) check (street_median is null or street_median >= 0),
    street_spread numeric(14,4) check (street_spread is null or street_spread >= 0),
    z_score numeric(10,4),
    epc_floor_area_m2 numeric(12,3) check (epc_floor_area_m2 is null or epc_floor_area_m2 >= 0),
    epc_area_gap_ratio numeric(10,5),
    threshold_distance numeric(14,2),
    est_saving numeric(14,2) check (est_saving is null or est_saving >= 0),
    score numeric(12,6),
    flags jsonb not null default '[]'::jsonb,
    evidence_refs jsonb not null default '[]'::jsonb,
    playbook_version text,
    status text not null default 'flagged'
        check (status in ('flagged','building_pack','packed','pack_ready','no_case','killed','survived','waiting_owner','approved','in_case','dismissed')),
    scored_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (run_id, uarn, rating_list)
);

-- Safe to rerun against a project where the earlier draft schema is already applied.
alter table public.scout_runs
    add column if not exists candidates_no_case integer not null default 0
    check (candidates_no_case >= 0);

alter table public.candidates drop constraint if exists candidates_status_check;
alter table public.candidates add constraint candidates_status_check
    check (status in ('flagged','building_pack','packed','pack_ready','no_case','killed','survived','waiting_owner','approved','in_case','dismissed'));
comment on column public.candidates.status is
    'no_case means no defensible challenge case was found; killed means the advocate rejected a built pack.';


-- Immutable versions of builder arguments/calculations. Comparable records must
-- contain real VOA UARNs and evidence refs returned by the data service.
create table if not exists public.packs (
    pack_id uuid primary key default gen_random_uuid(),
    case_id uuid not null unique default gen_random_uuid(),
    case_ref text unique,
    candidate_id uuid not null references public.candidates(candidate_id) on delete restrict,
    pack_version integer not null default 1 check (pack_version > 0),
    uarn text not null,
    address text not null,
    current_rv numeric(14,2) not null check (current_rv >= 0),
    proposed_rv numeric(14,2) not null check (proposed_rv >= 0),
    comparables jsonb not null default '[]'::jsonb,
    pack jsonb,
    calc jsonb not null default '{}'::jsonb,
    annual_saving numeric(14,2) not null check (annual_saving >= 0),
    argument text not null,
    evidence_refs jsonb not null default '[]'::jsonb,
    source_snapshot text not null,
    pdf_url text,
    status text not null default 'built' check (status in ('built','reviewing','survive','killed','waiting_owner','approved','superseded')),
    builder_version text not null,
    created_at timestamptz not null default now(),
    unique (candidate_id, pack_version),
    check (proposed_rv <= current_rv)
);

-- Additive contract fields requested by the cases owner. These ALTERs also
-- upgrade projects where the earlier packs table is already present.
alter table public.packs add column if not exists case_ref text unique;
alter table public.packs add column if not exists pack jsonb;
comment on column public.packs.case_ref is 'Readable case identifier, for example c_0001; case_id remains the internal UUID.';
comment on column public.packs.pack is 'Exact validated evidence pack JSON passed to the advocate.';


create table if not exists public.verdicts (
    verdict_id uuid primary key default gen_random_uuid(),
    case_id uuid not null references public.packs(case_id) on delete restrict,
    pack_id uuid not null references public.packs(pack_id) on delete restrict,
    verdict text not null check (verdict in ('survive','kill','needs_evidence')),
    reasons jsonb not null default '[]'::jsonb,
    cited_records jsonb not null default '[]'::jsonb,
    advocate_version text not null,
    created_at timestamptz not null default now(),
    unique (pack_id, advocate_version)
);

-- Opened after an owner approves a surviving pack. VOA submission remains
-- explicitly mocked until a permitted, working production integration exists.
create table if not exists public.cases (
    case_id uuid primary key references public.packs(case_id) on delete restrict,
    pack_id uuid not null unique references public.packs(pack_id) on delete restrict,
    candidate_id uuid not null references public.candidates(candidate_id) on delete restrict,
    uarn text not null,
    stage text not null default 'waiting_owner' check (stage in (
        'waiting_owner','check_ready','check_draft','check_submitted','waiting_check_decision',
        'challenge_draft','challenge_submitted','waiting_challenge_decision',
        'appeal_draft','appeal_submitted','waiting_tribunal','resolved','closed','needs_human')),
    owner_approved boolean not null default false,
    owner_approved_at timestamptz,
    owner_approval_version integer,
    next_deadline timestamptz,
    next_action text,
    external_case_ref text,
    submission_mode text not null default 'mock' check (submission_mode in ('mock','live')),
    submitted_at timestamptz,
    resolved_at timestamptz,
    outcome text check (outcome is null or outcome in ('reduced','unchanged','withdrawn','partially_reduced','unknown')),
    final_rv numeric(14,2) check (final_rv is null or final_rv >= 0),
    confirmed_annual_saving numeric(14,2) check (confirmed_annual_saving is null or confirmed_annual_saving >= 0),
    history jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (not owner_approved or owner_approved_at is not null)
);

alter table public.cases drop constraint if exists cases_stage_check;
alter table public.cases add constraint cases_stage_check
    check (stage in (
        'waiting_owner','check_ready','check_draft','check_submitted','waiting_check_decision',
        'challenge_draft','challenge_submitted','waiting_challenge_decision',
        'appeal_draft','appeal_submitted','waiting_tribunal','resolved','closed','needs_human'));
comment on column public.verdicts.reasons is
    'JSON array of advocate reasons; each item has reason, voa_record, and kind fields.';


-- Append-only app feed/audit trail. Keep references and concise facts here,
-- never a republished copy of the VOA data.
create table if not exists public.events (
    event_id uuid primary key default gen_random_uuid(),
    ts timestamptz not null default now(),
    agent text not null,
    kind text not null,
    message text not null,
    run_id uuid references public.scout_runs(run_id) on delete set null,
    candidate_id uuid references public.candidates(candidate_id) on delete set null,
    pack_id uuid references public.packs(pack_id) on delete set null,
    case_id uuid,
    tokens integer check (tokens is null or tokens >= 0),
    cost_usd numeric(12,6) check (cost_usd is null or cost_usd >= 0),
    payload jsonb not null default '{}'::jsonb,
    idempotency_key text unique
);

create table if not exists public.eval_runs (
    eval_run_id uuid primary key default gen_random_uuid(),
    version text not null,
    scout_version text,
    builder_version text,
    advocate_version text,
    dataset_snapshot text not null,
    split text not null,
    lift_at_50 numeric(12,6),
    pack_pass_rate numeric(8,6) check (pack_pass_rate is null or pack_pass_rate between 0 and 1),
    advocate_accuracy numeric(8,6) check (advocate_accuracy is null or advocate_accuracy between 0 and 1),
    metrics jsonb not null default '{}'::jsonb,
    status text not null default 'running' check (status in ('running','completed','failed')),
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    created_at timestamptz not null default now(),
    unique (version, dataset_snapshot, split)
);

create table if not exists public.playbook (
    playbook_id uuid primary key default gen_random_uuid(),
    version text not null unique,
    rules jsonb not null,
    status text not null default 'candidate' check (status in ('candidate','live','rejected','superseded')),
    based_on_version text,
    diff jsonb not null default '{}'::jsonb,
    eval_run_id uuid references public.eval_runs(eval_run_id) on delete set null,
    created_by text not null default 'playbook_keeper',
    approved_at timestamptz,
    created_at timestamptz not null default now(),
    check (status <> 'live' or eval_run_id is not null)
);

create index if not exists candidates_run_score_idx on public.candidates (run_id, score desc nulls last);
create index if not exists candidates_uarn_idx on public.candidates (uarn, rating_list);
create index if not exists candidates_status_idx on public.candidates (status);
create index if not exists packs_candidate_idx on public.packs (candidate_id, pack_version desc);
create index if not exists packs_status_idx on public.packs (status);
create index if not exists verdicts_pack_created_idx on public.verdicts (pack_id, created_at desc);
create index if not exists cases_stage_deadline_idx on public.cases (stage, next_deadline);
create index if not exists events_feed_idx on public.events (ts desc);
create index if not exists events_case_idx on public.events (case_id, ts);
create index if not exists playbook_status_idx on public.playbook (status, created_at desc);

create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists scout_runs_set_updated_at on public.scout_runs;
create trigger scout_runs_set_updated_at before update on public.scout_runs
for each row execute function public.set_updated_at();
drop trigger if exists candidates_set_updated_at on public.candidates;
create trigger candidates_set_updated_at before update on public.candidates
for each row execute function public.set_updated_at();
drop trigger if exists cases_set_updated_at on public.cases;
create trigger cases_set_updated_at before update on public.cases
for each row execute function public.set_updated_at();

-- Browser access: authenticated users may read app tables; anonymous users may not.
-- The server-side orchestrator uses service_role, which bypasses RLS in Supabase.
do $$
declare
    policy_row record;
begin
    for policy_row in
        select schemaname, tablename, policyname
        from pg_policies
        where schemaname = 'public'
          and tablename = any (array[
              'scout_runs','candidates','packs','verdicts',
              'cases','events','eval_runs','playbook'
          ])
          and roles && array['anon','public']::name[]
    loop
        execute format('drop policy if exists %I on %I.%I',
                       policy_row.policyname, policy_row.schemaname, policy_row.tablename);
    end loop;
end;
$$;

alter table public.scout_runs enable row level security;
alter table public.candidates enable row level security;
alter table public.packs enable row level security;
alter table public.verdicts enable row level security;
alter table public.cases enable row level security;
alter table public.events enable row level security;
alter table public.eval_runs enable row level security;
alter table public.playbook enable row level security;

revoke all on table public.scout_runs, public.candidates, public.packs, public.verdicts,
    public.cases, public.events, public.eval_runs, public.playbook from anon;
grant select on table public.scout_runs, public.candidates, public.packs, public.verdicts,
    public.cases, public.events, public.eval_runs, public.playbook to authenticated;

drop policy if exists overrated_authenticated_select on public.scout_runs;
create policy overrated_authenticated_select on public.scout_runs
    for select to authenticated using (true);
drop policy if exists overrated_authenticated_select on public.candidates;
create policy overrated_authenticated_select on public.candidates
    for select to authenticated using (true);
drop policy if exists overrated_authenticated_select on public.packs;
create policy overrated_authenticated_select on public.packs
    for select to authenticated using (true);
drop policy if exists overrated_authenticated_select on public.verdicts;
create policy overrated_authenticated_select on public.verdicts
    for select to authenticated using (true);
drop policy if exists overrated_authenticated_select on public.cases;
create policy overrated_authenticated_select on public.cases
    for select to authenticated using (true);
drop policy if exists overrated_authenticated_select on public.events;
create policy overrated_authenticated_select on public.events
    for select to authenticated using (true);
drop policy if exists overrated_authenticated_select on public.eval_runs;
create policy overrated_authenticated_select on public.eval_runs
    for select to authenticated using (true);
drop policy if exists overrated_authenticated_select on public.playbook;
create policy overrated_authenticated_select on public.playbook
    for select to authenticated using (true);

-- Add app-facing tables to Supabase Realtime when the publication exists.
do $$
begin
    if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
        begin alter publication supabase_realtime add table public.events; exception when duplicate_object then null; end;
        begin alter publication supabase_realtime add table public.cases; exception when duplicate_object then null; end;
        begin alter publication supabase_realtime add table public.verdicts; exception when duplicate_object then null; end;
    end if;
end;
$$;

commit;