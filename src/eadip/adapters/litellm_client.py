"""LiteLLM-backed model client (FR-053 seed).

Provides the provider-agnostic seam and per-call token/cost accounting (AP-9,
NFR-10). The full per-task routing policy arrives in Phase 11. `litellm` is
imported lazily so importing this module (and running unit tests) is cheap.
"""

from __future__ import annotations

from typing import Any

from eadip.observability.logging import get_logger
from eadip.ports.model_client import ModelResponse

log = get_logger(__name__)


class LiteLLMClient:
    def __init__(self, default_model: str, api_base: str | None = None) -> None:
        self._default_model = default_model
        self._api_base = api_base

    async def complete(
        self, prompt: str, *, model: str | None = None, **kwargs: Any
    ) -> ModelResponse:
        import litellm

        chosen = model or self._default_model
        resp = await litellm.acompletion(
            model=chosen,
            messages=[{"role": "user", "content": prompt}],
            api_base=self._api_base,
            **kwargs,
        )
        usage = getattr(resp, "usage", None)
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
        try:
            cost = float(litellm.completion_cost(completion_response=resp))
        except Exception:  # noqa: BLE001 — cost is best-effort, never fatal
            cost = 0.0
        text = resp.choices[0].message.content or ""
        log.info(
            "model.complete",
            model=chosen,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )
        return ModelResponse(
            text=text,
            model=chosen,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )
