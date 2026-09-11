"""Production embedder via LiteLLM (FR-016).

Provider-agnostic embeddings behind the Embedder port. `litellm` is imported
lazily. The dimension is discovered from the first response and cached.
"""

from __future__ import annotations

from eadip.ports.embeddings import DenseVector

# Known output dimensions for common models; otherwise inferred at first call.
_KNOWN_DIMS = {
    "voyage/voyage-3": 1024,
    "voyage/voyage-3-lite": 512,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


class LiteLLMEmbedder:
    def __init__(self, model: str) -> None:
        self.model_version = model
        self.dimension = _KNOWN_DIMS.get(model, 0)

    async def embed(self, texts: list[str]) -> list[DenseVector]:
        import litellm

        response = await litellm.aembedding(model=self.model_version, input=texts)
        vectors: list[DenseVector] = [item["embedding"] for item in response["data"]]
        if self.dimension == 0 and vectors:
            self.dimension = len(vectors[0])
        return vectors
