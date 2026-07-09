"""Per-tenant data residency (Phase 12, PRD §21, GDPR Ch V / data-localisation).

Each deployment declares its region (`deployment_region`); a tenant may be
pinned to a region through the admin portal. A pinned tenant's data-plane
requests are only served by deployments in that region — a request landing
anywhere else is refused with **451** (Unavailable For Legal Reasons) before any
data is touched. Unpinned tenants are served wherever they land (their data
simply lives in the deployment's region).

Enforcement is a gateway dependency on the data-plane routes (runs, search,
analytics, tools) — reads and writes alike. Replicating data *to* the pinned
region is the storage layer's job (per-region clusters; see the DR runbook);
this guard guarantees no cross-region serving at the application layer.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class ResidencyStore(Protocol):
    async def get(self, tenant_id: UUID) -> str | None: ...

    async def set(self, tenant_id: UUID, region: str) -> None: ...

    async def all(self) -> dict[UUID, str]: ...


class InMemoryResidencyStore:
    def __init__(self) -> None:
        self._by_tenant: dict[UUID, str] = {}

    async def get(self, tenant_id: UUID) -> str | None:
        return self._by_tenant.get(tenant_id)

    async def set(self, tenant_id: UUID, region: str) -> None:
        self._by_tenant[tenant_id] = region

    async def all(self) -> dict[UUID, str]:
        return dict(self._by_tenant)


class ResidencyPolicy:
    def __init__(self, store: ResidencyStore, *, deployment_region: str) -> None:
        self._store = store
        self._region = deployment_region

    @property
    def deployment_region(self) -> str:
        return self._region

    async def region_for(self, tenant_id: UUID) -> str | None:
        """The tenant's pinned region, or None (unpinned — served anywhere)."""
        return await self._store.get(tenant_id)

    async def pin(self, tenant_id: UUID, region: str) -> None:
        await self._store.set(tenant_id, region)

    async def allows(self, tenant_id: UUID) -> bool:
        """May THIS deployment serve the tenant's data? Unpinned -> yes; pinned
        -> only when the pin matches the deployment's region."""
        pinned = await self._store.get(tenant_id)
        return pinned is None or pinned == self._region

    async def assignments(self) -> dict[UUID, str]:
        return await self._store.all()
