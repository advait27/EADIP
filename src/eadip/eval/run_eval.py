"""CLI entrypoint for the offline eval gate (`eadip-eval`).

Phase 7 (Alpha) made this **live and graded**: it runs the real orchestrated +
verified investigation pipeline over the gold set and grades the produced
executive brief. Phase 12 makes it a **hard gate** (PRD §13-14, TAD Ch 12):
non-zero exit — failing CI — unless

  - accuracy          >= 92%  (gold-set pass rate)
  - checked coverage  >= 95%  (ANALYTICS claims whose label rested on comparing
                               a recomputed value with the claimed one —
                               ``value_checked``; a reproduced query with no
                               comparison does not count)
  - grounding         >= 95%  (verified claims carrying a provenance pointer —
                               a provenance check, not a correctness check)

Retrieval and tool claims are verified on their source pointer, not
value-checked; they are reported as a separate count ("grounded by pointer")
and are not part of checked coverage. "Checked" means the recorded SQL was
re-run on the same warehouse and the number recomputed — reproducibility and
arithmetic, not that the query answers the question.

Deterministic and offline (heuristic agents, in-memory stores, in-process
DuckDB). Requires the `data` extra (DuckDB).
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from eadip.adapters.duckdb_warehouse import DuckDBWarehouse
from eadip.adapters.memory_vector_store import InMemoryVectorStore
from eadip.analytics.factory import build_analytics_service
from eadip.config.settings import get_settings
from eadip.eval.harness import run_eval
from eadip.graph.factory import build_graph_service, build_knowledge_graph
from eadip.mcp.factory import build_mcp_manager, build_tool_executor
from eadip.orchestrator.checkpoint import InMemoryCheckpointer
from eadip.orchestrator.factory import build_orchestrator
from eadip.orchestrator.state import RunState
from eadip.retrieval.factory import build_retrieval_service
from eadip.security.policy import PolicyDecisionPoint
from eadip.security.rbac import default_catalog
from eadip.verification.models import VerificationStatus, VerifiedClaim


@dataclass
class VerificationStats:
    """Aggregated over every claim the gold-set runs produced."""

    total_claims: int = 0
    verified: int = 0
    analytics_claims: int = 0
    value_checked: int = 0  # analytics claims labelled on a value comparison
    pointer_grounded: int = 0  # retrieval/tool claims VERIFIED on a source pointer
    verified_with_provenance: int = 0
    briefs: int = 0
    briefs_missing: int = 0

    def add(self, claim: VerifiedClaim) -> None:
        self.total_claims += 1
        if claim.source == "analytics":
            self.analytics_claims += 1
            if claim.value_checked:
                self.value_checked += 1
        elif claim.status is VerificationStatus.VERIFIED:
            self.pointer_grounded += 1
        if claim.status is VerificationStatus.VERIFIED:
            self.verified += 1
            if claim.provenance:
                self.verified_with_provenance += 1

    @property
    def coverage(self) -> float:
        """Checked coverage: share of ANALYTICS claims whose label rested on a
        comparison of a recomputed value with the claimed one."""
        return self.value_checked / self.analytics_claims if self.analytics_claims else 0.0

    @property
    def grounding(self) -> float:
        """Share of VERIFIED claims that carry provenance (a provenance check:
        a pointer exists, not that the claim is correct)."""
        return self.verified_with_provenance / self.verified if self.verified else 0.0


@dataclass
class _Collector:
    stats: VerificationStats = field(default_factory=VerificationStats)


async def _investigate(question: str, collector: _Collector) -> str:
    """Run one full investigation and return the verified brief as gradable text.
    Includes the Phase 8 governed-tool executor (real MCP path) and the Phase 10
    GraphRAG service, so tool-calling and lineage gold cases exercise the real
    pipeline; verification stats are collected for the Phase 12 gates."""
    settings = get_settings()
    vector_store = InMemoryVectorStore()
    manager = build_mcp_manager(settings, PolicyDecisionPoint(default_catalog()))
    graph = build_graph_service(settings, build_knowledge_graph(settings))
    orchestrator = build_orchestrator(
        settings,
        retrieval_service=build_retrieval_service(settings, vector_store, graph=graph),
        analytics_service=build_analytics_service(settings, DuckDBWarehouse()),
        warehouse=DuckDBWarehouse(),
        checkpointer=InMemoryCheckpointer(),
        tool_executor=build_tool_executor(settings, manager),
    )
    # Runs execute as an analyst: read-only tools auto-authorize, writes stay gated.
    state = RunState(
        run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question=question, acl_tags=("analyst",)
    )
    await orchestrator.run(state)

    stats = collector.stats
    for claim in state.verified_claims:
        stats.add(claim)

    brief = state.brief
    if brief is None:
        stats.briefs_missing += 1
        return "no verified brief produced"
    stats.briefs += 1
    parts = [brief.headline]
    parts += [c.claim for c in brief.key_findings]
    parts += [r.action for r in brief.recommendations]
    # Include the verified drill-down claims (grounding + governed tool results)
    # so the graded text reflects the full brief, not only the exec layer.
    parts += [str(c.get("claim", "")) for c in brief.drill_down.get("claims", [])]
    return "\n".join(parts)


async def _main(min_accuracy: float, min_coverage: float, min_grounding: float) -> int:
    collector = _Collector()

    async def answer(question: str) -> str:
        return await _investigate(question, collector)

    report = await run_eval(answer, gold_set="alpha")
    stats = collector.stats
    print(
        f"eval gold-set alpha (live, verified pipeline): {report.passed}/{report.total} passed, "
        f"mean score {report.mean_score:.2f} (pass rate {report.pass_rate:.0%})"
    )
    print(
        f"  verification: {stats.total_claims} claims — checked coverage {stats.coverage:.0%} "
        f"({stats.value_checked}/{stats.analytics_claims} analytics claims value-compared), "
        f"{stats.pointer_grounded} retrieval/tool claims grounded by pointer (not value-checked), "
        f"grounding {stats.grounding:.0%} (verified claims with a provenance pointer), "
        f"briefs {stats.briefs}/{report.total}"
    )
    breaches: list[str] = []
    if report.pass_rate < min_accuracy:
        breaches.append(f"accuracy {report.pass_rate:.0%} < {min_accuracy:.0%}")
    if stats.coverage < min_coverage:
        breaches.append(f"checked coverage {stats.coverage:.0%} < {min_coverage:.0%}")
    if stats.grounding < min_grounding:
        breaches.append(f"grounding {stats.grounding:.0%} < {min_grounding:.0%}")
    if breaches:
        for b in breaches:
            print(f"  GATE BREACH: {b}")
        return 1
    print(
        f"  GATE PASS: accuracy >= {min_accuracy:.0%}, checked coverage >= {min_coverage:.0%}, "
        f"grounding >= {min_grounding:.0%}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="EADIP eval hard gate (PRD §13-14)")
    parser.add_argument(
        "--min-accuracy", type=float, default=0.92, help="minimum gold-set pass rate"
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.95,
        help="minimum checked coverage: share of analytics claims whose label rested on "
        "comparing a recomputed value with the claimed one",
    )
    parser.add_argument(
        "--min-grounding",
        type=float,
        default=0.95,
        help="minimum share of verified claims carrying a provenance pointer "
        "(a provenance check, not a correctness check)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.min_accuracy, args.min_coverage, args.min_grounding)))


if __name__ == "__main__":
    main()
