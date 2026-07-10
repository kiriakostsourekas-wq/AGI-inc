-- Run as the managed PostgreSQL owner before applying Alembic migrations.
-- Supply random deployment secrets with:
--   psql "$MIGRATION_DATABASE_URL" \
--     -v runtime_password="$RUNTIME_DB_PASSWORD" \
--     -v oracle_password="$ORACLE_DB_PASSWORD" \
--     -f infra/postgres/provision-production.sql

\set ON_ERROR_STOP on

select format(
  'create role runtime_app login password %L nosuperuser nocreatedb nocreaterole noinherit',
  :'runtime_password'
)
where not exists (select 1 from pg_roles where rolname = 'runtime_app')
\gexec

select format(
  'alter role runtime_app login password %L nosuperuser nocreatedb nocreaterole noinherit',
  :'runtime_password'
)
\gexec

select format(
  'create role eval_oracle login password %L nosuperuser nocreatedb nocreaterole noinherit',
  :'oracle_password'
)
where not exists (select 1 from pg_roles where rolname = 'eval_oracle')
\gexec

select format(
  'alter role eval_oracle login password %L nosuperuser nocreatedb nocreaterole noinherit',
  :'oracle_password'
)
\gexec

revoke create on schema public from public;

select format('revoke all on database %I from public', current_database())
\gexec

select format(
  'grant connect on database %I to runtime_app, eval_oracle',
  current_database()
)
\gexec

create schema if not exists oracle;
revoke all on schema oracle from public, runtime_app;
grant usage on schema oracle to eval_oracle;
