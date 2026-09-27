-- Local compose only: create the least-privilege app role on first init.
-- Grants come from migration 0010 when `eadip-migrate` runs as the owner.
-- Dev password; production uses deploy/postgres/app_role.sql with a secret.
CREATE ROLE eadip_app LOGIN PASSWORD 'eadip_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
