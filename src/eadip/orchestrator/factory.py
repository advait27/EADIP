"""Build the orchestration engine from settings.

Agents default to deterministic heuristics (offline) with an LLM backend behind
the same Protocols; executors wrap the Phase 4 retrieval and Phase 5 analytics
services. Bounds come from the run guards in settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eadip.agents.interpreter import GoalInterpreter, HeuristicGoalInterpreter
from eadip.agents.planner import HeuristicPlanner, Planner
from eadip.agents.reflection import HeuristicReflection, ReflectionAgent
from eadip.agents.router import Router
from eadip.analytics.service import AnalyticsService
from eadip.approval.factory import build_autonomy_policy
from eadip.config.settings import Settings
from eadip.orchestrator.checkpoint import Checkpointer
from eadip.orchestrator.executors import AnalyticsExecutor, Executor, RetrievalExecutor
from eadip.orchestrator.service import OrchestratorService
from eadip.platform.factory import build_routing_policy
from eadip.platform.models import TaskKind
from eadip.ports.warehouse import Warehouse
from eadip.retrieval.service import RetrievalService
from eadip.verification.factory import build_reporter

if TYPE_CHECKING:
    from eadip.agents.llm import InstructionProvider
    from eadip.mcp.executor import ToolExecutor
    from eadip.memory.agent import MemoryAgent
    from eadip.platform.prompts import PromptRegistry


def _instruction_provider(prompts: PromptRegistry | None, name: str) -> InstructionProvider | None:
    """Per-call fetch of the active governed prompt (promote/rollback applies
    immediately); None keeps the shipped default."""
    if prompts is None:
        return None

    async def provide() -> str | None:
        version = await prompts.resolve(name)
        return version.content if version is not None else None

    return provide


def build_agents(
    settings: Settings, *, prompts: PromptRegistry | None = None
) -> tuple[GoalInterpreter, Planner, ReflectionAgent]:
    if settings.planner_backend == "llm":
        from eadip.adapters.litellm_client import LiteLLMClient
        from eadip.agents.interpreter import LLMGoalInterpreter
        from eadip.agents.planner import LLMPlanner
        from eadip.agents.reflection import LLMReflection

        client = LiteLLMClient(settings.default_model, settings.model_api_base)
        # Per-task model routing (Phase 11, FR-053): planning rides the pinned
        # strong tier; interpret/reflect take their routed tiers.
        routing = build_routing_policy(settings)
        return (
            LLMGoalInterpreter(
                client,
                routing.route(TaskKind.INTERPRET).model,
                instruction_provider=_instruction_provider(prompts, "agent/interpreter"),
            ),
            LLMPlanner(
                client,
                routing.route(TaskKind.PLAN).model,
                instruction_provider=_instruction_provider(prompts, "agent/planner"),
            ),
            LLMReflection(
                client,
                routing.route(TaskKind.REFLECT).model,
                instruction_provider=_instruction_provider(prompts, "agent/reflection"),
            ),
        )
    return HeuristicGoalInterpreter(), HeuristicPlanner(), HeuristicReflection()


def build_orchestrator(
    settings: Settings,
    *,
    retrieval_service: RetrievalService,
    analytics_service: AnalyticsService,
    warehouse: Warehouse,
    checkpointer: Checkpointer,
    tool_executor: ToolExecutor | None = None,
    memory: MemoryAgent | None = None,
    prompts: PromptRegistry | None = None,
) -> OrchestratorService:
    interpreter, planner, reflection = build_agents(settings, prompts=prompts)
    executors: dict[str, Executor] = {
        "retrieve": RetrievalExecutor(retrieval_service),
        "analyze": AnalyticsExecutor(analytics_service),
    }
    # A governed MCP tool executor turns any registered tool into an orchestrator
    # step (Phase 8) — no code change needed to add a new tool, only registration.
    if tool_executor is not None:
        executors["tool"] = tool_executor
    return OrchestratorService(
        interpreter=interpreter,
        planner=planner,
        router=Router(),
        reflection=reflection,
        executors=executors,
        checkpointer=checkpointer,
        reporter=build_reporter(settings, warehouse),
        autonomy=build_autonomy_policy(settings),
        memory=memory,
        max_iterations=settings.max_iterations,
        max_cost_usd=settings.max_run_cost_usd,
        deadline_s=float(settings.run_deadline_s),
        step_timeout_s=settings.orchestrator_step_timeout_s,
        retry_attempts=settings.orchestrator_retry_attempts,
        retry_base_delay_s=settings.orchestrator_retry_base_delay_s,
        loop_similarity=settings.orchestrator_loop_similarity,
        max_parallelism=settings.orchestrator_max_parallelism,
    )
