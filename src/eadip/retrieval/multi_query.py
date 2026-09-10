"""Multi-query expansion (FR-012).

A complex question is expanded into several sub-queries whose results are fused,
improving recall. The heuristic expander is deterministic (no model call); the
LLM expander uses a ModelClient in production.
"""

from __future__ import annotations

from typing import Protocol

from eadip.ports.model_client import ModelClient
from eadip.retrieval.text import content_tokens


class QueryExpander(Protocol):
    async def expand(self, text: str) -> list[str]: ...


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if item.strip() and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


class HeuristicQueryExpander:
    """Original query + a content-keyword variant. Deterministic."""

    def __init__(self, max_variants: int = 3) -> None:
        self._max = max_variants

    async def expand(self, text: str) -> list[str]:
        variants = [text]
        keywords = content_tokens(text)
        if keywords:
            variants.append(" ".join(keywords))
        return _dedupe(variants)[: self._max]


class LLMQueryExpander:
    """Ask a model for paraphrases/sub-questions (production)."""

    def __init__(
        self, model_client: ModelClient, *, max_variants: int = 3, model: str | None = None
    ) -> None:
        self._client = model_client
        self._max = max_variants
        self._model = model

    async def expand(self, text: str) -> list[str]:
        prompt = (
            "Rewrite the question into up to "
            f"{self._max - 1} alternative search queries, one per line. "
            f"Question: {text}"
        )
        try:
            response = await self._client.complete(prompt, model=self._model)
        except Exception:  # noqa: BLE001 — model unreachable -> original query only
            return [text]
        variants = [text, *[line.strip("-• ").strip() for line in response.text.splitlines()]]
        return _dedupe(variants)[: self._max]
