"""Neo4j knowledge graph (Phase 10, TAD Ch 4.4) — the production lineage store.

Implements the same `KnowledgeGraph` port as the in-memory reference. Nodes are
labelled by kind and carry a `tenant_id` + business `key`; a uniqueness
constraint on (tenant_id, key) enforces the meta-model's identity. Reads are
tenant-scoped and bounded to `hops` via a variable-length Cypher path, with an
ACL filter applied in the query so a caller never traverses nodes they may not
see. `neo4j` is imported lazily (the `graph` extra); the in-memory store covers
offline/dev, and the integration test runs this in CI.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from eadip.ports.graph import EdgeKind, GraphEdge, GraphNode, NodeKind, Subgraph

# Uniqueness constraints (the meta-model's identity): one node per (tenant, key)
# per kind. Applied on connect; idempotent.
_CONSTRAINTS = [
    f"CREATE CONSTRAINT eadip_{kind.value.lower()}_key IF NOT EXISTS "
    f"FOR (n:{kind.value}) REQUIRE (n.tenant_id, n.key) IS UNIQUE"
    for kind in NodeKind
]


class Neo4jKnowledgeGraph:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._uri = uri
        self._auth = (user, password)
        self._driver: Any | None = None
        self._constraints_applied = False

    def _driver_or_connect(self) -> Any:
        if self._driver is None:
            from neo4j import AsyncGraphDatabase

            self._driver = AsyncGraphDatabase.driver(self._uri, auth=self._auth)
        return self._driver

    async def _ensure_constraints(self) -> None:
        if self._constraints_applied:
            return
        driver = self._driver_or_connect()
        async with driver.session() as session:
            for stmt in _CONSTRAINTS:
                await session.run(stmt)
        self._constraints_applied = True

    async def close(self) -> None:  # pragma: no cover - lifecycle
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def upsert_lineage(
        self, tenant_id: UUID, nodes: list[GraphNode], edges: list[GraphEdge]
    ) -> None:
        await self._ensure_constraints()
        driver = self._driver_or_connect()
        tid = str(tenant_id)
        async with driver.session() as session:
            for n in nodes:
                await session.run(
                    f"MERGE (x:{n.kind.value} {{tenant_id: $tid, key: $key}}) "
                    "SET x.label = $label, x.acl_tags = $acl, x += $props",
                    tid=tid,
                    key=n.key,
                    label=n.label,
                    acl=list(n.acl_tags),
                    props=_json_props(n.properties),
                )
            for e in edges:
                await session.run(
                    "MATCH (a {tenant_id: $tid, key: $src}) "
                    "MATCH (b {tenant_id: $tid, key: $dst}) "
                    f"MERGE (a)-[r:{e.kind.value}]->(b) "
                    "SET r.weight = $weight, r.direction = $direction, r += $props",
                    tid=tid,
                    src=e.src,
                    dst=e.dst,
                    weight=e.weight,
                    direction=e.direction,
                    props=_json_props(e.properties),
                )

    async def neighborhood(
        self,
        tenant_id: UUID,
        seed: str,
        *,
        hops: int,
        acl_tags: tuple[str, ...] = (),
    ) -> Subgraph:
        await self._ensure_constraints()
        driver = self._driver_or_connect()
        tid = str(tenant_id)
        # Bounded variable-length path in both directions; the ACL predicate keeps
        # a caller from traversing nodes they may not see (empty acl => public only).
        cypher = (
            "MATCH (seed {tenant_id: $tid, key: $seed}) "
            f"MATCH path = (seed)-[*0..{int(hops)}]-(m) "
            "WHERE all(n IN nodes(path) WHERE "
            "  n.tenant_id = $tid AND (size(coalesce(n.acl_tags, [])) = 0 "
            "  OR any(t IN n.acl_tags WHERE t IN $acl))) "
            "UNWIND nodes(path) AS n UNWIND relationships(path) AS r "
            "RETURN collect(DISTINCT n) AS ns, collect(DISTINCT r) AS rs"
        )
        async with driver.session() as session:
            result = await session.run(cypher, tid=tid, seed=seed, acl=list(acl_tags))
            record = await result.single()
        if record is None or not record["ns"]:
            return Subgraph(seed=seed)
        nodes = tuple(_node_from(n) for n in record["ns"])
        edges = tuple(_edge_from(r) for r in record["rs"])
        return Subgraph(seed=seed, nodes=nodes, edges=edges)

    async def node_count(self, tenant_id: UUID) -> int:
        driver = self._driver_or_connect()
        async with driver.session() as session:
            result = await session.run(
                "MATCH (n {tenant_id: $tid}) RETURN count(n) AS c", tid=str(tenant_id)
            )
            record = await result.single()
        return int(record["c"]) if record else 0


def _json_props(props: dict[str, object]) -> dict[str, object]:
    # Neo4j stores only primitives/arrays; keep property values scalar.
    return {k: v for k, v in props.items() if isinstance(v, (str, int, float, bool))}


def _node_from(node: Any) -> GraphNode:
    kind = next((k for k in NodeKind if k.value in node.labels), NodeKind.ENTITY)
    props = {
        k: v for k, v in dict(node).items() if k not in ("tenant_id", "key", "label", "acl_tags")
    }
    return GraphNode(
        key=node["key"],
        kind=kind,
        label=node.get("label", ""),
        properties=props,
        acl_tags=tuple(node.get("acl_tags", []) or ()),
    )


def _edge_from(rel: Any) -> GraphEdge:
    kind = next((k for k in EdgeKind if k.value == rel.type), EdgeKind.RELATES_TO)
    props = {k: v for k, v in dict(rel).items() if k not in ("weight", "direction")}
    return GraphEdge(
        src=rel.start_node["key"],
        dst=rel.end_node["key"],
        kind=kind,
        weight=float(rel.get("weight", 1.0)),
        direction=int(rel.get("direction", 1)),
        properties=props,
    )
