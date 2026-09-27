-- 0002_audit_append_only.sql — make audit_event append-only (SEC-08).
-- A BEFORE UPDATE/DELETE row trigger rejects mutation for any role that cannot
-- change the trigger. It is NOT tamper-evidence: a superuser or the table owner
-- can disable the trigger (and TRUNCATE is not covered) and rewrite rows without
-- a trace here. Tamper-evidence for rows written from 0009 on comes from the
-- per-tenant hash chain (0009_audit_hash_chain.sql), which in turn needs an
-- externally anchored head hash to detect a consistent rewrite of the chain.
-- In production, also REVOKE UPDATE/DELETE/TRUNCATE from the least-privileged
-- application role, and do not let it own the table.

CREATE OR REPLACE FUNCTION audit_event_no_mutate() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_event is append-only (SEC-08): % not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_event_append_only ON audit_event;
CREATE TRIGGER audit_event_append_only
  BEFORE UPDATE OR DELETE ON audit_event
  FOR EACH ROW EXECUTE FUNCTION audit_event_no_mutate();
