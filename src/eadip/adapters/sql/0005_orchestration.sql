-- 0005_orchestration.sql — durable orchestration checkpoints (Phase 6).
-- One row per run holds the serialised RunState; the orchestrator upserts it
-- after every node so a crashed run resumes at the last completed step. Tenant-
-- scoped via RLS, same pattern as run/audit/document/finance_metrics.

CREATE TABLE IF NOT EXISTS run_checkpoint (
  run_id      UUID PRIMARY KEY,
  tenant_id   UUID NOT NULL REFERENCES tenant(id),
  status      TEXT NOT NULL,
  iteration   INT NOT NULL DEFAULT 0,
  state       JSONB NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_checkpoint_tenant_idx
  ON run_checkpoint (tenant_id, updated_at DESC);

ALTER TABLE run_checkpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_checkpoint FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS run_checkpoint_tenant_isolation ON run_checkpoint;
CREATE POLICY run_checkpoint_tenant_isolation ON run_checkpoint
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
