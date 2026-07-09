"""Executors bridge the orchestrator to the Phase 4 retrieval and Phase 5
analytics engines, normalising results into findings with provenance."""

from __future__ import annotations

import importlib.util
from uuid import uuid4

import pytest

from eadip.adapters.hash_embedder import HashEmbedder
from eadip.adapters.memory_embedding_cache import InMemoryEmbeddingCache
from eadip.adapters.memory_vector_store import InMemoryVectorStore
from eadip.adapters.sparse_encoder import HashingSparseEncoder
from eadip.ingestion.models import IngestRequest
from eadip.ingestion.pipeline import DocumentPipeline
from eadip.orchestrator.executors import AnalyticsExecutor, RetrievalExecutor
from eadip.orchestrator.models import PlanStep
from eadip.orchestrator.state import RunState
from eadip.retrieval.multi_query import HeuristicQueryExpander
from eadip.retrieval.rerank import LexicalReranker
from eadip.retrieval.service import RetrievalService

_DUCKDB = importlib.util.find_spec("duckdb") is not None


async def test_retrieval_executor_produces_grounded_findings() -> None:
    vs = InMemoryVectorStore()
    tenant = uuid4()
    pipe = DocumentPipeline(
        embedder=HashEmbedder(dimension=128),
        sparse_encoder=HashingSparseEncoder(),
        cache=InMemoryEmbeddingCache(),
        vector_store=vs,
        chunk_max_chars=400,
        chunk_overlap=40,
    )
    await pipe.ingest(
        IngestRequest(
            tenant_id=tenant,
            raw=b"EMEA gross margin fell on higher COGS from a supplier price increase.",
            content_type="text/plain",
            source_ref="finance/emea",
        )
    )
    svc = RetrievalService(
        embedder=HashEmbedder(dimension=128),
        sparse_encoder=HashingSparseEncoder(),
        vector_store=vs,
        expander=HeuristicQueryExpander(),
        reranker=LexicalReranker(),
    )
    state = RunState(
        run_id=uuid4(), tenant_id=tenant, user_id=uuid4(), question="why did EMEA margin fall?"
    )
    step = PlanStep(
        id="retrieve-context",
        kind="retrieve",
        description="ground",
        params={"query": "EMEA margin COGS"},
    )

    result = await RetrievalExecutor(svc).execute(step, state)
    assert result.ok
    assert result.findings
    assert result.findings[0].source == "retrieval"
    assert result.findings[0].evidence[0].ref == "finance/emea"  # provenance pointer


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
async def test_analytics_executor_produces_findings_with_sql_provenance() -> None:
    from eadip.adapters.duckdb_warehouse import DuckDBWarehouse
    from eadip.analytics.factory import build_analytics_service
    from eadip.config.settings import Settings

    svc = build_analytics_service(Settings(), DuckDBWarehouse())
    state = RunState(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        question="why did EMEA gross margin fall?",
    )
    step = PlanStep(
        id="analyze-metric",
        kind="analyze",
        description="analyze",
        params={
            "question": "why did EMEA gross margin fall?",
            "metric": "margin",
            "filter_value": "EMEA",
        },
    )
    result = await AnalyticsExecutor(svc).execute(step, state)
    assert result.ok
    assert any("margin" in f.claim.lower() for f in result.findings)
    # Every analytics finding carries its SQL (provenance seed for Phase 7).
    assert all(f.evidence and f.evidence[0].ref for f in result.findings)
    # The correlation-not-causation flag is carried through from Phase 5.
    assert any(f.association_only for f in result.findings)
