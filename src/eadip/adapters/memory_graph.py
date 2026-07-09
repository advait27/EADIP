"""In-memory knowledge graph (Phase 10) — the deterministic reference store.

Holds a per-tenant node/edge set and answers a bounded, breadth-first
neighbourhood query around a seed metric (both edge directions), applying an
ACL-tag visibility filter. This is what the unit tests and the offline GraphRAG
demo run against; the Neo4j adapter implements the same port for production.
Fully in-process — no external dependency.
"""

from __future__ import annotations

from collections import deque
from uuid import UUID

from eadip.ports.graph import GraphEdge, GraphNode, Subgraph


class InMemoryKnowledgeGraph:
    def __init__(self) -> None:
        # tenant -> node key -> node ; tenant -> list of edges
        self._nodes: dict[UUID, dict[str, GraphNode]] = {}
        self._edges: dict[UUID, list[GraphEdge]] = {}

    async def upsert_lineage(
        self, tenant_id: UUID, nodes: list[GraphNode], edges: list[GraphEdge]
    ) -> None:
        tnodes = self._nodes.setdefault(tenant_id, {})
        for n in nodes:
            tnodes[n.key] = n  # upsert by business key (uniqueness per tenant+key)
        tedges = self._edges.setdefault(tenant_id, [])
        existing = {(e.src, e.dst, e.kind) for e in tedges}
        for e in edges:
            if (e.src, e.dst, e.kind) not in existing:
                tedges.append(e)
                existing.add((e.src, e.dst, e.kind))

    async def neighborhood(
        self,
        tenant_id: UUID,
        seed: str,
        *,
        hops: int,
        acl_tags: tuple[str, ...] = (),
    ) -> Subgraph:
        nodes = self._nodes.get(tenant_id, {})
        if seed not in nodes or not self._visible(nodes[seed], acl_tags):
            return Subgraph(seed=seed)

        edges = self._edges.get(tenant_id, [])
        # Adjacency in both directions (lineage is naturally traversed upstream).
        adj: dict[str, list[tuple[str, GraphEdge]]] = {}
        for e in edges:
            adj.setdefault(e.src, []).append((e.dst, e))
            adj.setdefault(e.dst, []).append((e.src, e))

        seen_nodes: dict[str, GraphNode] = {seed: nodes[seed]}
        seen_edges: list[GraphEdge] = []
        edge_ids: set[tuple[str, str, str]] = set()
        # BFS bounded to `hops` levels.
        frontier: deque[tuple[str, int]] = deque([(seed, 0)])
        visited = {seed}
        while frontier:
            key, depth = frontier.popleft()
            if depth >= hops:
                continue
            for neighbor, edge in adj.get(key, []):
                node = nodes.get(neighbor)
                if node is None or not self._visible(node, acl_tags):
                    continue  # unknown or not visible to this caller (ACL)
                eid = (edge.src, edge.dst, str(edge.kind))
                if eid not in edge_ids:
                    seen_edges.append(edge)
                    edge_ids.add(eid)
                if neighbor not in visited:
                    visited.add(neighbor)
                    seen_nodes[neighbor] = node
                    frontier.append((neighbor, depth + 1))

        return Subgraph(
            seed=seed,
            nodes=tuple(seen_nodes.values()),
            edges=tuple(seen_edges),
        )

    async def node_count(self, tenant_id: UUID) -> int:
        return len(self._nodes.get(tenant_id, {}))

    @staticmethod
    def _visible(node: GraphNode, acl_tags: tuple[str, ...]) -> bool:
        # A node with no acl_tags is public within the tenant; otherwise the
        # caller must hold at least one of the node's tags (any-match, AP-5).
        return not node.acl_tags or bool(set(node.acl_tags) & set(acl_tags))
