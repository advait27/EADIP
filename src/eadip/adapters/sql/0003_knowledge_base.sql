-- 0003_knowledge_base.sql — knowledge-base lineage + pgvector provisioning.
-- Chunk vectors live in Qdrant; this relational `document` row is the governed,
-- tenant-scoped provenance record. pgvector is enabled for the small-corpus
-- pgvector backend option (TAD Ch 4.1).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            UUID NOT NULL REFERENCES tenant(id),
  source_ref           TEXT NOT NULL,
  source_type          TEXT NOT NULL DEFAULT 'document',
  acl_tags             TEXT[] NOT NULL DEFAULT '{}',
  chunk_count          INT NOT NULL DEFAULT 0,
  embed_model_version  TEXT NOT NULL,
  valid_from           TIMESTAMPTZ,
  valid_to             TIMESTAMPTZ,
  ingested_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS document_tenant_idx ON document (tenant_id, ingested_at DESC);

-- Tenant isolation, same pattern as run/audit (SEC-09, AP-5).
ALTER TABLE document ENABLE ROW LEVEL SECURITY;
ALTER TABLE document FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS document_tenant_isolation ON document;
CREATE POLICY document_tenant_isolation ON document
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
