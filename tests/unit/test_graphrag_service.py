"""GraphRAG service + linearization (Phase 10): seed detection, bounded traversal
to evidence passages with graph provenance, and the impact edge ranked first."""

from __future__ import annotations

from uuid import UUID

from eadip.adapters.memory_graph import InMemoryKnowledgeGraph
from eadip.graph.linearize import linearize
from eadip.graph.meta_model import DEMO_EDGES, DEMO_NODES
from eadip.graph.service import GraphRAGService
from eadip.ports.graph import EdgeKind, GraphEdge, GraphNode, NodeKind, Subgraph

T = UUID("cccccccc-0000-4000-8000-000000000003")


async def _svc() -> GraphRAGService:
    g = InMemoryKnowledgeGraph()
    await g.upsert_lineage(T, list(DEMO_NODES), list(DEMO_EDGES))
    return GraphRAGService(g, hops=2)


def test_seed_detection_prefers_longest_alias() -> None:
    svc = GraphRAGService(InMemoryKnowledgeGraph())
    assert svc.seed_for("why did gross margin fall?") == "gross_margin"
    assert svc.seed_for("what happened to revenue?") == "revenue"
    assert svc.seed_for("tell me about the weather") is None


async def test_evidence_surfaces_impacting_event_first() -> None:
    svc = await _svc()
    passages = await svc.evidence(T, "why did gross margin fall?")
    assert passages
    top = passages[0]
    assert top.source_type == "graph"
    assert top.source_ref == "graph:gross_margin"
    assert "COGS spike" in top.text and "220" in top.text  # the decisive "why"
    # scores are monotonically non-increasing (impact ranked highest).
    assert all(passages[i].score >= passages[i + 1].score for i in range(len(passages) - 1))


async def test_no_metric_in_question_yields_no_evidence() -> None:
    svc = await _svc()
    assert await svc.evidence(T, "who is on the finance team?") == []


async def test_evidence_respects_hop_and_passage_bounds() -> None:
    g = InMemoryKnowledgeGraph()
    await g.upsert_lineage(T, list(DEMO_NODES), list(DEMO_EDGES))
    svc = GraphRAGService(g, hops=2, max_passages=2)
    passages = await svc.evidence(T, "why did gross margin fall?")
    assert len(passages) <= 2


def test_linearize_empty_subgraph() -> None:
    assert linearize(Subgraph(seed="x")) == []


def test_linearize_impact_sentence() -> None:
    nodes = (
        GraphNode("gross_margin", NodeKind.METRIC, "Gross Margin"),
        GraphNode("cogs", NodeKind.METRIC, "COGS"),
        GraphNode("evt", NodeKind.EVENT, "Cost event", {"period": "2026-Q2"}),
    )
    edges = (
        GraphEdge("gross_margin", "cogs", EdgeKind.DERIVED_FROM, direction=-1),
        GraphEdge("evt", "cogs", EdgeKind.IMPACTS, weight=220, direction=-1),
    )
    lines = linearize(Subgraph(seed="gross_margin", nodes=nodes, edges=edges))
    assert any("impacted COGS" in ln and "lowering Gross Margin by 220" in ln for ln in lines)
    assert any("derived from COGS (-)" in ln for ln in lines)
