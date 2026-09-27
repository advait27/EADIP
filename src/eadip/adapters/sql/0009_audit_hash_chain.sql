-- 0009_audit_hash_chain.sql — per-tenant hash chain on audit_event (SEC-08).
-- Each new row stores prev_hash (the tenant's previous row hash, or 64 zeros
-- for the first chained row) and hash = sha256 over a canonical encoding of
-- (prev_hash, tenant_id, run_id, actor, action, detail, at), computed by the
-- application (eadip.security.audit.event_hash) under a per-tenant advisory
-- lock. Rows written before this migration keep NULL hashes and are NOT
-- covered. The chain makes edits/deletes/reorders in the chained range
-- detectable (`eadip-audit-verify`), but a writer able to bypass the 0002
-- trigger can also recompute the chain; only an externally anchored head hash
-- (e.g. periodic export) detects that, or truncation of the newest rows.

ALTER TABLE audit_event ADD COLUMN IF NOT EXISTS prev_hash TEXT;
ALTER TABLE audit_event ADD COLUMN IF NOT EXISTS hash TEXT;

-- Both set (chained) or both NULL (pre-chain); hex sha256 only.
DO $$ BEGIN
  ALTER TABLE audit_event ADD CONSTRAINT audit_event_chain_shape CHECK (
    (prev_hash IS NULL AND hash IS NULL)
    OR (prev_hash ~ '^[0-9a-f]{64}$' AND hash ~ '^[0-9a-f]{64}$')
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Chain head lookup (latest row per tenant) and in-order verification.
CREATE INDEX IF NOT EXISTS audit_tenant_id_idx ON audit_event (tenant_id, id DESC);
