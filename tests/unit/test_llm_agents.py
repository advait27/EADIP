"""The LLM-backed seams, exercised behind a fake model client (Glass Box).

Every agent must: use the model's output when it is well-formed, repair once
when the shape is wrong, and fall back to its deterministic heuristic when the
model is unreachable or keeps producing garbage. No network, no key.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from eadip.agents.interpreter import HeuristicGoalInterpreter, LLMGoalInterpreter
from eadip.agents.llm import (
    _extract_json,
    complete_json,
    complete_json_traced,
    resolve_instruction,
)
from eadip.agents.planner import HeuristicPlanner, LLMPlanner
from eadip.agents.reflection import LLMReflection
from eadip.analytics.models import AnalyticsRequest, QueryPlan
from eadip.analytics.sql_generator import LLMSqlGenerator
from eadip.orchestrator.models import Finding, Goal, StepResult
from eadip.retrieval.multi_query import LLMQueryExpander
from eadip.verification.models import VerificationStatus, VerifiedClaim
from eadip.verification.recommend import LLMRecommender
from tests.fakes import FakeModelClient

_PROVENANCE = {"produced_by", "fallback_reason"}


def _core(model: Any) -> dict[str, Any]:
    """An agent output minus its provenance stamp, to compare with the heuristic."""
    return model.model_dump(exclude=_PROVENANCE)


# --- complete_json ---------------------------------------------------------------


def test_extract_json_handles_fences_prose_and_garbage() -> None:
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('Sure! Here you go: {"a": {"b": [1, 2]}} hope it helps') == {
        "a": {"b": [1, 2]}
    }
    assert _extract_json("[1, 2, 3]") is None
    assert _extract_json("no json here") is None
    assert _extract_json("{not: valid}") is None


async def test_complete_json_returns_dict_without_validator() -> None:
    client = FakeModelClient(['{"x": 1}'])
    assert await complete_json(client, "p") == {"x": 1}
    assert client.prompts[0].startswith("p\n\nRespond with a single JSON object")


async def test_complete_json_repairs_once_on_bad_shape_then_succeeds() -> None:
    client = FakeModelClient(['{"objective": 5}', '{"objective": "fixed"}'])
    goal = await complete_json(client, "p", validate=Goal.model_validate)
    assert isinstance(goal, Goal) and goal.objective == "fixed"
    assert client.calls == 2
    assert "rejected" in client.prompts[1] and "Fix it" in client.prompts[1]


async def test_complete_json_gives_up_after_repair_budget() -> None:
    client = FakeModelClient(['{"objective": 5}'])  # repeats forever
    assert await complete_json(client, "p", validate=Goal.model_validate) is None
    assert client.calls == 2  # one repair, then None


async def test_complete_json_repairs_unparseable_output() -> None:
    client = FakeModelClient(["I cannot do that", '{"objective": "ok"}'])
    goal = await complete_json(client, "p", validate=Goal.model_validate)
    assert isinstance(goal, Goal) and client.calls == 2
    assert "not a single JSON object" in client.prompts[1]


async def test_complete_json_none_on_transport_error() -> None:
    assert await complete_json(FakeModelClient(raise_on=1), "p") is None


async def test_complete_json_reports_why_it_fell_back() -> None:
    reasons: list[str] = []
    assert await complete_json(FakeModelClient(raise_on=1), "p", on_fallback=reasons.append) is None
    assert len(reasons) == 1 and reasons[0].startswith("llm_error:")
    assert "unreachable" in reasons[0]

    reasons.clear()
    assert await complete_json(FakeModelClient(["nope"]), "p", on_fallback=reasons.append) is None
    assert len(reasons) == 1 and reasons[0].startswith("unparseable_json")

    reasons.clear()
    bad_shape = FakeModelClient(['{"objective": 5}'])
    shaped = await complete_json(
        bad_shape, "p", validate=Goal.model_validate, on_fallback=reasons.append
    )
    assert shaped is None
    assert len(reasons) == 1 and reasons[0].startswith("invalid_shape:")

    # Success never reports a fallback.
    reasons.clear()
    await complete_json(FakeModelClient(['{"x": 1}']), "p", on_fallback=reasons.append)
    assert reasons == []


async def test_complete_json_traced_returns_value_or_reason() -> None:
    ok = await complete_json_traced(
        FakeModelClient(['{"objective": "o"}']), "p", validate=Goal.model_validate
    )
    assert isinstance(ok[0], Goal) and ok[1] is None
    value, reason = await complete_json_traced(
        FakeModelClient(raise_on=1), "p", validate=Goal.model_validate
    )
    assert value is None and reason is not None and reason.startswith("llm_error:")


async def test_resolve_instruction_prefers_registry_and_survives_failure() -> None:
    async def ok() -> str | None:
        return "governed"

    async def empty() -> str | None:
        return None

    async def boom() -> str | None:
        raise RuntimeError("registry down")

    assert await resolve_instruction(None, "default") == "default"
    assert await resolve_instruction(ok, "default") == "governed"
    assert await resolve_instruction(empty, "default") == "default"
    assert await resolve_instruction(boom, "default") == "default"


# --- interpreter ----------------------------------------------------------------


async def test_llm_interpreter_uses_model_output_and_model_name() -> None:
    client = FakeModelClient(
        [
            json.dumps(
                {
                    "objective": "EMEA margin drop",
                    "metrics": ["gross_margin"],
                    "entities": ["EMEA"],
                    "complexity": "deep",
                }
            )
        ]
    )
    goal = await LLMGoalInterpreter(client, "strong-model").interpret("why?")
    assert goal.metrics == ["gross_margin"] and goal.complexity == "deep"
    assert client.models == ["strong-model"]
    assert goal.produced_by == "llm" and goal.fallback_reason is None


async def test_llm_interpreter_falls_back_to_heuristic() -> None:
    q = "Why did EMEA margin fall?"
    expected = await HeuristicGoalInterpreter().interpret(q)
    assert expected.produced_by == "heuristic" and expected.fallback_reason is None

    down = await LLMGoalInterpreter(FakeModelClient(raise_on=1)).interpret(q)
    assert _core(down) == _core(expected)
    assert down.produced_by == "heuristic_fallback"
    assert down.fallback_reason is not None and down.fallback_reason.startswith("llm_error:")

    garbage = await LLMGoalInterpreter(FakeModelClient(["garbage"])).interpret(q)
    assert _core(garbage) == _core(expected)
    assert garbage.produced_by == "heuristic_fallback"
    assert garbage.fallback_reason is not None
    assert garbage.fallback_reason.startswith("unparseable_json")


async def test_llm_interpreter_overwrites_model_emitted_provenance() -> None:
    # The model cannot launder a fallback (or anything else) into its own label.
    client = FakeModelClient(
        ['{"objective": "x", "produced_by": "heuristic", "fallback_reason": "made up"}']
    )
    goal = await LLMGoalInterpreter(client).interpret("q")
    assert goal.produced_by == "llm" and goal.fallback_reason is None


async def test_llm_interpreter_uses_governed_instruction() -> None:
    async def provider() -> str | None:
        return "GOVERNED PROMPT"

    client = FakeModelClient(['{"objective": "x"}'])
    await LLMGoalInterpreter(client, instruction_provider=provider).interpret("q")
    assert client.prompts[0].startswith("GOVERNED PROMPT")


# --- planner --------------------------------------------------------------------


async def test_llm_planner_accepts_a_valid_plan() -> None:
    plan_json = {
        "steps": [
            {"id": "r", "kind": "retrieve", "description": "get docs", "params": {"query": "q"}},
            {"id": "a", "kind": "analyze", "description": "numbers", "depends_on": ["r"]},
        ],
        "rationale": "ground then quantify",
    }
    plan = await LLMPlanner(FakeModelClient([json.dumps(plan_json)])).plan(Goal(objective="q"), [])
    assert [s.id for s in plan.steps] == ["r", "a"] and plan.steps[1].depends_on == ["r"]
    assert plan.produced_by == "llm" and plan.fallback_reason is None


async def test_llm_planner_overwrites_model_emitted_provenance() -> None:
    plan_json = {
        "steps": [{"id": "r", "kind": "retrieve", "description": "d"}],
        "produced_by": "heuristic",
        "fallback_reason": "made up",
    }
    plan = await LLMPlanner(FakeModelClient([json.dumps(plan_json)])).plan(Goal(objective="q"), [])
    assert plan.produced_by == "llm" and plan.fallback_reason is None


async def test_llm_planner_prompt_omits_goal_provenance() -> None:
    client = FakeModelClient(['{"steps": [{"id": "r", "kind": "retrieve", "description": "d"}]}'])
    goal = Goal(objective="q", produced_by="heuristic_fallback", fallback_reason="llm_error: x")
    await LLMPlanner(client).plan(goal, [])
    assert "produced_by" not in client.prompts[0] and "llm_error" not in client.prompts[0]


@pytest.mark.parametrize(
    "bad",
    ['{"steps": []}', '{"steps": [{"id": "x", "kind": "delete", "description": "d"}]}', "nope"],
)
async def test_llm_planner_rejects_bad_plans_and_falls_back(bad: str) -> None:
    goal = Goal(objective="Why did EMEA margin fall?", metrics=["margin"], entities=["EMEA"])
    expected = await HeuristicPlanner().plan(goal, [])
    assert expected.produced_by == "heuristic"
    plan = await LLMPlanner(FakeModelClient([bad])).plan(goal, [])
    assert _core(plan) == _core(expected)
    assert plan.produced_by == "heuristic_fallback" and plan.fallback_reason


async def test_llm_planner_falls_back_on_transport_error_with_reason() -> None:
    plan = await LLMPlanner(FakeModelClient(raise_on=1)).plan(Goal(objective="q"), [])
    assert plan.produced_by == "heuristic_fallback"
    assert plan.fallback_reason is not None and plan.fallback_reason.startswith("llm_error:")


# --- reflection -----------------------------------------------------------------


async def test_llm_reflection_uses_model_and_falls_back() -> None:
    goal = Goal(objective="q", metrics=["margin"])
    findings = [Finding(claim="margin fell", source="analytics")]
    results = [StepResult(step_id="a", kind="analyze", ok=True)]
    good = FakeModelClient(['{"sufficient": false, "gaps": ["need docs"], "should_replan": true}'])
    r = await LLMReflection(good).reflect(goal, findings, results)
    assert r.gaps == ["need docs"] and r.should_replan
    assert "margin fell" in good.prompts[0]
    assert r.produced_by == "llm" and r.fallback_reason is None
    r2 = await LLMReflection(FakeModelClient(["???"])).reflect(goal, findings, results)
    assert r2.sufficient is False and "no grounding evidence retrieved" in r2.gaps
    assert r2.produced_by == "heuristic_fallback"
    assert r2.fallback_reason is not None and r2.fallback_reason.startswith("unparseable_json")
    r3 = await LLMReflection(FakeModelClient(raise_on=1)).reflect(goal, findings, results)
    assert r3.produced_by == "heuristic_fallback"
    assert r3.fallback_reason is not None and r3.fallback_reason.startswith("llm_error:")


async def test_llm_reflection_overwrites_model_emitted_provenance() -> None:
    client = FakeModelClient(['{"sufficient": true, "produced_by": "heuristic_fallback"}'])
    r = await LLMReflection(client).reflect(Goal(objective="q"), [], [])
    assert r.produced_by == "llm" and r.fallback_reason is None


# --- SQL generator --------------------------------------------------------------


async def test_llm_sql_generator_strips_fences_and_feeds_back_repairs() -> None:
    from uuid import uuid4

    from eadip.adapters.demo_finance import FINANCE_SCHEMA

    client = FakeModelClient(
        ["```sql\nSELECT region FROM finance_metrics WHERE tenant_id = 't';\n```"]
    )
    gen = LLMSqlGenerator(client, "sql-model")
    req = AnalyticsRequest(tenant_id=uuid4(), question="q")
    plan = QueryPlan(
        purpose="drivers",
        table="finance_metrics",
        measures=(("SUM(revenue)", "revenue"),),
        dimensions=("region",),
        filters=(("region", "EMEA"),),
        period_in=("period", ("a", "b")),
    )
    sql = await gen.generate(req, FINANCE_SCHEMA, plan, tenant_id=req.tenant_id)
    assert sql == "SELECT region FROM finance_metrics WHERE tenant_id = 't'"
    assert client.models == ["sql-model"]
    assert "finance_metrics(" in client.prompts[0] and str(req.tenant_id) in client.prompts[0]
    await gen.generate(
        req, FINANCE_SCHEMA, plan, tenant_id=req.tenant_id, feedback="unknown column"
    )
    assert "rejected: unknown column" in client.prompts[1]


# --- query expander -------------------------------------------------------------


async def test_llm_query_expander_dedupes_and_survives_failure() -> None:
    client = FakeModelClient(["- margin drivers EMEA\n• Why did EMEA margin fall?\n- cogs spike"])
    out = await LLMQueryExpander(client, max_variants=3, model="light").expand(
        "Why did EMEA margin fall?"
    )
    assert out == ["Why did EMEA margin fall?", "margin drivers EMEA", "cogs spike"]
    assert client.models == ["light"]
    assert await LLMQueryExpander(FakeModelClient(raise_on=1)).expand("q") == ["q"]


# --- recommender ----------------------------------------------------------------


def _claims() -> list[VerifiedClaim]:
    return [
        VerifiedClaim(
            claim="Hardware contributed -220",
            source="analytics",
            kind="driver",
            status=VerificationStatus.VERIFIED,
            method="recompute",
            confidence=0.9,
            claimed_magnitude=-220.0,
        ),
        VerifiedClaim(
            claim="Services correlates with margin",
            source="analytics",
            kind="correlation",
            status=VerificationStatus.VERIFIED,
            method="recompute",
            confidence=0.8,
            association_only=True,
        ),
        VerifiedClaim(
            claim="Software contributed -5",
            source="analytics",
            kind="driver",
            status=VerificationStatus.CONFLICTING,
            method="recompute",
            confidence=0.2,
            claimed_magnitude=-5.0,
        ),
    ]


async def test_llm_recommender_keeps_only_grounded_actions_and_clamps_confidence() -> None:
    payload: dict[str, Any] = {
        "recommendations": [
            {
                "action": "Renegotiate hardware COGS",
                "rationale": "r",
                "impact": -220,
                "confidence": 0.99,
                "based_on": ["Hardware contributed -220"],
            },
            {
                "action": "Cut services",
                "rationale": "r",
                "impact": 10,
                "confidence": 0.9,
                "based_on": ["Services correlates with margin"],
            },
            {
                "action": "Fix software",
                "rationale": "r",
                "impact": 5,
                "confidence": 0.9,
                "based_on": ["Software contributed -5"],
            },
            {"action": "Made up", "rationale": "r", "impact": 5, "confidence": 0.9, "based_on": []},
        ]
    }
    # A model-emitted provenance label is ignored — stamped after validation.
    payload["recommendations"][0]["produced_by"] = "heuristic"
    client = FakeModelClient([json.dumps(payload)])
    recs = await LLMRecommender(client, "std").recommend("objective", _claims())
    assert [r.action for r in recs] == ["Renegotiate hardware COGS"]
    assert recs[0].confidence == 0.9 and recs[0].impact == 220.0
    assert recs[0].produced_by == "llm" and recs[0].fallback_reason is None
    assert "Services correlates" not in client.prompts[0]  # never offered to the model


async def test_llm_recommender_falls_back_when_nothing_is_grounded() -> None:
    from eadip.verification.recommend import HeuristicRecommender

    expected = await HeuristicRecommender().recommend("o", _claims())
    ungrounded = json.dumps(
        {
            "recommendations": [
                {
                    "action": "x",
                    "rationale": "r",
                    "impact": 1,
                    "confidence": 0.5,
                    "based_on": ["nope"],
                }
            ]
        }
    )
    assert expected and all(r.produced_by == "heuristic" for r in expected)
    ungrounded_recs = await LLMRecommender(FakeModelClient([ungrounded])).recommend("o", _claims())
    assert [_core(r) for r in ungrounded_recs] == [_core(r) for r in expected]
    assert all(r.produced_by == "heuristic_fallback" for r in ungrounded_recs)
    assert all((r.fallback_reason or "").startswith("invalid_shape:") for r in ungrounded_recs)
    down_recs = await LLMRecommender(FakeModelClient(raise_on=1)).recommend("o", _claims())
    assert [_core(r) for r in down_recs] == [_core(r) for r in expected]
    assert all((r.fallback_reason or "").startswith("llm_error:") for r in down_recs)
    # No eligible claims at all: straight to the heuristic, no model call.
    client = FakeModelClient(["{}"])
    assert await LLMRecommender(client).recommend("o", [_claims()[2]]) == []
    assert client.calls == 0


# --- factories route models through the policy ------------------------------------


def test_factories_route_llm_backends_through_tiers() -> None:
    from eadip.analytics.factory import build_generator
    from eadip.config.settings import Settings
    from eadip.retrieval.factory import _build_expander
    from eadip.verification.factory import build_recommender

    s = Settings(
        sql_generator_backend="llm",
        query_expander_backend="llm",
        recommendation_backend="llm",
        model_tier_standard="std-m",
        model_tier_light="light-m",
        model_tier_strong="strong-m",
    )
    assert build_generator(s)._model == "std-m"  # type: ignore[attr-defined]
    assert _build_expander(s)._model == "light-m"  # type: ignore[attr-defined]
    assert build_recommender(s)._model == "std-m"  # type: ignore[attr-defined]


# --- LiteLLM client: usage + cost parsing with the SDK stubbed ---------------------


async def test_litellm_client_parses_usage_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    from eadip.adapters.litellm_client import LiteLLMClient

    class _Msg:
        content = "hello"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = types.SimpleNamespace(prompt_tokens=12, completion_tokens=3)

    calls: list[dict[str, Any]] = []

    async def fake_acompletion(**kwargs: Any) -> _Resp:
        calls.append(kwargs)
        return _Resp()

    fake = types.SimpleNamespace(
        acompletion=fake_acompletion, completion_cost=lambda completion_response: 0.0042
    )
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake)
    resp = await LiteLLMClient("default-m", "http://base").complete("hi", model="m2")
    assert (resp.text, resp.model, resp.tokens_in, resp.tokens_out, resp.cost_usd) == (
        "hello",
        "m2",
        12,
        3,
        0.0042,
    )
    assert calls[0]["model"] == "m2" and calls[0]["api_base"] == "http://base"
    assert calls[0]["messages"] == [{"role": "user", "content": "hi"}]

    fake.completion_cost = lambda completion_response: (_ for _ in ()).throw(
        RuntimeError("no price")
    )
    assert (await LiteLLMClient("default-m").complete("hi")).cost_usd == 0.0
