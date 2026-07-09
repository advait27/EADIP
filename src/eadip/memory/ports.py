"""Memory store ports + in-memory adapters (Phase 11, FR-045, TAD Ch 10).

Episodic and semantic stores are tenant-scoped. The in-memory adapters are the
offline-deterministic reference (and dev default); a Postgres/pgvector-backed
store implements the same ports in production. Both support an **erasure
cascade** — `forget(tenant)` removes all of a tenant's memory (GDPR/erasure).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from eadip.memory.models import EpisodicMemory, SemanticFact


class EpisodicStore(Protocol):
    async def add(self, memory: EpisodicMemory) -> None: ...

    async def find(self, tenant_id: UUID, question_key: str) -> EpisodicMemory | None:
        """Return a prior episode whose question matches (exact normalised key)."""
        ...

    async def touch(self, tenant_id: UUID, memory_id: UUID) -> None:
        """Record a reuse hit (usefulness signal); tenant-scoped for RLS."""
        ...

    async def count(self, tenant_id: UUID) -> int: ...

    async def forget(self, tenant_id: UUID) -> int:
        """Erasure cascade: drop all of a tenant's episodes; return how many."""
        ...


class SemanticStore(Protocol):
    async def add(self, fact: SemanticFact) -> None: ...

    async def all(self, tenant_id: UUID) -> list[SemanticFact]: ...

    async def count(self, tenant_id: UUID) -> int: ...

    async def forget(self, tenant_id: UUID) -> int: ...


class InMemoryEpisodicStore:
    def __init__(self) -> None:
        self._by_tenant: dict[UUID, dict[str, EpisodicMemory]] = {}
        self._by_id: dict[UUID, EpisodicMemory] = {}

    async def add(self, memory: EpisodicMemory) -> None:
        self._by_tenant.setdefault(memory.tenant_id, {})[memory.question_key] = memory
        self._by_id[memory.id] = memory

    async def find(self, tenant_id: UUID, question_key: str) -> EpisodicMemory | None:
        return self._by_tenant.get(tenant_id, {}).get(question_key)

    async def touch(self, tenant_id: UUID, memory_id: UUID) -> None:
        mem = self._by_id.get(memory_id)
        if mem is not None and mem.tenant_id == tenant_id:
            mem.hits += 1

    async def count(self, tenant_id: UUID) -> int:
        return len(self._by_tenant.get(tenant_id, {}))

    async def forget(self, tenant_id: UUID) -> int:
        episodes = self._by_tenant.pop(tenant_id, {})
        for mem in episodes.values():
            self._by_id.pop(mem.id, None)
        return len(episodes)


class InMemorySemanticStore:
    def __init__(self) -> None:
        self._by_tenant: dict[UUID, list[SemanticFact]] = {}

    async def add(self, fact: SemanticFact) -> None:
        facts = self._by_tenant.setdefault(fact.tenant_id, [])
        # De-dup on the triple so consolidation is idempotent across repeat runs.
        key = (fact.subject, fact.predicate, fact.object)
        if not any((f.subject, f.predicate, f.object) == key for f in facts):
            facts.append(fact)

    async def all(self, tenant_id: UUID) -> list[SemanticFact]:
        return list(self._by_tenant.get(tenant_id, []))

    async def count(self, tenant_id: UUID) -> int:
        return len(self._by_tenant.get(tenant_id, []))

    async def forget(self, tenant_id: UUID) -> int:
        facts = self._by_tenant.pop(tenant_id, [])
        return len(facts)
