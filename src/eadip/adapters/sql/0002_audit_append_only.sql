-- 0002_audit_append_only.sql — make audit_event tamper-evident (SEC-08).
-- A BEFORE UPDATE/DELETE trigger rejects any mutation, so the audit trail is
-- insert-only regardless of the connecting role. In production, also REVOKE
-- UPDATE/DELETE from the least-privileged application role as defense in depth.

CREATE OR REPLACE FUNCTION audit_event_no_mutate() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_event is append-only (SEC-08): % not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_event_append_only ON audit_event;
CREATE TRIGGER audit_event_append_only
  BEFORE UPDATE OR DELETE ON audit_event
  FOR EACH ROW EXECUTE FUNCTION audit_event_no_mutate();
