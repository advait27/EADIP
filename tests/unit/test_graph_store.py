"""In-memory knowledge graph (Phase 10): bounded multi-hop traversal, both edge
directions, ACL filtering, tenant isolation, and idempotent upsert."""

from __future__ import annotations

from uuid import UUID

from eadip.adapters.memory_graph import InMemoryKnowledgeGraph
from eadip.graph.meta_model import DEMO_EDGES, DEMO_NODES
from eadip.ports.graph import EdgeKind, GraphEdge, GraphNode, NodeKind

T = UUID("aaaaaaaa-0000-4000-8000-000000000001")


async def _seeded() -> InMemoryKnowledgeGraph:
    g = InMemoryKnowledgeGraph()
    await g.upsert_lineage(T, list(DEMO_NODES), list(DEMO_EDGES))
    return g


async def test_neighborhood_traverses_lineage_both_directions() -> None:
    g = await _seeded()
    sub = await g.neighborhood(T, "gross_margin", hops=2)
    keys = {n.key for n in sub.nodes}
    # 1 hop: revenue, cogs, region, product_line, finance_team; 2 hops: the event
    # (via cogs) and finance_warehouse (via revenue/cogs).
    assert {"gross_margin", "revenue", "cogs"} <= keys
    assert "emea_hw_cogs_spike_2026q2" in keys  # the impacting event (2 hops)
    assert "finance_warehouse" in keys


async def test_hop_bound_is_enforced() -> None:
    g = await _seeded()
    one = await g.neighborhood(T, "gross_margin", hops=1)
    keys = {n.key for n in one.nodes}
    assert "cogs" in keys  # 1 hop
    assert "emea_hw_cogs_spike_2026q2" not in keys  # 2 hops away -> excluded


async def test_unknown_seed_returns_empty() -> None:
    g = await _seeded()
    sub = await g.neighborhood(T, "nonexistent", hops=2)
    assert sub.is_empty


async def test_acl_filter_hides_restricted_nodes() -> None:
    g = InMemoryKnowledgeGraph()
    nodes = [
        GraphNode("m", NodeKind.METRIC, "M"),
        GraphNode("secret", NodeKind.EVENT, "Secret", acl_tags=("restricted",)),
    ]
    edges = [GraphEdge("secret", "m", EdgeKind.IMPACTS)]
    await g.upsert_lineage(T, nodes, edges)
    # Caller without the tag never traverses to the restricted node.
    open_sub = await g.neighborhood(T, "m", hops=2, acl_tags=())
    assert "secret" not in {n.key for n in open_sub.nodes}
    # Caller with the tag sees it.
    priv = await g.neighborhood(T, "m", hops=2, acl_tags=("restricted",))
    assert "secret" in {n.key for n in priv.nodes}


async def test_tenant_isolation() -> None:
    g = await _seeded()
    other = UUID("bbbbbbbb-0000-4000-8000-000000000002")
    assert await g.node_count(other) == 0
    assert (await g.neighborhood(other, "gross_margin", hops=2)).is_empty


async def test_upsert_is_idempotent() -> None:
    g = await _seeded()
    n1 = await g.node_count(T)
    await g.upsert_lineage(T, list(DEMO_NODES), list(DEMO_EDGES))  # re-ingest
    assert await g.node_count(T) == n1  # no duplicates
    sub = await g.neighborhood(T, "gross_margin", hops=1)
    derived = [e for e in sub.edges if e.kind is EdgeKind.DERIVED_FROM]
    assert len(derived) == 2  # revenue(+), cogs(-), not doubled
