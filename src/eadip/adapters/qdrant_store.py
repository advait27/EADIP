"""Qdrant vector store: per-tenant collections, dense + sparse, payload-filtered.

Implements the TAD Ch 4.5 layout: a collection per tenant with named `dense`
(cosine) and `bm25` sparse vectors, HNSW tuning, and payload indexes on
`acl_tags` + `valid_from` so identity/time filters apply pre-ANN (AP-5).
`qdrant_client` is imported lazily.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from eadip.ports.embeddings import DenseVector, SparseVector
from eadip.ports.vector_store import ScoredPoint, SearchFilter, VectorPoint


class QdrantVectorStore:
    def __init__(self, url: str, prefix: str = "kb") -> None:
        self._url = url
        self._prefix = prefix
        self._client: Any | None = None

    def collection_name(self, tenant_id: UUID) -> str:
        return f"{self._prefix}_{tenant_id.hex}"

    def _client_or_connect(self) -> Any:
        if self._client is None:
            from qdrant_client import AsyncQdrantClient

            self._client = AsyncQdrantClient(url=self._url)
        return self._client

    async def ensure_collection(self, tenant_id: UUID, *, dimension: int) -> str:
        from qdrant_client import models

        name = self.collection_name(tenant_id)
        client = self._client_or_connect()
        if not await client.collection_exists(name):
            await client.create_collection(
                collection_name=name,
                vectors_config={
                    "dense": models.VectorParams(size=dimension, distance=models.Distance.COSINE)
                },
                sparse_vectors_config={
                    "bm25": models.SparseVectorParams(index=models.SparseIndexParams())
                },
                hnsw_config=models.HnswConfigDiff(m=32, ef_construct=256),
            )
            await client.create_payload_index(
                name, field_name="acl_tags", field_schema=models.PayloadSchemaType.KEYWORD
            )
            await client.create_payload_index(
                name, field_name="valid_from", field_schema=models.PayloadSchemaType.DATETIME
            )
        return name

    async def upsert(self, tenant_id: UUID, points: list[VectorPoint]) -> None:
        from qdrant_client import models

        structs = []
        for point in points:
            vector: dict[str, Any] = {"dense": point.dense}
            if point.sparse is not None:
                vector["bm25"] = models.SparseVector(
                    indices=point.sparse.indices, values=point.sparse.values
                )
            structs.append(
                models.PointStruct(id=str(point.id), vector=vector, payload=point.payload)
            )
        await self._client_or_connect().upsert(
            collection_name=self.collection_name(tenant_id), points=structs, wait=True
        )

    async def count(self, tenant_id: UUID) -> int:
        result = await self._client_or_connect().count(
            collection_name=self.collection_name(tenant_id)
        )
        return int(result.count)

    def _build_filter(self, flt: SearchFilter) -> Any:
        """Translate the SearchFilter into a Qdrant filter (applied pre-ANN).

        Mirrors InMemoryVectorStore._visible: empty acl_tags = public; tagged
        points require an any-match; nulls are open-ended for the time window.
        """
        from qdrant_client import models

        must: list[Any] = []

        # ACL: point has no acl_tags (public) OR matches one of the caller's tags.
        acl_should: list[Any] = [
            models.IsEmptyCondition(is_empty=models.PayloadField(key="acl_tags"))
        ]
        if flt.acl_tags:
            acl_should.append(
                models.FieldCondition(key="acl_tags", match=models.MatchAny(any=list(flt.acl_tags)))
            )
        must.append(models.Filter(should=acl_should))

        if flt.sources:
            must.append(
                models.Filter(
                    should=[
                        models.FieldCondition(
                            key="source_type", match=models.MatchAny(any=list(flt.sources))
                        ),
                        models.FieldCondition(
                            key="source_ref", match=models.MatchAny(any=list(flt.sources))
                        ),
                    ]
                )
            )

        if flt.as_of is not None:
            # qdrant-client's DatetimeRange takes a datetime/date directly.
            as_of = flt.as_of
            must.append(
                models.Filter(
                    should=[
                        models.IsEmptyCondition(is_empty=models.PayloadField(key="valid_from")),
                        models.FieldCondition(
                            key="valid_from", range=models.DatetimeRange(lte=as_of)
                        ),
                    ]
                )
            )
            must.append(
                models.Filter(
                    should=[
                        models.IsEmptyCondition(is_empty=models.PayloadField(key="valid_to")),
                        models.FieldCondition(key="valid_to", range=models.DatetimeRange(gt=as_of)),
                    ]
                )
            )

        return models.Filter(must=must)

    def _to_scored(self, points: list[Any]) -> list[ScoredPoint]:
        return [
            ScoredPoint(id=UUID(str(p.id)), score=float(p.score), payload=p.payload or {})
            for p in points
        ]

    async def search_dense(
        self, tenant_id: UUID, *, vector: DenseVector, flt: SearchFilter, limit: int
    ) -> list[ScoredPoint]:
        result = await self._client_or_connect().query_points(
            collection_name=self.collection_name(tenant_id),
            query=vector,
            using="dense",
            query_filter=self._build_filter(flt),
            limit=limit,
            with_payload=True,
        )
        return self._to_scored(result.points)

    async def search_sparse(
        self, tenant_id: UUID, *, vector: SparseVector, flt: SearchFilter, limit: int
    ) -> list[ScoredPoint]:
        from qdrant_client import models

        result = await self._client_or_connect().query_points(
            collection_name=self.collection_name(tenant_id),
            query=models.SparseVector(indices=vector.indices, values=vector.values),
            using="bm25",
            query_filter=self._build_filter(flt),
            limit=limit,
            with_payload=True,
        )
        return self._to_scored(result.points)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
