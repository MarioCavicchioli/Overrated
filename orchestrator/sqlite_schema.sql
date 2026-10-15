-- Local SQLite copy of contracts/schema.sql, used until the Supabase project is ready.
-- Same tables and columns; Postgres types mapped to SQLite (jsonb/text[] -> JSON text,
-- timestamptz -> ISO text, identity -> AUTOINCREMENT). Keep in step with schema.sql;
-- tests/test_store.py fails if the column names drift.

create table if not exists candidates (
  uarn          text primary key,
  address       text not null,
  postcode      text,
  street_key    text,
  rv            integer,
  zone_a_price  numeric,
  street_median numeric,
  z_score       numeric,
  flags         text default '[]',
  est_saving    integer,
  status        text not null default 'new',
  created_at    text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

create table if not exists packs (
  case_id       text primary key,
  uarn          text not null references candidates(uarn),
  pack          text not null,
  comparables   text not null,
  proposed_rv   integer not null,
  annual_saving integer not null,
  argument      text not null,
  pdf_url       text,
  version       text not null,
  created_at    text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

create table if not exists verdicts (
  id         integer primary key autoincrement,
  case_id    text not null references packs(case_id),
  verdict    text not null check (verdict in ('survive', 'kill')),
  reasons    text not null,
  version    text not null,
  created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

create table if not exists cases (
  case_id        text primary key references packs(case_id),
  uarn           text not null,
  stage          text not null default 'awaiting_owner',
  next_deadline  text,
  owner_approved integer not null default 0,
  history        text not null default '[]',
  updated_at     text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

create table if not exists events (
  id       integer primary key autoincrement,
  ts       text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  agent    text not null,
  case_id  text,
  kind     text not null,
  message  text not null,
  tokens   integer default 0
);

create table if not exists playbook (
  version     text primary key,
  rules       text not null,
  status      text not null default 'candidate' check (status in ('candidate', 'live', 'rejected')),
  eval_run_id integer,
  created_at  text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

create table if not exists eval_runs (
  id                integer primary key autoincrement,
  version           text not null,
  lift_at_50        numeric,
  pack_pass_rate    numeric,
  advocate_accuracy numeric,
  split             text check (split in ('train', 'heldout')),
  created_at        text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
