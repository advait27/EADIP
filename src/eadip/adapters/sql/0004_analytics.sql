-- 0004_analytics.sql — governed analytical fact table for NL->SQL (Phase 5).
-- Tenant-scoped via Row-Level Security, same pattern as run/audit/document. In
-- production this lives on a read replica; the gateway runs validated SQL here
-- inside a read-only, RLS-scoped transaction (adapters.postgres_warehouse).

CREATE TABLE IF NOT EXISTS finance_metrics (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  region        TEXT NOT NULL,
  product_line  TEXT NOT NULL,
  period        TEXT NOT NULL,           -- e.g. '2026-Q2'
  revenue       NUMERIC(14,2) NOT NULL DEFAULT 0,
  cogs          NUMERIC(14,2) NOT NULL DEFAULT 0,
  opex          NUMERIC(14,2) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS finance_metrics_tenant_idx
  ON finance_metrics (tenant_id, region, period);

-- Tenant isolation (SEC-09, AP-5): a session sees only its tenant's rows, no
-- matter what SQL the generator produced. This is the hard backstop behind the
-- AST validator's cooperative tenant-predicate check.
ALTER TABLE finance_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE finance_metrics FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS finance_metrics_tenant_isolation ON finance_metrics;
CREATE POLICY finance_metrics_tenant_isolation ON finance_metrics
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
