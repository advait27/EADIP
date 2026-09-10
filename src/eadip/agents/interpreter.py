"""Goal Interpreter (FR-002): natural-language question -> structured Goal.

Deterministic heuristic default (offline, testable) with an LLM seam behind the
same Protocol — mirrors the Phase 4/5 "deterministic default, real seam" rule.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

from eadip.orchestrator.models import Goal

if TYPE_CHECKING:
    from eadip.agents.llm import InstructionProvider

_METRIC_WORDS = (
    "margin",
    "revenue",
    "cogs",
    "opex",
    "cost",
    "churn",
    "growth",
    "profit",
    "spend",
)
_ENTITY_TOKENS = ("EMEA", "AMER", "APAC", "LATAM", "NA", "EU", "US", "UK")
_DEEP_WORDS = ("why", "driver", "drivers", "explain", "root cause", "compare", "decline", "fall")


class GoalInterpreter(Protocol):
    async def interpret(self, question: str) -> Goal: ...


class HeuristicGoalInterpreter:
    async def interpret(self, question: str) -> Goal:
        q = question.lower()
        metrics = [m for m in _METRIC_WORDS if m in q]
        entities = [t for t in _ENTITY_TOKENS if re.search(rf"\b{t}\b", question, re.IGNORECASE)]
        if any(w in q for w in _DEEP_WORDS):
            complexity = "deep"
        elif len(question.split()) <= 6:
            complexity = "simple"
        else:
            complexity = "standard"
        return Goal(
            objective=question.strip(),
            metrics=metrics,
            entities=entities,
            complexity=complexity,
        )


INTERPRETER_INSTRUCTION = (
    "Extract the analytical goal from the question as JSON with keys "
    '"objective" (string), "metrics" (string[]), "entities" (string[]), '
    '"time_range" (string|null), "complexity" ("simple"|"standard"|"deep").'
)


class LLMGoalInterpreter:
    """Production interpreter (lazy ModelClient). Falls back to the heuristic if
    the model output cannot be parsed into a Goal. The instruction is a governed
    prompt artifact (Phase 11): resolved per call, so a promote/rollback in the
    registry applies immediately without a redeploy."""

    def __init__(
        self,
        client: object,
        model: str | None = None,
        instruction_provider: InstructionProvider | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._instruction_provider = instruction_provider
        self._fallback = HeuristicGoalInterpreter()

    async def interpret(self, question: str) -> Goal:
        from eadip.agents.llm import complete_json, resolve_instruction

        instruction = await resolve_instruction(self._instruction_provider, INTERPRETER_INSTRUCTION)
        prompt = f"{instruction}\nQuestion: {question}"
        goal = await complete_json(
            self._client, prompt, model=self._model, validate=Goal.model_validate
        )
        return goal if goal is not None else await self._fallback.interpret(question)
