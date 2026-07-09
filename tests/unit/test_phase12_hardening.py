"""Phase 12 hardening units: retention planning, residency policy + 451 guard,
load-gate logic, PII-redaction auditing, and hardened tool-transport faults."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from eadip.config.settings import Settings
from eadip.eval.load import LoadReport, gate
from eadip.platform.residency import InMemoryResidencyStore, ResidencyPolicy
from eadip.platform.retention import plan

ADMIN = {"X-Roles": "admin"}


# --- retention (GDPR storage limitation) -----------------------------------------
def test_retention_plan_covers_every_class_with_correct_cutoffs() -> None:
    now = datetime(2026, 7, 9, tzinfo=UTC)
    settings = Settings(
        retention_run_days=90, retention_memory_days=365, retention_document_days=730
    )
    statements = plan(settings, now=now)
    by_artifact = {s.artifact: s for s in statements}
    assert set(by_artifact) == {
        "runs",
        "run_checkpoints",
        "episodic_memory",
        "semantic_facts",
        "document_lineage",
    }
    assert (now - by_artifact["runs"].cutoff).days == 90
    assert (now - by_artifact["episodic_memory"].cutoff).days == 365
    assert (now - by_artifact["document_lineage"].cutoff).days == 730
    # Parameterised deletes only — never string-interpolated cutoffs.
    assert all(s.sql.startswith("DELETE FROM ") and "$1" in s.sql for s in statements)
    # The audit trail is never swept (append-only compliance evidence).
    assert not any("audit" in s.sql for s in statements)


def test_retention_window_zero_disables_a_class() -> None:
    settings = Settings(retention_run_days=0, retention_memory_days=30, retention_document_days=0)
    artifacts = {s.artifact for s in plan(settings)}
    assert artifacts == {"episodic_memory", "semantic_facts"}


# --- data residency (PRD §21) -------------------------------------------------------
async def test_residency_unpinned_allowed_pinned_enforced() -> None:
    policy = ResidencyPolicy(InMemoryResidencyStore(), deployment_region="us-east-1")
    tenant = uuid4()
    assert await policy.allows(tenant)  # unpinned -> served anywhere
    await policy.pin(tenant, "eu-west-1")
    assert not await policy.allows(tenant)  # pinned elsewhere -> refused here
    await policy.pin(tenant, "us-east-1")
    assert await policy.allows(tenant)  # pin matches this deployment


def test_residency_guard_refuses_cross_region_with_451(client: TestClient) -> None:
    tenant = uuid4()
    set_pin = client.put(
        f"/v1/admin/tenants/{tenant}/residency", headers=ADMIN, json={"region": "eu-west-1"}
    )
    assert set_pin.status_code == 200 and set_pin.json()["region"] == "eu-west-1"

    headers = {"X-Roles": "analyst", "X-Tenant-Id": str(tenant)}
    refused = client.post("/v1/search", headers=headers, json={"query": "margin"})
    assert refused.status_code == 451
    assert "eu-west-1" in refused.json()["detail"]
    refused_run = client.post("/v1/runs", headers=headers, json={"question": "why?"})
    assert refused_run.status_code == 451

    # Re-pin to the deployment's own region -> served again.
    client.put(f"/v1/admin/tenants/{tenant}/residency", headers=ADMIN, json={"region": "us-east-1"})
    allowed = client.post("/v1/search", headers=headers, json={"query": "margin"})
    assert allowed.status_code == 200


# --- load gate logic (NFR-02) ----------------------------------------------------------
def test_load_gate_breaches_on_errors_and_slow_read_paths() -> None:
    report = LoadReport(users=10, duration_s=1.0)
    report.latencies_ms["search"] = [100.0] * 19 + [5000.0]
    report.latencies_ms["run"] = [30000.0]  # long-running SSE workload: not gated
    assert gate(report, max_p95_ms=2000, max_error_rate=0.0) == ["search p95 5000ms > 2000ms"]

    report_ok = LoadReport(users=10, duration_s=1.0)
    report_ok.latencies_ms["search"] = [100.0] * 20
    assert gate(report_ok, max_p95_ms=2000, max_error_rate=0.0) == []

    report_err = LoadReport(users=10, duration_s=1.0)
    report_err.latencies_ms["search"] = [100.0] * 9
    report_err.failures = ["search: non-2xx"]
    breaches = gate(report_err, max_p95_ms=2000, max_error_rate=0.0)
    assert breaches and "error rate" in breaches[0]


# --- PII redaction is audited (NFR-08) ---------------------------------------------------
async def test_ingestion_audits_pii_redaction() -> None:
    from eadip.adapters.memory_audit_log import InMemoryAuditLog
    from eadip.ingestion.factory import build_pipeline
    from eadip.ingestion.models import IngestRequest

    audit = InMemoryAuditLog()
    pipeline = build_pipeline(Settings(), audit=audit)
    result = await pipeline.ingest(
        IngestRequest(
            tenant_id=uuid4(),
            raw=b"Contact jane.doe@acme.com about the EMEA margin drop.",
            content_type="text/plain",
            source_ref="hr-note-1",
        )
    )
    assert "email" in result.pii_types_found
    events = [e for e in audit.events if e.action == "pii.redacted"]
    assert len(events) == 1
    assert events[0].detail["pii_types"] == ["email"]
    assert events[0].detail["source_ref"] == "hr-note-1"


# --- hardened governed-tool transport (any fault stays governed) --------------------------
async def test_unexpected_transport_fault_returns_refusal_not_crash() -> None:
    from eadip.mcp.demo_tools import DEMO_CONFIG
    from eadip.mcp.registry import InMemoryServerStore, McpManager
    from eadip.security.identity import Identity
    from eadip.security.policy import PolicyDecisionPoint
    from eadip.security.rbac import default_catalog

    class ExplodingTransport:
        async def discover(self):  # type: ignore[no-untyped-def]
            from eadip.mcp.demo_tools import build_demo_transport

            return await build_demo_transport().discover()

        async def invoke(self, tool, arguments):  # type: ignore[no-untyped-def]
            raise ValueError("not a TransportError")  # unexpected fault class

        async def ping(self) -> None:
            return None

    manager = McpManager(
        InMemoryServerStore(),
        PolicyDecisionPoint(default_catalog()),
        transport_provider=lambda config, credential: ExplodingTransport(),
    )
    identity = Identity(user_id=uuid4(), tenant_id=uuid4(), roles=("analyst",))
    await manager.register(identity.tenant_id, DEMO_CONFIG)
    result = await manager.invoke(identity, DEMO_CONFIG.name, "fx_rate", {"currency": "EUR"})
    assert result.ok is False and result.reason == "transport_error"
