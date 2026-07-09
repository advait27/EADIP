-- 0007_memory_platform.sql — long-term memory + platform administration (Phase 11).
-- episodic_memory / semantic_fact: per-tenant long-term memory (FR-045). Both are
-- tenant-scoped via RLS FORCE; deleting a tenant's rows IS the erasure cascade.
-- prompt_version: platform-global, versioned, IMMUTABLE prompt artifacts (FR-052)
--   — content never updates; only the lifecycle stage (and eval/canary metadata)
--   moves. A trigger rejects content mutation to make immutability structural.
-- tenant_budget: per-tenant monthly spend cap + running spend (FR-053).

CREATE TABLE IF NOT EXISTS episodic_memory (
  id            UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  question_key  TEXT NOT NULL,
  memory        JSONB NOT NULL,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, question_key)
);
CREATE INDEX IF NOT EXISTS episodic_memory_tenant_idx
  ON episodic_memory (tenant_id, updated_at DESC);

ALTER TABLE episodic_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE episodic_memory FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS episodic_memory_tenant_isolation ON episodic_memory;
CREATE POLICY episodic_memory_tenant_isolation ON episodic_memory
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE TABLE IF NOT EXISTS semantic_fact (
  id            UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  subject       TEXT NOT NULL,
  predicate     TEXT NOT NULL,
  object        TEXT NOT NULL,
  fact          JSONB NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, subject, predicate, object)
);
CREATE INDEX IF NOT EXISTS semantic_fact_tenant_idx
  ON semantic_fact (tenant_id, created_at DESC);

ALTER TABLE semantic_fact ENABLE ROW LEVEL SECURITY;
ALTER TABLE semantic_fact FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS semantic_fact_tenant_isolation ON semantic_fact;
CREATE POLICY semantic_fact_tenant_isolation ON semantic_fact
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- Prompt artifacts are platform-global (admin-governed, not tenant data): no RLS,
-- but immutable content enforced by trigger.
CREATE TABLE IF NOT EXISTS prompt_version (
  id           UUID PRIMARY KEY,
  name         TEXT NOT NULL,
  version      INTEGER NOT NULL,
  artifact     JSONB NOT NULL,
  content_sha  TEXT NOT NULL,
  stage        TEXT NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (name, version)
);

CREATE OR REPLACE FUNCTION prompt_version_immutable() RETURNS trigger AS $$
BEGIN
  IF NEW.content_sha IS DISTINCT FROM OLD.content_sha
     OR NEW.name IS DISTINCT FROM OLD.name
     OR NEW.version IS DISTINCT FROM OLD.version THEN
    RAISE EXCEPTION 'prompt_version content is immutable; register a new version';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS prompt_version_immutable_trg ON prompt_version;
CREATE TRIGGER prompt_version_immutable_trg
  BEFORE UPDATE ON prompt_version
  FOR EACH ROW EXECUTE FUNCTION prompt_version_immutable();

CREATE TABLE IF NOT EXISTS tenant_budget (
  tenant_id    UUID PRIMARY KEY REFERENCES tenant(id),
  budget       JSONB NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
