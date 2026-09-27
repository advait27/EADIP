-- 0010_app_role_grants.sql — least-privilege application role (SEC-09, AP-5).
-- Row-Level Security does not apply to superusers or to roles with BYPASSRLS,
-- and FORCE only extends it to the table owner. So the app must connect as a
-- role that is none of these: `eadip_app` (created by
-- deploy/postgres/app_role.sql, or the compose init script), while migrations
-- run as the owner. This grants DML to that role when it exists; a database
-- without it is left unchanged (re-run app_role.sql after creating the role).

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eadip_app') THEN
    EXECUTE 'GRANT USAGE ON SCHEMA public TO eadip_app';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eadip_app';
    EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eadip_app';
    -- The audit trail is insert-only for the app (the 0002 trigger also blocks it).
    EXECUTE 'REVOKE UPDATE, DELETE ON audit_event FROM eadip_app';
    EXECUTE 'REVOKE ALL ON schema_migrations FROM eadip_app';
    -- Tables created by later migrations (run by this same owner role).
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eadip_app';
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO eadip_app';
  END IF;
END
$$;
