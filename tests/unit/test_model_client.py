from __future__ import annotations

from typing import Any

from eadip.ports.model_client import ModelClient, ModelResponse


class FakeModelClient:
    async def complete(
        self, prompt: str, *, model: str | None = None, **kwargs: Any
    ) -> ModelResponse:
        return ModelResponse(
            text=f"echo: {prompt}",
            model=model or "fake",
            tokens_in=1,
            tokens_out=2,
            cost_usd=0.0001,
        )


def test_fake_conforms_to_port() -> None:
    assert isinstance(FakeModelClient(), ModelClient)


async def test_complete_returns_response() -> None:
    resp = await FakeModelClient().complete("hello", model="x")
    assert resp.text == "echo: hello"
    assert resp.model == "x"
    assert resp.cost_usd > 0
