"""Planner (FR-003): Goal (+ reflection gaps) -> an executable Plan.

The heuristic planner is deterministic: it always grounds the answer with a
retrieval step, adds an analytics step when the goal names a metric, adds a
governed *tool* step when the goal needs external data neither retrieval nor the
warehouse holds (Phase 8), and turns each reflection gap into a focused follow-up
retrieval. Steps carry `depends_on` so the Router can fan independent ones out in
parallel. The LLM planner fills the same Plan schema and falls back to the
heuristic on bad output.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

from eadip.orchestrator.models import Goal, Plan, PlanStep

if TYPE_CHECKING:
    from eadip.agents.llm import InstructionProvider

# Terms that signal external reference data a governed tool serves (Phase 8). The
# heuristic only adds a tool step on an explicit signal, so existing plans (and
# their tests) are unchanged when no tool is needed.
_TOOL_SIGNALS = ("currency", "exchange rate", "fx", "usd rate", "convert")
_CURRENCIES = ("EUR", "GBP", "JPY", "USD")
_CURRENCY_RE = re.compile(r"\b(" + "|".join(_CURRENCIES) + r")\b")

# Terms that signal a side-effecting remediation the operator asked for (Phase 9).
# The resulting tool step is a *write* — it pauses for human approval before it
# runs (governed autonomy). The heuristic never fabricates a write on its own.
_ACTION_SIGNALS = ("open a ticket", "open a remediation", "file a ticket", "raise a ticket")


def _slug(text: str, n: int = 24) -> str:
    keep = [c if c.isalnum() else "-" for c in text.lower()]
    return "".join(keep).strip("-")[:n] or "step"


def _detect_currency(text: str) -> str | None:
    """Extract a currency code the tool step can pass as a typed argument. The
    heuristic only calls a tool when it can supply its required arguments; an LLM
    planner (behind the same Plan schema) can fill richer arguments."""
    match = _CURRENCY_RE.search(text.upper())
    return match.group(1) if match else None


class Planner(Protocol):
    async def plan(self, goal: Goal, gaps: list[str]) -> Plan: ...


class HeuristicPlanner:
    async def plan(self, goal: Goal, gaps: list[str]) -> Plan:
        steps: list[PlanStep] = []
        entity = goal.entities[0] if goal.entities else None

        steps.append(
            PlanStep(
                id="retrieve-context",
                kind="retrieve",
                description=f"Retrieve grounding context for: {goal.objective}",
                params={"query": goal.objective, "effort": goal.complexity},
            )
        )
        if goal.metrics:
            steps.append(
                PlanStep(
                    id="analyze-metric",
                    kind="analyze",
                    description=f"Analyze {goal.metrics[0]} movement"
                    + (f" for {entity}" if entity else ""),
                    params={
                        "question": goal.objective,
                        "metric": goal.metrics[0],
                        "filter_value": entity,
                        "effort": goal.complexity,
                    },
                )
            )
        # External reference data (Phase 8): a governed tool step, added only on
        # an explicit signal *and* when we can supply the required argument. The
        # Tool Agent picks the tool; RBAC gates the call; the arg gate validates.
        currency = _detect_currency(goal.objective)
        if currency and any(sig in goal.objective.lower() for sig in _TOOL_SIGNALS):
            steps.append(
                PlanStep(
                    id="tool-external-data",
                    kind="tool",
                    description=f"Fetch the {currency} exchange rate (external reference data)",
                    params={
                        "intent": f"exchange rate currency fx {currency}",
                        "arguments": {"currency": currency},
                    },
                )
            )
        # Remediation the operator explicitly asked for (Phase 9): a side-effecting
        # tool step. It will pause for human approval before it runs — the engine
        # never executes a write autonomously.
        if any(sig in goal.objective.lower() for sig in _ACTION_SIGNALS):
            steps.append(
                PlanStep(
                    id="tool-open-ticket",
                    kind="tool",
                    description="Open a remediation ticket (write — requires approval)",
                    params={
                        "intent": "open a remediation ticket tracker",
                        "arguments": {
                            "title": f"Remediation for: {goal.objective}"[:200],
                            "priority": "high",
                        },
                    },
                )
            )
        # Reflection gaps become focused follow-up retrievals (the re-plan branch).
        for i, gap in enumerate(gaps):
            steps.append(
                PlanStep(
                    id=f"retrieve-gap-{i}-{_slug(gap)}",
                    kind="retrieve",
                    description=f"Retrieve evidence for gap: {gap}",
                    params={"query": gap},
                )
            )
        rationale = "ground with retrieval" + (", quantify with analytics" if goal.metrics else "")
        if any(s.kind == "tool" for s in steps):
            rationale += ", enrich with a governed tool"
        return Plan(steps=steps, rationale=rationale)


PLANNER_INSTRUCTION = (
    'Plan an investigation as JSON {"steps":[{"id","kind",'
    '"description","params","depends_on"}], "rationale"}. '
    'kind is "retrieve" or "analyze". Keep it minimal.'
)


class LLMPlanner:
    def __init__(
        self,
        client: object,
        model: str | None = None,
        instruction_provider: InstructionProvider | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._instruction_provider = instruction_provider
        self._fallback = HeuristicPlanner()

    async def plan(self, goal: Goal, gaps: list[str]) -> Plan:
        from eadip.agents.llm import (
            PRODUCED_BY_FALLBACK,
            PRODUCED_BY_LLM,
            complete_json_traced,
            log_fallback,
            resolve_instruction,
        )

        instruction = await resolve_instruction(self._instruction_provider, PLANNER_INSTRUCTION)
        goal_json = goal.model_dump_json(exclude={"produced_by", "fallback_reason"})
        prompt = f"{instruction}\nGoal: {goal_json}\nGaps to close: {gaps}"

        def validate(data: dict) -> Plan:
            plan = Plan.model_validate(data)
            if not plan.steps:
                raise ValueError("plan has no steps")
            if any(s.kind not in ("retrieve", "analyze", "tool") for s in plan.steps):
                raise ValueError("step kind must be retrieve, analyze or tool")
            return plan

        plan, reason = await complete_json_traced(
            self._client, prompt, model=self._model, validate=validate
        )
        if plan is None:
            reason = reason or "no model output"
            log_fallback("planner", reason)
            fallback = await self._fallback.plan(goal, gaps)
            return fallback.model_copy(
                update={"produced_by": PRODUCED_BY_FALLBACK, "fallback_reason": reason}
            )
        # Stamped after validation: a produced_by the model emitted is discarded.
        return plan.model_copy(update={"produced_by": PRODUCED_BY_LLM, "fallback_reason": None})
