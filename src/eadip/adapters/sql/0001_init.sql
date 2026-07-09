-- 0001_init.sql — tenancy, identity/RBAC, runs, audit + Row-Level Security.
-- Forward-only; safe to re-run (IF NOT EXISTS / idempotent guards). TAD §4.3.

CREATE EXTENSION IF NOT EXISTS citext;

-- Tenancy ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  region      TEXT NOT NULL DEFAULT 'eu',
  settings    JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_user (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenant(id),
  email        CITEXT NOT NULL,
  sso_subject  TEXT NOT NULL,
  UNIQUE (tenant_id, email)
);

-- RBAC ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS role (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID REFERENCES tenant(id),   -- NULL = built-in/global role
  name       TEXT NOT NULL,
  UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS permission (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  role_id     UUID NOT NULL REFERENCES role(id) ON DELETE CASCADE,
  resource    TEXT NOT NULL,
  data_domain TEXT NOT NULL,
  effect      TEXT NOT NULL CHECK (effect IN ('read', 'write', 'high_impact'))
);

CREATE TABLE IF NOT EXISTS user_role (
  user_id  UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  role_id  UUID NOT NULL REFERENCES role(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, role_id)
);

-- Investigation run --------------------------------------------------------
DO $$ BEGIN
  CREATE TYPE run_status AS ENUM
    ('queued', 'planning', 'executing', 'awaiting_approval',
     'verifying', 'done', 'failed', 'cancelled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS run (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenant(id),
  user_id     UUID NOT NULL REFERENCES app_user(id),
  question    TEXT NOT NULL,
  status      run_status NOT NULL DEFAULT 'queued',
  cost_usd    NUMERIC(10,4) NOT NULL DEFAULT 0,
  token_in    BIGINT NOT NULL DEFAULT 0,
  token_out   BIGINT NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS run_tenant_created_idx ON run (tenant_id, created_at DESC);

-- Audit (append-only enforcement added in 0002) ----------------------------
CREATE TABLE IF NOT EXISTS audit_event (
  id         BIGSERIAL PRIMARY KEY,
  tenant_id  UUID NOT NULL,
  run_id     UUID,
  actor      TEXT NOT NULL,            -- user id, or 'system:<agent>'
  action     TEXT NOT NULL,            -- e.g. 'authz.decision', 'run.create'
  detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
  at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_tenant_at_idx ON audit_event (tenant_id, at DESC);

-- Row-Level Security (SEC-09, AP-5) ----------------------------------------
-- A session sees only its tenant's rows. FORCE so the table OWNER is also
-- subject to the policy (the app may connect as the owner in dev). Tenant is
-- supplied per transaction via set_config('app.tenant_id', ...). When unset,
-- current_setting(..., true) returns NULL and the policy denies (deny-by-default).
ALTER TABLE run ENABLE ROW LEVEL SECURITY;
ALTER TABLE run FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS run_tenant_isolation ON run;
CREATE POLICY run_tenant_isolation ON run
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE audit_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_event FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS audit_tenant_isolation ON audit_event;
CREATE POLICY audit_tenant_isolation ON audit_event
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
