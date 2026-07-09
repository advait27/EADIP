"""Demo metric-lineage meta-model (Phase 10), mirroring the demo finance star
schema (see adapters.demo_finance). It encodes explicitly what the warehouse data
only implies, so a structural "why did EMEA margin fall?" question is answered by
traversal:

    gross_margin  --DERIVED_FROM(+1)--  revenue
    gross_margin  --DERIVED_FROM(-1)--  cogs         (margin = revenue - cogs)
    gross_margin  --SLICED_BY--         region, product_line
    revenue/cogs  --SOURCED_FROM--      finance_warehouse   --OWNED_BY-- finance_team
    Event "EMEA Hardware COGS spike (2026-Q2)"  --IMPACTS(-1, magnitude)-->  cogs

Traversing gross_margin's neighbourhood surfaces the COGS driver and the event
that moved it — the lineage the vector store can't express. Deterministic and
offline; the same shape a real extraction pipeline would ingest.
"""

from __future__ import annotations

from eadip.ports.graph import EdgeKind, GraphEdge, GraphNode, NodeKind

# The event magnitude matches demo_finance: EMEA Hardware COGS 600 -> 820 in
# 2026-Q2, a +220 COGS move that lowers gross margin by 220.
_COGS_SPIKE = 220.0

DEMO_NODES: list[GraphNode] = [
    GraphNode("gross_margin", NodeKind.METRIC, "Gross Margin", {"formula": "revenue - cogs"}),
    GraphNode("revenue", NodeKind.METRIC, "Revenue"),
    GraphNode("cogs", NodeKind.METRIC, "Cost of Goods Sold"),
    GraphNode("region", NodeKind.DIMENSION, "Region"),
    GraphNode("product_line", NodeKind.DIMENSION, "Product Line"),
    GraphNode("finance_warehouse", NodeKind.DATA_SOURCE, "Finance Warehouse"),
    GraphNode("finance_team", NodeKind.TEAM, "Finance Team"),
    GraphNode(
        "emea_hw_cogs_spike_2026q2",
        NodeKind.EVENT,
        "EMEA Hardware COGS spike (2026-Q2)",
        {
            "period": "2026-Q2",
            "region": "EMEA",
            "product_line": "Hardware",
            "magnitude": _COGS_SPIKE,
            "note": "Hardware COGS rose from 600 to 820, lowering gross margin.",
        },
    ),
]

DEMO_EDGES: list[GraphEdge] = [
    GraphEdge("gross_margin", "revenue", EdgeKind.DERIVED_FROM, weight=1.0, direction=1),
    GraphEdge("gross_margin", "cogs", EdgeKind.DERIVED_FROM, weight=1.0, direction=-1),
    GraphEdge("gross_margin", "region", EdgeKind.SLICED_BY),
    GraphEdge("gross_margin", "product_line", EdgeKind.SLICED_BY),
    GraphEdge("revenue", "finance_warehouse", EdgeKind.SOURCED_FROM),
    GraphEdge("cogs", "finance_warehouse", EdgeKind.SOURCED_FROM),
    GraphEdge("gross_margin", "finance_team", EdgeKind.OWNED_BY),
    GraphEdge(
        "emea_hw_cogs_spike_2026q2",
        "cogs",
        EdgeKind.IMPACTS,
        weight=_COGS_SPIKE,
        direction=-1,  # raises COGS, which lowers margin
        properties={"period": "2026-Q2"},
    ),
]
