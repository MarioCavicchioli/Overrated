-- Overrated: Supabase schema, GENERATED from the live database by
-- contracts/schema_from_live.py (read-only). The live database is the source of truth;
-- person 3 owns it. Do not edit by hand: change the database, then regenerate.
--
-- Read through PostgREST's OpenAPI description (information_schema isn't exposed).
-- Shown: types, NOT NULL, defaults, primary and foreign keys.
-- Not visible from there, so NOT in this file: check constraints (allowed status
-- and stage values), unique constraints other than those implied by foreign keys,
-- indexes, RLS policies, and the realtime publication.

create table if not exists scout_runs (
  run_id                   uuid not null default gen_random_uuid() primary key,
  area_name                text not null,
  area_code                text,
  rating_list              text not null default '2026',
  source_snapshot          text not null,
  source_as_of             date,
  status                   text not null default 'queued',
  started_at               timestamptz,
  finished_at              timestamptz,
  properties_checked       integer not null default 0,
  candidates_flagged       integer not null default 0,
  packs_built              integer not null default 0,
  packs_killed             integer not null default 0,
  config                   jsonb not null,
  error_message            text,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

create table if not exists candidates (
  candidate_id             uuid not null default gen_random_uuid() primary key,
  run_id                   uuid not null references scout_runs(run_id),
  uarn                     text not null,
  rating_list              text not null default '2026',
  source_snapshot          text not null,
  source_record_hash       text,
  address                  text not null,
  postcode                 text,
  street_key               text,
  latitude                 double precision,
  longitude                double precision,
  property_use             text,
  rv                       numeric not null,
  floor_area_m2            numeric,
  zone_a_area_m2           numeric,
  zone_a_price             numeric,
  street_median            numeric,
  street_spread            numeric,
  z_score                  numeric,
  epc_floor_area_m2        numeric,
  epc_area_gap_ratio       numeric,
  threshold_distance       numeric,
  est_saving               numeric,
  score                    numeric,
  flags                    jsonb not null,
  evidence_refs            jsonb not null,
  playbook_version         text,
  status                   text not null default 'flagged',
  scored_at                timestamptz not null default now(),
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

create table if not exists packs (
  pack_id                  uuid not null default gen_random_uuid() primary key,
  case_id                  uuid not null default gen_random_uuid() unique,  -- implied: referenced by a foreign key
  candidate_id             uuid not null references candidates(candidate_id),
  pack_version             integer not null default 1,
  uarn                     text not null,
  address                  text not null,
  current_rv               numeric not null,
  proposed_rv              numeric not null,
  comparables              jsonb not null,
  calc                     jsonb not null,
  annual_saving            numeric not null,
  argument                 text not null,
  evidence_refs            jsonb not null,
  source_snapshot          text not null,
  pdf_url                  text,
  status                   text not null default 'built',
  builder_version          text not null,
  created_at               timestamptz not null default now()
);

create table if not exists verdicts (
  verdict_id               uuid not null default gen_random_uuid() primary key,
  case_id                  uuid not null references packs(case_id),
  pack_id                  uuid not null references packs(pack_id),
  verdict                  text not null,
  reasons                  jsonb not null,
  cited_records            jsonb not null,
  advocate_version         text not null,
  created_at               timestamptz not null default now()
);

create table if not exists cases (
  case_id                  uuid not null primary key references packs(case_id),
  pack_id                  uuid not null references packs(pack_id),
  candidate_id             uuid not null references candidates(candidate_id),
  uarn                     text not null,
  stage                    text not null default 'waiting_owner',
  owner_approved           boolean not null default false,
  owner_approved_at        timestamptz,
  owner_approval_version   integer,
  next_deadline            timestamptz,
  next_action              text,
  external_case_ref        text,
  submission_mode          text not null default 'mock',
  submitted_at             timestamptz,
  resolved_at              timestamptz,
  outcome                  text,
  final_rv                 numeric,
  confirmed_annual_saving  numeric,
  history                  jsonb not null,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

create table if not exists events (
  event_id                 uuid not null default gen_random_uuid() primary key,
  ts                       timestamptz not null default now(),
  agent                    text not null,
  kind                     text not null,
  message                  text not null,
  run_id                   uuid references scout_runs(run_id),
  candidate_id             uuid references candidates(candidate_id),
  pack_id                  uuid references packs(pack_id),
  case_id                  uuid,
  tokens                   integer,
  cost_usd                 numeric,
  payload                  jsonb not null,
  idempotency_key          text
);

create table if not exists eval_runs (
  eval_run_id              uuid not null default gen_random_uuid() primary key,
  version                  text not null,
  scout_version            text,
  builder_version          text,
  advocate_version         text,
  dataset_snapshot         text not null,
  split                    text not null,
  lift_at_50               numeric,
  pack_pass_rate           numeric,
  advocate_accuracy        numeric,
  metrics                  jsonb not null,
  status                   text not null default 'running',
  started_at               timestamptz not null default now(),
  finished_at              timestamptz,
  created_at               timestamptz not null default now()
);

create table if not exists playbook (
  playbook_id              uuid not null default gen_random_uuid() primary key,
  version                  text not null,
  rules                    jsonb not null,
  status                   text not null default 'candidate',
  based_on_version         text,
  diff                     jsonb not null,
  eval_run_id              uuid references eval_runs(eval_run_id),
  created_by               text not null default 'playbook_keeper',
  approved_at              timestamptz,
  created_at               timestamptz not null default now()
);

-- PENDING: agreed, not yet live (person 3 is adding them).
alter table packs add column if not exists case_ref text;  -- readable case id, e.g. c_0001 (pack.schema.json case_id)
alter table packs add column if not exists pack jsonb;  -- the full pack, validated against pack.schema.json
