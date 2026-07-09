"""Knowledge-graph port (Phase 10, TAD Ch 4.4/7.2, FR-013).

The graph encodes *metric lineage* explicitly — how a metric is derived from
other metrics, what dimensions slice it, and which events impacted it — so
structural "why" questions are answered by bounded traversal rather than
inference. This module defines the meta-model node/edge kinds, the value objects,
and the `KnowledgeGraph` port. Concrete stores (an in-memory reference graph and
a Neo4j adapter) live in `eadip.adapters` and are imported lazily.

Every read is tenant-scoped; ACL tags on nodes let identity-scoped filtering
apply to graph evidence exactly as it does to vector hits (AP-5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class NodeKind(StrEnum):
    METRIC = "Metric"
    DIMENSION = "Dimension"
    DATA_SOURCE = "DataSource"
    ENTITY = "Entity"
    EVENT = "Event"
    DOC = "Doc"
    TEAM = "Team"


class EdgeKind(StrEnum):
    DERIVED_FROM = "DERIVED_FROM"  # Metric <- Metric (lineage; carries weight/direction)
    SLICED_BY = "SLICED_BY"  # Metric -> Dimension
    IMPACTS = "IMPACTS"  # Event -> Metric (carries magnitude/direction)
    SOURCED_FROM = "SOURCED_FROM"  # Metric -> DataSource
    OWNED_BY = "OWNED_BY"  # Metric/DataSource -> Team
    MENTIONS = "MENTIONS"  # Doc -> Entity/Metric
    RELATES_TO = "RELATES_TO"  # generic Entity <-> Entity


@dataclass(frozen=True)
class GraphNode:
    key: str  # stable business key, unique per (tenant, kind), e.g. "gross_margin"
    kind: NodeKind
    label: str = ""  # human-readable name
    properties: dict[str, object] = field(default_factory=dict)
    acl_tags: tuple[str, ...] = ()  # identity-scoped visibility (AP-5)


@dataclass(frozen=True)
class GraphEdge:
    src: str  # source node key
    dst: str  # destination node key
    kind: EdgeKind
    # Lineage/impact edges carry a signed weight: magnitude of contribution and
    # direction (+1 raises the target metric, -1 lowers it).
    weight: float = 1.0
    direction: int = 1
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Subgraph:
    """A bounded neighbourhood extracted around a seed, ready to linearize."""

    seed: str
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()

    def node(self, key: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.key == key), None)

    @property
    def is_empty(self) -> bool:
        return not self.nodes


class KnowledgeGraph(Protocol):
    """Tenant-scoped metric-lineage graph. Writes ingest lineage; reads extract a
    bounded neighbourhood around a seed metric for GraphRAG."""

    async def upsert_lineage(
        self, tenant_id: UUID, nodes: list[GraphNode], edges: list[GraphEdge]
    ) -> None: ...

    async def neighborhood(
        self,
        tenant_id: UUID,
        seed: str,
        *,
        hops: int,
        acl_tags: tuple[str, ...] = (),
    ) -> Subgraph:
        """Bounded ``hops``-deep neighbourhood around ``seed`` (both edge
        directions), filtered to nodes the caller's ``acl_tags`` may see. Returns
        an empty subgraph when the seed is unknown."""
        ...

    async def node_count(self, tenant_id: UUID) -> int: ...
