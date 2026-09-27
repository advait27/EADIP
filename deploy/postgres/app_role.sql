-- Least-privilege application role for EADIP (run as the database owner/admin).
--   psql "$ADMIN_DSN" -v app_password=<secret> -f deploy/postgres/app_role.sql
-- Run after `eadip-migrate` (it grants on the migrated tables).
-- The app connects as eadip_app; migrations (eadip-migrate) run as the owner via
-- EADIP_POSTGRES_ADMIN_DSN. RLS only binds a role that is not a superuser, has
-- no BYPASSRLS and does not own the tables — which is exactly this role.
\if :{?app_password}
\else
  \set app_password eadip_app
\endif
SELECT 'CREATE ROLE eadip_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eadip_app') \gexec
ALTER ROLE eadip_app WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD :'app_password';

-- Same grants as migration 0010, for databases migrated before the role existed.
GRANT USAGE ON SCHEMA public TO eadip_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eadip_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eadip_app;
REVOKE UPDATE, DELETE ON audit_event FROM eadip_app;
REVOKE ALL ON schema_migrations FROM eadip_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eadip_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO eadip_app;
