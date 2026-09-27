"""Test doubles shared across suites (Glass Box).

``FakeModelClient`` satisfies the ``ModelClient`` port with scripted responses
so every LLM-backed seam (planner, interpreter, reflection, SQL generator, query
expander, recommender) is exercised without a network or a key.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from eadip.ports.model_client import ModelResponse


class FakeModelClient:
    """Returns scripted responses in order (the last one repeats), or whatever
    ``script`` callable returns for a prompt. Records every prompt/model.

    ``raise_on`` makes call number N (1-based) raise, to test transport faults.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        script: Callable[[str], str] | None = None,
        raise_on: int | None = None,
        model: str = "fake-model",
        cost_usd: float = 0.001,
    ) -> None:
        self._responses = list(responses or [])
        self._script = script
        self._raise_on = raise_on
        self._model = model
        self._cost = cost_usd
        self.prompts: list[str] = []
        self.models: list[str | None] = []
        self.calls = 0

    async def complete(
        self, prompt: str, *, model: str | None = None, **kwargs: Any
    ) -> ModelResponse:
        self.calls += 1
        self.prompts.append(prompt)
        self.models.append(model)
        if self._raise_on is not None and self.calls == self._raise_on:
            raise ConnectionError("fake model unreachable")
        if self._script is not None:
            text = self._script(prompt)
        elif self._responses:
            idx = min(self.calls - 1, len(self._responses) - 1)
            text = self._responses[idx]
        else:
            text = ""
        return ModelResponse(
            text=text,
            model=model or self._model,
            tokens_in=len(prompt.split()),
            tokens_out=len(text.split()),
            cost_usd=self._cost,
        )
