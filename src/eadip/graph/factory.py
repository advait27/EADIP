"""Build the GraphRAG service from settings (Phase 10).

The default is the in-process reference graph, seeded per tenant on first use
with the demo finance lineage (dev) so a `deep` investigation has lineage to
traverse with no external store. The Neo4j backend implements the same port and
is selected by settings; its lineage is ingested via the same `upsert_lineage`
path a real extraction pipeline would use.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from eadip.config.settings import Settings
from eadip.graph.meta_model import DEMO_EDGES, DEMO_NODES
from eadip.graph.service import GraphRAGService
from eadip.ports.graph import KnowledgeGraph
from eadip.retrieval.models import Passage


def build_knowledge_graph(settings: Settings) -> KnowledgeGraph:
    if settings.graph_backend == "neo4j":
        from eadip.adapters.neo4j_graph import Neo4jKnowledgeGraph

        return Neo4jKnowledgeGraph(
            settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password.get_secret_value(),
        )
    from eadip.adapters.memory_graph import InMemoryKnowledgeGraph

    return InMemoryKnowledgeGraph()


async def seed_demo_lineage(graph: KnowledgeGraph, tenant_id: UUID) -> None:
    """Ingest the demo finance lineage for a tenant (idempotent — MERGE/upsert)."""
    await graph.upsert_lineage(tenant_id, list(DEMO_NODES), list(DEMO_EDGES))


def build_graph_service(settings: Settings, graph: KnowledgeGraph) -> GraphRAGService:
    seed: Callable[[UUID], Awaitable[None]] | None = None
    if settings.graph_seed_demo_lineage:

        async def _seed(tenant_id: UUID) -> None:
            await seed_demo_lineage(graph, tenant_id)

        seed = _seed
    return SeedingGraphRAGService(
        graph,
        hops=settings.graph_max_hops,
        max_passages=settings.graph_max_passages,
        seed=seed,
    )


class SeedingGraphRAGService(GraphRAGService):
    """GraphRAGService that lazily seeds a tenant's demo lineage on first use, so
    the reference graph has lineage to traverse without an explicit ingest step
    (dev). In production, lineage is ingested ahead of time and no seed runs."""

    def __init__(
        self,
        graph: KnowledgeGraph,
        *,
        hops: int = 2,
        max_passages: int = 6,
        seed: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(graph, hops=hops, max_passages=max_passages)
        self._graph_store = graph
        self._seed = seed
        self._seeded: set[UUID] = set()

    async def evidence(
        self,
        tenant_id: UUID,
        question: str,
        *,
        acl_tags: tuple[str, ...] = (),
        seed: str | None = None,
    ) -> list[Passage]:
        await self._ensure_seeded(tenant_id)
        return await super().evidence(tenant_id, question, acl_tags=acl_tags, seed=seed)

    async def _ensure_seeded(self, tenant_id: UUID) -> None:
        if self._seed is None or tenant_id in self._seeded:
            return
        if await self._graph_store.node_count(tenant_id) == 0:
            await self._seed(tenant_id)
        self._seeded.add(tenant_id)
