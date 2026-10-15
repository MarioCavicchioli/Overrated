-- Overrated: Supabase schema
-- DRAFT from the Architecture tab so everyone can start. Person 3 owns this file;
-- change columns only after telling the team.

create table if not exists candidates (
  uarn          text primary key,
  address       text not null,
  postcode      text,
  street_key    text,
  rv            integer,
  zone_a_price  numeric,
  street_median numeric,
  z_score       numeric,
  flags         text[] default '{}',
  est_saving    integer,
  status        text not null default 'new',  -- new | building | packed | killed | survived
  created_at    timestamptz not null default now()
);

create table if not exists packs (
  case_id       text primary key,
  uarn          text not null references candidates(uarn),
  pack          jsonb not null,                 -- full pack, validated against pack.schema.json
  comparables   jsonb not null,
  proposed_rv   integer not null,
  annual_saving integer not null,
  argument      text not null,
  pdf_url       text,
  version       text not null,
  created_at    timestamptz not null default now()
);

create table if not exists verdicts (
  id         bigint generated always as identity primary key,
  case_id    text not null references packs(case_id),
  verdict    text not null check (verdict in ('survive', 'kill')),
  reasons    jsonb not null,                    -- [{reason, voa_record}]
  version    text not null,
  created_at timestamptz not null default now()
);

create table if not exists cases (
  case_id        text primary key references packs(case_id),
  uarn           text not null,
  stage          text not null default 'awaiting_owner', -- awaiting_owner | check | challenge | tribunal | won | lost | closed
  next_deadline  timestamptz,
  owner_approved boolean not null default false,
  history        jsonb not null default '[]',
  updated_at     timestamptz not null default now()
);

create table if not exists events (
  id       bigint generated always as identity primary key,
  ts       timestamptz not null default now(),
  agent    text not null,                       -- scout | builder | advocate | caseworker | playbook | eval | orchestrator
  case_id  text,
  kind     text not null,
  message  text not null,
  tokens   integer default 0
);

create table if not exists playbook (
  version     text primary key,
  rules       jsonb not null,
  status      text not null default 'candidate' check (status in ('candidate', 'live', 'rejected')),
  eval_run_id bigint,
  created_at  timestamptz not null default now()
);

create table if not exists eval_runs (
  id                bigint generated always as identity primary key,
  version           text not null,
  lift_at_50        numeric,
  pack_pass_rate    numeric,
  advocate_accuracy numeric,
  split             text check (split in ('train', 'heldout')),
  created_at        timestamptz not null default now()
);

-- Realtime for the live feed and case timeline
alter publication supabase_realtime add table events, cases, verdicts;
