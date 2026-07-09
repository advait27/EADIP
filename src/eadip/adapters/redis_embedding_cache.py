"""Redis-backed embedding cache (TAD Ch 14.1).

Caches text->vector by content hash so repeated content costs no model calls.
`redis.asyncio` is imported lazily. Vectors are stored as JSON with a TTL.
"""

from __future__ import annotations

import json
from typing import Any

from eadip.ports.embeddings import DenseVector


class RedisEmbeddingCache:
    def __init__(self, url: str, *, ttl_s: int = 7 * 24 * 3600) -> None:
        self._url = url
        self._ttl = ttl_s
        self._client: Any | None = None

    def _redis(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    async def get_many(self, keys: list[str]) -> dict[str, DenseVector]:
        if not keys:
            return {}
        raw = await self._redis().mget(keys)
        out: dict[str, DenseVector] = {}
        for key, value in zip(keys, raw, strict=True):
            if value is not None:
                out[key] = json.loads(value)
        return out

    async def put_many(self, vectors: dict[str, DenseVector]) -> None:
        if not vectors:
            return
        pipe = self._redis().pipeline()
        for key, vec in vectors.items():
            pipe.set(key, json.dumps(vec), ex=self._ttl)
        await pipe.execute()
