"""Goal Interpreter (FR-002): natural-language question -> structured Goal.

Deterministic heuristic default (offline, testable) with an LLM seam behind the
same Protocol — mirrors the Phase 4/5 "deterministic default, real seam" rule.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from eadip.orchestrator.models import Goal

if TYPE_CHECKING:
    from eadip.agents.llm import InstructionProvider
    from eadip.ingestion.datasets import DatasetVocabulary

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
    async def interpret(self, question: str, *, tenant_id: UUID | None = None) -> Goal: ...


def _mentioned(word: str, q: str) -> bool:
    return any(re.search(rf"\b{re.escape(p)}\b", q) for p in (word, word.replace("_", " ")))


def dataset_metrics(
    question: str, tenant_id: UUID | None, vocabulary: DatasetVocabulary | None
) -> list[str]:
    """Metrics implied by an uploaded dataset named in the question (Glass Box):
    the numeric columns it mentions, else the table's first numeric column so
    the plan still quantifies. Tenant-scoped: another tenant's tables are
    invisible to interpretation, exactly as they are to the safety gate."""
    if vocabulary is None or tenant_id is None:
        return []
    q = question.lower()
    out: list[str] = []
    for table, numeric in vocabulary.vocabulary(tenant_id):
        if not _mentioned(table, q) or not numeric:
            continue
        named = [c for c in numeric if _mentioned(c, q)]
        for m in named or [numeric[0]]:
            if m not in out:
                out.append(m)
    return out


class HeuristicGoalInterpreter:
    def __init__(self, vocabulary: DatasetVocabulary | None = None) -> None:
        self._vocabulary = vocabulary

    async def interpret(self, question: str, *, tenant_id: UUID | None = None) -> Goal:
        q = question.lower()
        metrics = [m for m in _METRIC_WORDS if m in q]
        for m in dataset_metrics(question, tenant_id, self._vocabulary):
            if m not in metrics:
                metrics.append(m)
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
        vocabulary: DatasetVocabulary | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._instruction_provider = instruction_provider
        self._vocabulary = vocabulary
        self._fallback = HeuristicGoalInterpreter(vocabulary)

    async def interpret(self, question: str, *, tenant_id: UUID | None = None) -> Goal:
        from eadip.agents.llm import complete_json, resolve_instruction

        instruction = await resolve_instruction(self._instruction_provider, INTERPRETER_INSTRUCTION)
        prompt = f"{instruction}\nQuestion: {question}"
        if self._vocabulary is not None and tenant_id is not None:
            tables = self._vocabulary.vocabulary(tenant_id)
            if tables:
                listed = "; ".join(f"{t}({', '.join(cols)})" for t, cols in tables)
                prompt += f"\nUploaded datasets the question may refer to: {listed}"
        goal = await complete_json(
            self._client, prompt, model=self._model, validate=Goal.model_validate
        )
        if goal is None:
            return await self._fallback.interpret(question, tenant_id=tenant_id)
        return goal
