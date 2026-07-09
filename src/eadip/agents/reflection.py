"""Reflection agent (FR-006, AP-7): is the evidence sufficient, or re-plan?

The heuristic checks coverage: every goal metric should be explained by an
analytics finding, the answer should be grounded by at least one retrieval
finding, and no step may have failed. Unmet coverage becomes gaps the Planner
turns into follow-up steps (the reflection loop). Deterministic and testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from eadip.orchestrator.models import Finding, Goal, Reflection, StepResult

if TYPE_CHECKING:
    from eadip.agents.llm import InstructionProvider


class ReflectionAgent(Protocol):
    async def reflect(
        self, goal: Goal, findings: list[Finding], step_results: list[StepResult]
    ) -> Reflection: ...


class HeuristicReflection:
    async def reflect(
        self, goal: Goal, findings: list[Finding], step_results: list[StepResult]
    ) -> Reflection:
        analytics = [f for f in findings if f.source == "analytics"]
        retrieval = [f for f in findings if f.source == "retrieval"]
        failed = [r for r in step_results if not r.ok]

        gaps: list[str] = []
        if goal.metrics and not analytics:
            gaps.append("no metric analysis produced")
        if not retrieval:
            gaps.append("no grounding evidence retrieved")
        for m in goal.metrics:
            if analytics and not any(m.lower() in f.claim.lower() for f in analytics):
                gaps.append(f"metric not explained: {m}")
        if failed:
            gaps.append(f"{len(failed)} step(s) failed")

        sufficient = not gaps
        rationale = "evidence covers the goal" if sufficient else "; ".join(gaps)
        return Reflection(
            sufficient=sufficient,
            gaps=gaps,
            should_replan=not sufficient,
            rationale=rationale,
        )


REFLECTION_INSTRUCTION = (
    "Given the goal and the findings, decide if the evidence is "
    'sufficient. Reply JSON {"sufficient":bool,"gaps":string[],'
    '"should_replan":bool,"rationale":string}.'
)


class LLMReflection:
    def __init__(
        self,
        client: object,
        model: str | None = None,
        instruction_provider: InstructionProvider | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._instruction_provider = instruction_provider
        self._fallback = HeuristicReflection()

    async def reflect(
        self, goal: Goal, findings: list[Finding], step_results: list[StepResult]
    ) -> Reflection:
        from eadip.agents.llm import complete_json, resolve_instruction

        claims = [f.claim for f in findings]
        instruction = await resolve_instruction(self._instruction_provider, REFLECTION_INSTRUCTION)
        prompt = f"{instruction}\nGoal: {goal.model_dump_json()}\nFindings: {claims}"
        data = await complete_json(self._client, prompt, model=self._model)
        if data is None:
            return await self._fallback.reflect(goal, findings, step_results)
        try:
            return Reflection.model_validate(data)
        except Exception:  # noqa: BLE001
            return await self._fallback.reflect(goal, findings, step_results)
