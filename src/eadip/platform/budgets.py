"""Per-tenant budget ledger (Phase 11, FR-053, US-D3).

Admins set a monthly cap per tenant; every finished run charges its actual cost.
An exhausted budget refuses *new* runs (429 at the gateway) — in-flight runs are
still bounded per-run by the Phase 6 cost ceiling, so worst-case overspend is one
run's cap. The in-memory store is the dev default; a Postgres store
(`tenant_budget`) sits behind the same port.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from eadip.platform.models import TenantBudget


class BudgetStore(Protocol):
    async def get(self, tenant_id: UUID) -> TenantBudget | None: ...

    async def save(self, budget: TenantBudget) -> None: ...

    async def all(self) -> list[TenantBudget]: ...


class InMemoryBudgetStore:
    def __init__(self) -> None:
        self._by_tenant: dict[UUID, TenantBudget] = {}

    async def get(self, tenant_id: UUID) -> TenantBudget | None:
        return self._by_tenant.get(tenant_id)

    async def save(self, budget: TenantBudget) -> None:
        self._by_tenant[budget.tenant_id] = budget

    async def all(self) -> list[TenantBudget]:
        return sorted(self._by_tenant.values(), key=lambda b: str(b.tenant_id))


class BudgetLedger:
    def __init__(self, store: BudgetStore) -> None:
        self._store = store

    async def status(self, tenant_id: UUID) -> TenantBudget:
        budget = await self._store.get(tenant_id)
        return budget if budget is not None else TenantBudget(tenant_id=tenant_id)

    async def set_cap(self, tenant_id: UUID, monthly_cap_usd: float) -> TenantBudget:
        budget = await self.status(tenant_id)
        budget.monthly_cap_usd = max(0.0, monthly_cap_usd)
        await self._store.save(budget)
        return budget

    async def charge(self, tenant_id: UUID, amount_usd: float) -> TenantBudget:
        """Record actual spend (called with the run's final cost)."""
        budget = await self.status(tenant_id)
        budget.spent_usd += max(0.0, amount_usd)
        await self._store.save(budget)
        return budget

    async def allows_new_run(self, tenant_id: UUID) -> bool:
        """False once the tenant's cap is exhausted (no cap = always allowed)."""
        return not (await self.status(tenant_id)).exhausted

    async def reset_spend(self, tenant_id: UUID) -> TenantBudget:
        """Start a new budget period (invoked by the monthly rollover job)."""
        budget = await self.status(tenant_id)
        budget.spent_usd = 0.0
        await self._store.save(budget)
        return budget

    async def all(self) -> list[TenantBudget]:
        return await self._store.all()
