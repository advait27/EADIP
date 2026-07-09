"""Neo4j knowledge graph: lineage round-trips, bounded traversal, and tenant
isolation via the (tenant_id, key) uniqueness constraint (Phase 10). Skipped
when no Neo4j is reachable; CI runs it."""

from __future__ import annotations

import importlib.util
import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from eadip.graph.meta_model import DEMO_EDGES, DEMO_NODES

_NEO4J = importlib.util.find_spec("neo4j") is not None
URI = os.environ.get("EADIP_TEST_NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("EADIP_TEST_NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("EADIP_TEST_NEO4J_PASSWORD", "neo4jpassword")

pytestmark = pytest.mark.skipif(not _NEO4J, reason="neo4j (graph extra) not installed")


@pytest_asyncio.fixture
async def graph():  # type: ignore[no-untyped-def]
    from eadip.adapters.neo4j_graph import Neo4jKnowledgeGraph

    g = Neo4jKnowledgeGraph(URI, user=USER, password=PASSWORD)
    try:
        await g._ensure_constraints()  # also verifies connectivity
    except Exception:
        pytest.skip("Neo4j not available for integration tests")
    # Clean slate: remove any demo-lineage nodes left by a prior run. Tests use
    # fresh random tenants, so this only touches this suite's data.
    driver = g._driver_or_connect()
    keys = [n.key for n in DEMO_NODES]
    async with driver.session() as s:
        await s.run("MATCH (n) WHERE n.key IN $keys DETACH DELETE n", keys=keys)
    yield g
    await g.close()


async def _tenant() -> UUID:
    return uuid4()


async def test_lineage_round_trips_and_traverses(graph) -> None:  # type: ignore[no-untyped-def]
    tenant = await _tenant()
    await graph.upsert_lineage(tenant, list(DEMO_NODES), list(DEMO_EDGES))

    sub = await graph.neighborhood(tenant, "gross_margin", hops=2)
    keys = {n.key for n in sub.nodes}
    assert {"gross_margin", "revenue", "cogs"} <= keys
    assert "emea_hw_cogs_spike_2026q2" in keys  # the impacting event, 2 hops out


async def test_upsert_is_idempotent_via_constraint(graph) -> None:  # type: ignore[no-untyped-def]
    tenant = await _tenant()
    await graph.upsert_lineage(tenant, list(DEMO_NODES), list(DEMO_EDGES))
    n1 = await graph.node_count(tenant)
    await graph.upsert_lineage(tenant, list(DEMO_NODES), list(DEMO_EDGES))  # re-ingest
    assert await graph.node_count(tenant) == n1  # (tenant, key) uniqueness holds


async def test_tenant_isolation(graph) -> None:  # type: ignore[no-untyped-def]
    ta, tb = await _tenant(), await _tenant()
    await graph.upsert_lineage(ta, list(DEMO_NODES), list(DEMO_EDGES))
    assert await graph.node_count(tb) == 0
    assert (await graph.neighborhood(tb, "gross_margin", hops=2)).is_empty
