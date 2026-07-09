-- 0008_residency.sql — per-tenant data residency (Phase 12, PRD §21).
-- A tenant pinned to a region is only served by deployments in that region;
-- the gateway refuses cross-region serving with 451 before touching data.
-- Administered cross-tenant by the platform admin (no RLS, like tenant_budget).

CREATE TABLE IF NOT EXISTS tenant_residency (
  tenant_id    UUID PRIMARY KEY REFERENCES tenant(id),
  region       TEXT NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
