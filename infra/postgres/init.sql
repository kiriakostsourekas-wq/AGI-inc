-- Development-only login passwords are supplied by the local Compose boundary.
-- Deployed environments provision equivalent roles through their secret manager.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'runtime_app') then
    create role runtime_app login password 'trust_runtime'
      nosuperuser nocreatedb nocreaterole noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'eval_oracle') then
    create role eval_oracle login password 'trust_oracle'
      nosuperuser nocreatedb nocreaterole noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'migration_admin') then
    create role migration_admin nologin nosuperuser nocreatedb nocreaterole noinherit;
  end if;
end;
$$;

revoke create on schema public from public;
revoke all on database trust_runtime from public;

grant connect on database trust_runtime to runtime_app, eval_oracle, migration_admin;
grant create on database trust_runtime to migration_admin;
grant migration_admin to trust;

create schema if not exists runtime authorization trust;
create schema if not exists sandbox authorization trust;
create schema if not exists oracle authorization trust;

grant usage on schema runtime, sandbox to runtime_app;
grant usage on schema runtime, sandbox, oracle to eval_oracle;

alter default privileges for role trust in schema runtime
  revoke all on tables from runtime_app, eval_oracle;
alter default privileges for role trust in schema runtime
  revoke all on sequences from runtime_app, eval_oracle;
alter default privileges for role trust in schema sandbox
  revoke all on tables from runtime_app, eval_oracle;
alter default privileges for role trust in schema sandbox
  revoke all on sequences from runtime_app, eval_oracle;
alter default privileges for role trust in schema oracle
  grant select, insert, update on tables to eval_oracle;
alter default privileges for role trust in schema oracle
  grant usage, select on sequences to eval_oracle;

-- No browser/client role receives CONNECT or schema privileges. Runtime access to
-- oracle-only tables is intentionally absent. Table-specific grants are finalized
-- by Alembic after the tables exist, including append-only run-event permissions.
