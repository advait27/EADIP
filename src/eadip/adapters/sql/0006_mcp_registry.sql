-- 0006_mcp_registry.sql — MCP server registry (Phase 8).
-- One row per registered MCP server holds its config + discovered tools (with
-- cached JSON schemas and permission descriptors) + live health, serialised as
-- the RegisteredServer JSON. Onboarding a system is an upsert here — no code
-- deploy. Tenant-scoped via RLS FORCE, same pattern as run_checkpoint/audit.

CREATE TABLE IF NOT EXISTS mcp_server (
  id           UUID PRIMARY KEY,
  tenant_id    UUID NOT NULL REFERENCES tenant(id),
  name         TEXT NOT NULL,
  state        TEXT NOT NULL,
  server       JSONB NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);
CREATE INDEX IF NOT EXISTS mcp_server_tenant_idx
  ON mcp_server (tenant_id, updated_at DESC);

ALTER TABLE mcp_server ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_server FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mcp_server_tenant_isolation ON mcp_server;
CREATE POLICY mcp_server_tenant_isolation ON mcp_server
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
