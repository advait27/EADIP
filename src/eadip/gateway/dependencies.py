"""Central dependency injection for the gateway.

Selects in-memory vs PostgreSQL adapters by `database_enabled` so dev/tests run
with no database while production gets RLS-scoped, audited persistence.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException

from eadip.adapters.memory_audit_log import InMemoryAuditLog
from eadip.adapters.memory_event_log import InMemoryEventLog
from eadip.adapters.memory_run_repository import InMemoryRunRepository
from eadip.adapters.postgres import Database
from eadip.adapters.postgres_audit_log import PostgresAuditLog
from eadip.adapters.postgres_checkpointer import PostgresCheckpointer
from eadip.adapters.postgres_run_repository import PostgresRunRepository
from eadip.analytics.factory import build_analytics_service, build_warehouse
from eadip.analytics.service import AnalyticsService
from eadip.approval.service import ApprovalService
from eadip.config.settings import Settings, get_settings
from eadip.gateway.auth import get_identity
from eadip.graph.factory import build_graph_service, build_knowledge_graph
from eadip.graph.service import GraphRAGService
from eadip.ingestion.factory import build_vector_store
from eadip.mcp.factory import build_mcp_manager, build_tool_executor
from eadip.mcp.registry import McpManager
from eadip.memory.agent import MemoryAgent
from eadip.memory.factory import build_memory_agent
from eadip.orchestrator.checkpoint import Checkpointer, InMemoryCheckpointer
from eadip.orchestrator.executor import RunExecutor
from eadip.orchestrator.factory import build_orchestrator
from eadip.orchestrator.models import Event
from eadip.orchestrator.service import OrchestratorService
from eadip.orchestrator.state import RunState
from eadip.platform.budgets import BudgetLedger
from eadip.platform.factory import (
    build_budget_ledger,
    build_concurrency_gate,
    build_flag_service,
    build_notification_service,
    build_prompt_registry,
    build_residency_policy,
    build_routing_policy,
)
from eadip.platform.flags import FeatureFlagService
from eadip.platform.models import Notification, NotificationKind
from eadip.platform.notifications import (
    InMemoryChannel,
    NotificationService,
    notifications_from_event,
)
from eadip.platform.prompts import PromptRegistry
from eadip.platform.ratelimit import ConcurrencyGate
from eadip.platform.residency import ResidencyPolicy
from eadip.platform.routing import ModelRoutingPolicy
from eadip.ports.events import EventLog, LoggedEvent
from eadip.ports.graph import KnowledgeGraph
from eadip.ports.repositories import RunRepository
from eadip.ports.vector_store import VectorStore
from eadip.ports.warehouse import Warehouse
from eadip.retrieval.factory import build_retrieval_service
from eadip.retrieval.service import RetrievalService
from eadip.security.audit import AuditLog
from eadip.security.identity import Identity
from eadip.security.policy import PolicyDecisionPoint
from eadip.security.rbac import default_catalog
from eadip.security.vault import EnvSecretsProvider, SecretsProvider, StaticSecretsProvider

SettingsDep = Annotated[Settings, Depends(get_settings)]
IdentityDep = Annotated[Identity, Depends(get_identity)]


# --- process-wide singletons -------------------------------------------------
@lru_cache
def get_pdp() -> PolicyDecisionPoint:
    return PolicyDecisionPoint(default_catalog())


@lru_cache
def get_database() -> Database:
    return Database(get_settings().postgres_dsn.get_secret_value())


@lru_cache
def get_secrets_provider() -> SecretsProvider:
    s = get_settings()
    if s.environment == "dev":
        # Deterministic dev key so local runs work without configuring a vault.
        # >=32 bytes per RFC 7518; clearly insecure and dev-only (prod uses vault).
        return StaticSecretsProvider(
            {s.service_jwt_secret_key: "dev-insecure-service-jwt-key-do-not-use-in-prod"}
        )
    return EnvSecretsProvider()


@lru_cache
def _dev_run_repo() -> InMemoryRunRepository:
    return InMemoryRunRepository()


@lru_cache
def _dev_audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


# --- request-scoped providers ------------------------------------------------
def get_run_repository(identity: IdentityDep) -> RunRepository:
    if get_settings().database_enabled:
        return PostgresRunRepository(get_database(), identity.tenant_id)
    return _dev_run_repo()


def get_audit_log() -> AuditLog:
    if get_settings().database_enabled:
        return PostgresAuditLog(get_database())
    return _dev_audit_log()


@lru_cache
def get_vector_store() -> VectorStore:
    # Process-wide so ingestion and retrieval share one store (memory or Qdrant).
    return build_vector_store(get_settings())


@lru_cache
def get_knowledge_graph() -> KnowledgeGraph:
    return build_knowledge_graph(get_settings())


@lru_cache
def get_graph_service() -> GraphRAGService:
    return build_graph_service(get_settings(), get_knowledge_graph())


@lru_cache
def get_retrieval_service() -> RetrievalService:
    s = get_settings()
    graph = get_graph_service() if s.graph_enabled else None
    return build_retrieval_service(s, get_vector_store(), graph=graph)


@lru_cache
def get_warehouse() -> Warehouse:
    s = get_settings()
    if s.warehouse_backend == "postgres":
        return build_warehouse(s, database=get_database())
    return build_warehouse(s)


@lru_cache
def get_analytics_service() -> AnalyticsService:
    return build_analytics_service(get_settings(), get_warehouse())


@lru_cache
def _memory_checkpointer() -> InMemoryCheckpointer:
    return InMemoryCheckpointer()


@lru_cache
def get_checkpointer() -> Checkpointer:
    if get_settings().checkpoint_backend == "postgres":
        return PostgresCheckpointer(get_database())
    return _memory_checkpointer()


@lru_cache
def get_mcp_manager() -> McpManager:
    s = get_settings()
    store = None
    if s.mcp_registry_backend == "postgres" and s.database_enabled:
        from eadip.adapters.postgres_mcp_store import PostgresServerStore

        store = PostgresServerStore(get_database())
    return build_mcp_manager(s, get_pdp(), store=store, secrets=get_secrets_provider())


@lru_cache
def get_memory_agent() -> MemoryAgent | None:
    s = get_settings()
    db = get_database() if (s.memory_backend == "postgres" and s.database_enabled) else None
    return build_memory_agent(s, database=db)


@lru_cache
def get_prompt_registry() -> PromptRegistry:
    s = get_settings()
    db = get_database() if (s.prompt_backend == "postgres" and s.database_enabled) else None
    return build_prompt_registry(s, database=db)


@lru_cache
def get_routing_policy() -> ModelRoutingPolicy:
    return build_routing_policy(get_settings())


@lru_cache
def get_budget_ledger() -> BudgetLedger:
    s = get_settings()
    db = get_database() if (s.budget_backend == "postgres" and s.database_enabled) else None
    return build_budget_ledger(s, database=db)


@lru_cache
def get_notification_inbox() -> InMemoryChannel:
    # Process-wide so the publisher and the portal's inbox listing share state.
    return InMemoryChannel()


@lru_cache
def get_notification_service() -> NotificationService:
    return build_notification_service(get_settings(), inbox=get_notification_inbox())


@lru_cache
def get_flag_service() -> FeatureFlagService:
    return build_flag_service(get_settings())


@lru_cache
def get_concurrency_gate() -> ConcurrencyGate:
    return build_concurrency_gate(get_settings())


@lru_cache
def get_residency_policy() -> ResidencyPolicy:
    s = get_settings()
    db = get_database() if (s.residency_backend == "postgres" and s.database_enabled) else None
    return build_residency_policy(s, database=db)


async def require_residency(identity: IdentityDep) -> Identity:
    """Data-residency guard (Phase 12, PRD §21): a tenant pinned to another
    region is refused with 451 BEFORE any data access in this deployment."""
    policy = get_residency_policy()
    if not await policy.allows(identity.tenant_id):
        pinned = await policy.region_for(identity.tenant_id)
        raise HTTPException(
            status_code=451,
            detail=f"tenant data resides in region '{pinned}'; "
            f"this deployment serves '{policy.deployment_region}'",
        )
    return identity


@lru_cache
def get_orchestrator() -> OrchestratorService:
    s = get_settings()
    tool_executor = build_tool_executor(s, get_mcp_manager()) if s.mcp_enabled else None
    return build_orchestrator(
        s,
        retrieval_service=get_retrieval_service(),
        analytics_service=get_analytics_service(),
        warehouse=get_warehouse(),
        checkpointer=get_checkpointer(),
        tool_executor=tool_executor,
        memory=get_memory_agent(),
        prompts=get_prompt_registry(),
    )


@lru_cache
def get_event_log() -> EventLog:
    # Process-wide: the executor appends, every stream/replay/share reads.
    return InMemoryEventLog(max_runs=get_settings().event_log_max_runs)


async def _notify_and_charge(state: RunState, event: LoggedEvent) -> None:
    """Executor hook (Glass Box): the two side effects that used to live in the
    SSE route — notification fan-out on approval/completion/anomaly events, and
    charging the run's final cost to the tenant budget on ``run.done``."""
    notifications = get_notification_service()
    plain = Event(type=event.type, data=event.data)
    for note in notifications_from_event(state.tenant_id, state.run_id, plain):
        await notifications.publish(note)
    if event.type == "run.done":
        budget = await get_budget_ledger().charge(
            state.tenant_id, float(event.data.get("cost_usd", 0.0))
        )
        if budget.exhausted:
            await notifications.publish(
                Notification(
                    tenant_id=state.tenant_id,
                    kind=NotificationKind.BUDGET_EXHAUSTED,
                    severity="warning",
                    title="Monthly budget exhausted — new runs will be refused",
                    body=f"spent ${budget.spent_usd:g} of ${budget.monthly_cap_usd:g}",
                    run_id=state.run_id,
                )
            )


@lru_cache
def get_run_executor() -> RunExecutor:
    return RunExecutor(
        orchestrator=get_orchestrator(),
        checkpointer=get_checkpointer(),
        event_log=get_event_log(),
        gate=get_concurrency_gate(),
        hooks=[_notify_and_charge],
    )


async def _event_log_dep() -> EventLog:
    # Async on purpose: FastAPI runs *sync* dependencies in a threadpool, and
    # concurrent cold-start requests can each miss the lru_cache and build their
    # own instance (lru_cache does not lock around the factory call). Stateful
    # singletons must be resolved on the event loop, where this cannot race.
    return get_event_log()


async def _run_executor_dep() -> RunExecutor:
    return get_run_executor()


def warm_singletons() -> None:
    """Build the stateful process-wide services once, up front (lifespan)."""
    get_run_executor()


RunRepositoryDep = Annotated[RunRepository, Depends(get_run_repository)]
EventLogDep = Annotated[EventLog, Depends(_event_log_dep)]
RunExecutorDep = Annotated[RunExecutor, Depends(_run_executor_dep)]
AuditLogDep = Annotated[AuditLog, Depends(get_audit_log)]
PdpDep = Annotated[PolicyDecisionPoint, Depends(get_pdp)]
RetrievalServiceDep = Annotated[RetrievalService, Depends(get_retrieval_service)]
AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]
CheckpointerDep = Annotated[Checkpointer, Depends(get_checkpointer)]
OrchestratorDep = Annotated[OrchestratorService, Depends(get_orchestrator)]
McpManagerDep = Annotated[McpManager, Depends(get_mcp_manager)]
MemoryAgentDep = Annotated[MemoryAgent | None, Depends(get_memory_agent)]
PromptRegistryDep = Annotated[PromptRegistry, Depends(get_prompt_registry)]
RoutingPolicyDep = Annotated[ModelRoutingPolicy, Depends(get_routing_policy)]
BudgetLedgerDep = Annotated[BudgetLedger, Depends(get_budget_ledger)]
NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]
NotificationInboxDep = Annotated[InMemoryChannel, Depends(get_notification_inbox)]
FlagServiceDep = Annotated[FeatureFlagService, Depends(get_flag_service)]
ConcurrencyGateDep = Annotated[ConcurrencyGate, Depends(get_concurrency_gate)]
ResidencyPolicyDep = Annotated[ResidencyPolicy, Depends(get_residency_policy)]
ResidencyGuardDep = Annotated[Identity, Depends(require_residency)]


def get_approval_service(checkpointer: CheckpointerDep) -> ApprovalService:
    return ApprovalService(checkpointer)


ApprovalServiceDep = Annotated[ApprovalService, Depends(get_approval_service)]
