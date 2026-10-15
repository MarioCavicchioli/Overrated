-- Restrict browser access to signed-in users; service_role remains server-only.
begin;

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

commit;
