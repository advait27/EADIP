"""Evidence graph (Glass Box): a run's reasoning as nodes and edges.

A pure function over :class:`RunState` — no I/O — so the same graph renders
live (fed by the SSE stream), on a shared replay, and in tests. Node ids are
stable strings so a client can merge incremental updates:

    goal · step:<id> · finding:<n> · claim:<n> · evidence:<sha1[:12]> · rec:<n>

Edges carry a ``relation``: ``plans`` (goal→step), ``produces`` (step→finding,
via ``Finding.step_id``), ``cites`` (finding→evidence), ``verifies``
(claim→finding, joined on claim text + first provenance ref) and ``supports``
(recommendation→claim, via ``Recommendation.based_on``).
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

from eadip.orchestrator.state import RunState


class GraphNode(BaseModel):
    id: str
    kind: str  # "question" | "goal" | "step" | "finding" | "claim" | "evidence" | "recommendation"
    label: str
    data: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str  # "asks" | "plans" | "produces" | "cites" | "verifies" | "supports"


class EvidenceGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def node_ids(self) -> set[str]:
        return {n.id for n in self.nodes}


def evidence_id(kind: str, ref: str) -> str:
    return "evidence:" + hashlib.sha1(f"{kind}:{ref}".encode()).hexdigest()[:12]  # noqa: S324 - id, not security


def _clip(text: str, n: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def build_evidence_graph(state: RunState) -> EvidenceGraph:  # noqa: C901 - one pass, many kinds
    graph = EvidenceGraph()
    seen: set[str] = set()

    def add(node: GraphNode) -> None:
        if node.id not in seen:
            seen.add(node.id)
            graph.nodes.append(node)

    def link(source: str, target: str, relation: str) -> None:
        graph.edges.append(GraphEdge(source=source, target=target, relation=relation))

    add(
        GraphNode(
            id="question",
            kind="question",
            label=_clip(state.question),
            data={"question": state.question, "status": str(state.status)},
        )
    )
    if state.goal is not None:
        add(
            GraphNode(
                id="goal",
                kind="goal",
                label=_clip(state.goal.objective),
                data=state.goal.model_dump(),
            )
        )
        link("question", "goal", "asks")

    if state.plan is not None:
        for step in state.plan.steps:
            add(
                GraphNode(
                    id=f"step:{step.id}",
                    kind="step",
                    label=_clip(step.description),
                    data={
                        "step_id": step.id,
                        "step_kind": step.kind,
                        "status": step.status,
                        "depends_on": list(step.depends_on),
                        "completed": step.id in state.completed_step_ids,
                    },
                )
            )
            if state.goal is not None:
                link("goal", f"step:{step.id}", "plans")

    # Findings, keyed for the claim join: (claim text, first provenance ref).
    finding_key: dict[tuple[str, str], str] = {}
    for n, finding in enumerate(state.findings):
        fid = f"finding:{n}"
        first_ref = finding.evidence[0].ref if finding.evidence else ""
        finding_key.setdefault((finding.claim, first_ref), fid)
        add(
            GraphNode(
                id=fid,
                kind="finding",
                label=_clip(finding.claim),
                data={
                    "claim": finding.claim,
                    "source": finding.source,
                    "finding_kind": finding.kind,
                    "magnitude": finding.magnitude,
                    "association_only": finding.association_only,
                    "step_id": finding.step_id,
                },
            )
        )
        if finding.step_id and f"step:{finding.step_id}" in seen:
            link(f"step:{finding.step_id}", fid, "produces")
        for ev in finding.evidence:
            eid = evidence_id(ev.kind, ev.ref)
            add(
                GraphNode(
                    id=eid,
                    kind="evidence",
                    label=_clip(ev.snippet or ev.ref, 60),
                    data={"evidence_kind": ev.kind, "ref": ev.ref, "snippet": ev.snippet},
                )
            )
            link(fid, eid, "cites")

    claim_by_text: dict[str, str] = {}
    for n, claim in enumerate(state.verified_claims):
        cid = f"claim:{n}"
        claim_by_text.setdefault(claim.claim, cid)
        add(
            GraphNode(
                id=cid,
                kind="claim",
                label=_clip(claim.claim),
                data={
                    "claim": claim.claim,
                    "index": n,
                    "status": str(claim.status),
                    "method": claim.method,
                    "confidence": claim.confidence,
                    "source": claim.source,
                    "claim_kind": claim.kind,
                    "claimed_magnitude": claim.claimed_magnitude,
                    "recomputed_magnitude": claim.recomputed_magnitude,
                    "provenance": list(claim.provenance),
                    "note": claim.note,
                },
            )
        )
        first_ref = claim.provenance[0] if claim.provenance else ""
        target = finding_key.get((claim.claim, first_ref))
        if target is not None:
            link(cid, target, "verifies")

    if state.brief is not None:
        for n, rec in enumerate(state.brief.recommendations):
            rid = f"rec:{n}"
            add(
                GraphNode(
                    id=rid,
                    kind="recommendation",
                    label=_clip(rec.action),
                    data=rec.model_dump(),
                )
            )
            for based in rec.based_on:
                supported = claim_by_text.get(based)
                if supported is not None:
                    link(rid, supported, "supports")
    return graph
