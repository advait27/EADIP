"""Model-client port (AP-10). Concrete impls live in eadip.adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class ModelResponse:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@runtime_checkable
class ModelClient(Protocol):
    """Provider-agnostic completion client; routing/cost accounting belong here."""

    async def complete(
        self, prompt: str, *, model: str | None = None, **kwargs: Any
    ) -> ModelResponse: ...
