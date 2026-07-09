"""Linearize a lineage subgraph to natural-language evidence (Phase 10, TAD 7.2).

GraphRAG's bridge: a `Subgraph` is turned into short, provenance-bearing sentences
the rest of the pipeline treats like any other passage. Lineage becomes
"gross margin is derived from revenue (+) and cost of goods sold (-)"; an impact
edge becomes "<event> impacted cost of goods sold, lowering gross margin by 220".
Deterministic and ordered (impacts first — they answer "why" — then derivation,
then slicing) so the evidence reads coherently and tests are stable.
"""

from __future__ import annotations

from eadip.ports.graph import EdgeKind, GraphNode, Subgraph

_DIRECTION_WORD = {1: "raising", -1: "lowering"}
_SIGN = {1: "+", -1: "-"}


def _label(node: GraphNode | None, fallback: str) -> str:
    return node.label if node and node.label else fallback


def linearize(subgraph: Subgraph) -> list[str]:
    """Ordered evidence sentences describing the seed metric's lineage. Empty
    when the subgraph is empty (unknown seed)."""
    if subgraph.is_empty:
        return []

    seed_label = _label(subgraph.node(subgraph.seed), subgraph.seed)
    impacts: list[str] = []
    derivations: list[str] = []
    slices: list[str] = []
    sources: list[str] = []

    for edge in subgraph.edges:
        src = _label(subgraph.node(edge.src), edge.src)
        dst = _label(subgraph.node(edge.dst), edge.dst)
        if edge.kind is EdgeKind.IMPACTS:
            target = subgraph.node(edge.dst)
            effect = _DIRECTION_WORD.get(edge.direction, "affecting")
            mag = f" by {edge.weight:g}" if edge.weight else ""
            note = str((target.properties if target else {}).get("note", "")) if target else ""
            event = subgraph.node(edge.src)
            when = str((event.properties if event else {}).get("period", "")) if event else ""
            when_str = f" in {when}" if when else ""
            impacts.append(
                f"Event '{src}'{when_str} impacted {dst}, {effect} {seed_label}{mag}."
                + (f" {note}" if note else "")
            )
        elif edge.kind is EdgeKind.DERIVED_FROM:
            impacts_seed = edge.src == subgraph.seed
            if impacts_seed:
                derivations.append(
                    f"{src} is derived from {dst} ({_SIGN.get(edge.direction, '')})."
                )
        elif edge.kind is EdgeKind.SLICED_BY:
            slices.append(dst)
        elif edge.kind is EdgeKind.SOURCED_FROM:
            sources.append(f"{src} is sourced from {dst}.")

    lines: list[str] = list(impacts)
    if derivations:
        lines.extend(derivations)
    if slices:
        lines.append(f"{seed_label} is sliced by {', '.join(slices)}.")
    lines.extend(sources)
    return lines
