from __future__ import annotations

from eadip.adapters.hash_embedder import HashEmbedder
from eadip.adapters.memory_embedding_cache import InMemoryEmbeddingCache
from eadip.adapters.sparse_encoder import HashingSparseEncoder
from eadip.ports.embeddings import cache_key


async def test_hash_embedder_is_deterministic_and_unit_norm() -> None:
    emb = HashEmbedder(dimension=64)
    a = await emb.embed(["margin fell in emea"])
    b = await emb.embed(["margin fell in emea"])
    assert a == b
    assert len(a[0]) == 64
    norm = sum(v * v for v in a[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-6


async def test_hash_embedder_distinguishes_text() -> None:
    emb = HashEmbedder(dimension=64)
    vecs = await emb.embed(["revenue growth", "supplier risk"])
    assert vecs[0] != vecs[1]


def test_sparse_encoder_deterministic_and_in_range() -> None:
    enc = HashingSparseEncoder(num_buckets=1024)
    [sv] = enc.encode(["margin margin revenue"])
    assert sv.indices == sorted(sv.indices)
    assert all(0 <= i < 1024 for i in sv.indices)
    # "margin" appears twice -> a value of 2.0 somewhere.
    assert 2.0 in sv.values


async def test_cache_roundtrip() -> None:
    cache = InMemoryEmbeddingCache()
    key = cache_key("hash-v1", "hello")
    assert await cache.get_many([key]) == {}
    await cache.put_many({key: [0.1, 0.2]})
    assert await cache.get_many([key]) == {key: [0.1, 0.2]}
