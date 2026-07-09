"""Administration Portal API (Phase 11, FR-052/053/056/057, US-D2/D3, TAD Ch 14).

Admin self-serve over the platform's governance surfaces: roles (RBAC), prompt
lifecycle (register → eval → canary → promote → rollback), model routing
(guardrailed tier overrides), per-tenant budgets, feature flags, long-term
memory (status + erasure cascade) and the notification inbox.

Every route requires the `admin`-only (resource=admin, domain=platform) grant —
deny-by-default, like everything else — and every mutation is audited. Identity
lifecycle itself (creating users, assigning them roles) lives in the enterprise
IdP (SEC-02); the portal governs what those roles mean inside the platform.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from eadip.gateway.authz import require
from eadip.gateway.dependencies import (
    AuditLogDep,
    BudgetLedgerDep,
    FlagServiceDep,
    MemoryAgentDep,
    NotificationInboxDep,
    PromptRegistryDep,
    ResidencyPolicyDep,
    RoutingPolicyDep,
    get_pdp,
)
from eadip.gateway.models import (
    BudgetItem,
    CanaryRequest,
    CreateRoleRequest,
    ErasureResponse,
    MemoryStatusItem,
    NotificationItem,
    PromptVersionItem,
    RecordPromptEvalRequest,
    RegisterPromptRequest,
    ResidencyItem,
    RoleItem,
    RoutingItem,
    SetBudgetRequest,
    SetFlagRequest,
    SetResidencyRequest,
    SetRoutingRequest,
)
from eadip.observability.logging import get_logger
from eadip.platform.models import ModelTier, PromptVersion, TaskKind
from eadip.platform.prompts import PromptError
from eadip.platform.routing import RoutingGuardrailError
from eadip.security.audit import make_event
from eadip.security.identity import Identity
from eadip.security.rbac import Effect, Permission, Role

router = APIRouter(prefix="/v1/admin", tags=["admin"])
log = get_logger(__name__)

AdminReadDep = Annotated[Identity, Depends(require("admin", "platform", Effect.READ))]
AdminWriteDep = Annotated[Identity, Depends(require("admin", "platform", Effect.WRITE))]
# Erasure is destructive and irreversible — the high-impact tier.
AdminEraseDep = Annotated[Identity, Depends(require("admin", "platform", Effect.HIGH_IMPACT))]

_BUILTIN_ROLES = frozenset({"analyst", "approver", "viewer", "compliance", "admin"})


def _prompt_item(v: PromptVersion) -> PromptVersionItem:
    return PromptVersionItem(
        name=v.name,
        version=v.version,
        stage=str(v.stage),
        content_sha=v.content_sha,
        eval_score=v.eval_score,
        eval_source=v.eval_source,
        canary_fraction=v.canary_fraction,
        created_by=v.created_by,
    )


async def _audit(audit: AuditLogDep, identity: Identity, action: str, detail: dict) -> None:
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action=action,
            detail=detail,
        )
    )


# --- roles (self-serve RBAC, FR-057) ------------------------------------------
@router.get("/roles", response_model=list[RoleItem])
async def list_roles(identity: AdminReadDep) -> list[RoleItem]:
    """The live role catalog: what each role grants (resource/domain/effect)."""
    return [
        RoleItem(
            name=role.name,
            permissions=sorted(f"{p.resource}/{p.domain}/{p.effect}" for p in role.permissions),
        )
        for role in get_pdp().catalog.all()
    ]


@router.post("/roles", status_code=status.HTTP_201_CREATED, response_model=RoleItem)
async def create_role(
    body: CreateRoleRequest, identity: AdminWriteDep, audit: AuditLogDep
) -> RoleItem:
    """Define a custom role from explicit grants. Built-in roles are immutable;
    deny-by-default is preserved (a role only ever adds grants)."""
    if body.name in _BUILTIN_ROLES:
        raise HTTPException(status_code=409, detail=f"role '{body.name}' is built-in")
    permissions: set[Permission] = set()
    for triple in body.permissions:
        parts = triple.split("/")
        if len(parts) != 3:
            raise HTTPException(
                status_code=422, detail=f"grant '{triple}' is not resource/domain/effect"
            )
        resource, domain, effect = parts
        try:
            permissions.add(Permission(resource, domain, Effect(effect)))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown effect '{effect}'") from None
    role = Role(name=body.name, permissions=frozenset(permissions))
    get_pdp().catalog.upsert(role)
    await _audit(audit, identity, "admin.role.create", {"role": body.name})
    return RoleItem(
        name=role.name,
        permissions=sorted(f"{p.resource}/{p.domain}/{p.effect}" for p in role.permissions),
    )


# --- prompt lifecycle (FR-052, US-D2) ------------------------------------------
@router.get("/prompts", response_model=list[PromptVersionItem])
async def list_prompts(
    identity: AdminReadDep, prompts: PromptRegistryDep
) -> list[PromptVersionItem]:
    return [_prompt_item(v) for v in await prompts.overview()]


@router.post("/prompts", status_code=status.HTTP_201_CREATED, response_model=PromptVersionItem)
async def register_prompt(
    body: RegisterPromptRequest,
    identity: AdminWriteDep,
    prompts: PromptRegistryDep,
    audit: AuditLogDep,
) -> PromptVersionItem:
    version = await prompts.register(body.name, body.content, actor=str(identity.user_id))
    await _audit(
        audit, identity, "admin.prompt.register", {"name": body.name, "version": version.version}
    )
    return _prompt_item(version)


@router.post("/prompts/{name:path}/versions/{version}/eval", response_model=PromptVersionItem)
async def record_prompt_eval(
    name: str,
    version: int,
    body: RecordPromptEvalRequest,
    identity: AdminWriteDep,
    prompts: PromptRegistryDep,
    audit: AuditLogDep,
) -> PromptVersionItem:
    try:
        pv = await prompts.record_eval(name, version, score=body.score, source=body.source)
    except PromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await _audit(
        audit,
        identity,
        "admin.prompt.eval",
        {"name": name, "version": version, "score": body.score, "source": body.source},
    )
    return _prompt_item(pv)


@router.post("/prompts/{name:path}/versions/{version}/canary", response_model=PromptVersionItem)
async def start_prompt_canary(
    name: str,
    version: int,
    body: CanaryRequest,
    identity: AdminWriteDep,
    prompts: PromptRegistryDep,
    audit: AuditLogDep,
) -> PromptVersionItem:
    try:
        pv = await prompts.start_canary(name, version, fraction=body.fraction)
    except PromptError as exc:
        code = 404 if "not found" in str(exc) else 409  # eval gate refusal
        raise HTTPException(status_code=code, detail=str(exc)) from None
    await _audit(
        audit,
        identity,
        "admin.prompt.canary",
        {"name": name, "version": version, "fraction": body.fraction},
    )
    return _prompt_item(pv)


@router.post("/prompts/{name:path}/versions/{version}/promote", response_model=PromptVersionItem)
async def promote_prompt(
    name: str,
    version: int,
    identity: AdminWriteDep,
    prompts: PromptRegistryDep,
    audit: AuditLogDep,
) -> PromptVersionItem:
    try:
        pv = await prompts.promote(name, version)
    except PromptError as exc:
        code = 404 if "not found" in str(exc) else 409  # eval gate refusal
        raise HTTPException(status_code=code, detail=str(exc)) from None
    await _audit(audit, identity, "admin.prompt.promote", {"name": name, "version": version})
    return _prompt_item(pv)


@router.post("/prompts/{name:path}/rollback", response_model=PromptVersionItem)
async def rollback_prompt(
    name: str,
    identity: AdminWriteDep,
    prompts: PromptRegistryDep,
    audit: AuditLogDep,
) -> PromptVersionItem:
    """One-click rollback: reinstate the most recently active prior version."""
    try:
        pv = await prompts.rollback(name)
    except PromptError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await _audit(
        audit, identity, "admin.prompt.rollback", {"name": name, "restored_version": pv.version}
    )
    return _prompt_item(pv)


@router.get("/prompts/{name:path}/resolve", response_model=PromptVersionItem)
async def resolve_prompt(
    name: str, identity: AdminReadDep, prompts: PromptRegistryDep, key: str = ""
) -> PromptVersionItem:
    """What the runtime would serve for `key` (shows canary bucketing)."""
    pv = await prompts.resolve(name, key=key)
    if pv is None:
        raise HTTPException(status_code=404, detail=f"prompt '{name}' has no active version")
    return _prompt_item(pv)


# --- model routing (FR-053, US-D3) ----------------------------------------------
@router.get("/routing", response_model=list[RoutingItem])
async def routing_table(identity: AdminReadDep, routing: RoutingPolicyDep) -> list[RoutingItem]:
    return [
        RoutingItem(
            task=str(c.task), tier=str(c.tier), model=c.model, pinned=c.pinned, reason=c.reason
        )
        for c in routing.table()
    ]


@router.put("/routing/{task}", response_model=RoutingItem)
async def set_routing(
    task: str,
    body: SetRoutingRequest,
    identity: AdminWriteDep,
    routing: RoutingPolicyDep,
    audit: AuditLogDep,
) -> RoutingItem:
    try:
        task_kind = TaskKind(task)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown task '{task}'") from None
    try:
        routing.set_tier(task_kind, ModelTier(body.tier))
    except RoutingGuardrailError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await _audit(audit, identity, "admin.routing.set", {"task": task, "tier": body.tier})
    choice = routing.route(task_kind)
    return RoutingItem(
        task=str(choice.task),
        tier=str(choice.tier),
        model=choice.model,
        pinned=choice.pinned,
        reason=choice.reason,
    )


# --- per-tenant budgets (FR-053) -------------------------------------------------
@router.get("/budgets", response_model=list[BudgetItem])
async def list_budgets(identity: AdminReadDep, budgets: BudgetLedgerDep) -> list[BudgetItem]:
    return [
        BudgetItem(
            tenant_id=b.tenant_id,
            monthly_cap_usd=b.monthly_cap_usd,
            spent_usd=round(b.spent_usd, 6),
            exhausted=b.exhausted,
        )
        for b in await budgets.all()
    ]


@router.get("/tenants/{tenant_id}/budget", response_model=BudgetItem)
async def tenant_budget(
    tenant_id: UUID, identity: AdminReadDep, budgets: BudgetLedgerDep
) -> BudgetItem:
    b = await budgets.status(tenant_id)
    return BudgetItem(
        tenant_id=b.tenant_id,
        monthly_cap_usd=b.monthly_cap_usd,
        spent_usd=round(b.spent_usd, 6),
        exhausted=b.exhausted,
    )


@router.put("/tenants/{tenant_id}/budget", response_model=BudgetItem)
async def set_tenant_budget(
    tenant_id: UUID,
    body: SetBudgetRequest,
    identity: AdminWriteDep,
    budgets: BudgetLedgerDep,
    audit: AuditLogDep,
) -> BudgetItem:
    b = await budgets.set_cap(tenant_id, body.monthly_cap_usd)
    await _audit(
        audit,
        identity,
        "admin.budget.set",
        {"target_tenant": str(tenant_id), "monthly_cap_usd": body.monthly_cap_usd},
    )
    return BudgetItem(
        tenant_id=b.tenant_id,
        monthly_cap_usd=b.monthly_cap_usd,
        spent_usd=round(b.spent_usd, 6),
        exhausted=b.exhausted,
    )


# --- feature flags (FR-057) ------------------------------------------------------
@router.get("/flags", response_model=dict[str, bool])
async def list_flags(identity: AdminReadDep, flags: FlagServiceDep) -> dict[str, bool]:
    return flags.all()


@router.put("/flags/{name}", response_model=dict[str, bool])
async def set_flag(
    name: str,
    body: SetFlagRequest,
    identity: AdminWriteDep,
    flags: FlagServiceDep,
    audit: AuditLogDep,
) -> dict[str, bool]:
    flags.set(name, body.enabled)
    await _audit(audit, identity, "admin.flag.set", {"flag": name, "enabled": body.enabled})
    return flags.all()


# --- long-term memory governance (FR-045) ----------------------------------------
@router.get("/memory/{tenant_id}", response_model=MemoryStatusItem)
async def memory_status(
    tenant_id: UUID, identity: AdminReadDep, memory: MemoryAgentDep
) -> MemoryStatusItem:
    if memory is None:
        raise HTTPException(status_code=409, detail="memory is disabled")
    episodic, semantic = await memory.status(tenant_id)
    return MemoryStatusItem(tenant_id=tenant_id, episodic=episodic, semantic=semantic)


@router.delete("/memory/{tenant_id}", response_model=ErasureResponse)
async def erase_memory(
    tenant_id: UUID,
    identity: AdminEraseDep,
    memory: MemoryAgentDep,
    audit: AuditLogDep,
) -> ErasureResponse:
    """Erasure cascade (GDPR): irreversibly drop all of a tenant's long-term
    memory across both tiers. High-impact — admin only, always audited."""
    if memory is None:
        raise HTTPException(status_code=409, detail="memory is disabled")
    episodic, semantic = await memory.forget_tenant(tenant_id)
    await _audit(
        audit,
        identity,
        "admin.memory.erase",
        {"target_tenant": str(tenant_id), "episodic": episodic, "semantic": semantic},
    )
    log.info("memory.erased", target_tenant=str(tenant_id), episodic=episodic, semantic=semantic)
    return ErasureResponse(tenant_id=tenant_id, episodic_erased=episodic, semantic_erased=semantic)


# --- data residency (Phase 12, PRD §21) ---------------------------------------------
@router.get("/tenants/{tenant_id}/residency", response_model=ResidencyItem)
async def tenant_residency(
    tenant_id: UUID, identity: AdminReadDep, residency: ResidencyPolicyDep
) -> ResidencyItem:
    return ResidencyItem(
        tenant_id=tenant_id,
        region=await residency.region_for(tenant_id),
        deployment_region=residency.deployment_region,
    )


@router.put("/tenants/{tenant_id}/residency", response_model=ResidencyItem)
async def set_tenant_residency(
    tenant_id: UUID,
    body: SetResidencyRequest,
    identity: AdminWriteDep,
    residency: ResidencyPolicyDep,
    audit: AuditLogDep,
) -> ResidencyItem:
    """Pin a tenant's data to a region. From that moment this deployment refuses
    the tenant's data-plane requests (451) unless its region matches the pin."""
    await residency.pin(tenant_id, body.region)
    await _audit(
        audit,
        identity,
        "admin.residency.set",
        {"target_tenant": str(tenant_id), "region": body.region},
    )
    return ResidencyItem(
        tenant_id=tenant_id,
        region=body.region,
        deployment_region=residency.deployment_region,
    )


# --- notification inbox (FR-056) ---------------------------------------------------
@router.get("/notifications/{tenant_id}", response_model=list[NotificationItem])
async def list_notifications(
    tenant_id: UUID, identity: AdminReadDep, inbox: NotificationInboxDep
) -> list[NotificationItem]:
    return [
        NotificationItem(
            id=n.id,
            kind=str(n.kind),
            severity=n.severity,
            title=n.title,
            body=n.body,
            run_id=n.run_id,
            delivered=n.delivered,
        )
        for n in inbox.list(tenant_id)
    ]
