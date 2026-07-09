"""Tool executor (FR-005/033, AP-8) — a governed MCP tool as an orchestrator step.

When the planner emits a `kind="tool"` step, this executor:
  1. asks the Tool Agent to rank capable tools for the step's intent;
  2. invokes the top candidate through the MCP Manager (which does the RBAC
     pre-check, typed-arg gate, breaker, and untrusted-output wrapping);
  3. on denial/failure, falls back to the next candidate (narrowed-scope
     continuation) — so a single sick or forbidden tool doesn't sink the step;
  4. turns the (untrusted) output into a finding with tool provenance.

It also implements ``preflight`` (Phase 9): resolve the tool the step would call
and, when that tool is side-effecting (write / high-impact), return the exact
``ProposedAction`` so the engine can pause for human approval before it runs.

Because it plugs into the same `Executor` Protocol as retrieval/analytics, a
newly-registered tool becomes usable by the orchestrator with no code change —
the extensibility backbone the phase is about.
"""

from __future__ import annotations

from typing import Any

from eadip.approval.models import ProposedAction
from eadip.mcp.agent import ToolAgent, ToolCandidate
from eadip.mcp.models import ToolResult
from eadip.mcp.registry import McpManager
from eadip.orchestrator.models import Evidence, Finding, PlanStep, StepResult
from eadip.orchestrator.state import RunState
from eadip.security.identity import Identity


def _clip(value: Any, n: int = 240) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class ToolExecutor:
    kind = "tool"

    def __init__(self, manager: McpManager, agent: ToolAgent, *, max_fallbacks: int = 2) -> None:
        self._manager = manager
        self._agent = agent
        self._max_fallbacks = max_fallbacks

    @staticmethod
    def _identity(step: PlanStep, state: RunState) -> Identity:
        # An approved side-effecting step executes under the approver's roles
        # (user-scoped token, Phase 9); otherwise the run's own identity.
        approver_roles = step.params.get("_approver_roles")
        roles = tuple(approver_roles) if approver_roles else tuple(state.acl_tags)
        return Identity(user_id=state.user_id, tenant_id=state.tenant_id, roles=roles)

    async def _candidates(self, step: PlanStep, state: RunState) -> list[ToolCandidate]:
        intent = str(step.params.get("intent", step.description or state.question))
        servers = await self._manager.list_servers(state.tenant_id)
        candidates = self._agent.select(intent, servers, self._identity(step, state))
        # Explicit server.tool pin overrides capability matching when provided.
        pinned = step.params.get("tool")
        if isinstance(pinned, str) and "." in pinned:
            server_name, tool_name = pinned.split(".", 1)
            candidates = [
                c for c in candidates if c.server == server_name and c.tool == tool_name
            ] or candidates
        return candidates

    async def preflight(self, step: PlanStep, state: RunState) -> ProposedAction | None:
        """Describe the side-effecting action this step would take (Phase 9). The
        engine calls this before executing a `tool` step; a None means the step is
        read-only (or has no capable tool) and needs no approval gate."""
        candidates = await self._candidates(step, state)
        if not candidates:
            return None
        top = candidates[0]
        perm = top.spec.permission
        if not perm.is_side_effecting:
            return None
        arguments: dict[str, Any] = dict(step.params.get("arguments", {}))
        return ProposedAction(
            step_id=step.id,
            kind=self.kind,
            effect=perm.effect,
            server=top.server,
            tool=top.tool,
            summary=f"call {top.ref} ({perm.effect})",
            payload=arguments,
        )

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        identity = self._identity(step, state)
        arguments: dict[str, Any] = dict(step.params.get("arguments", {}))
        candidates = await self._candidates(step, state)

        if not candidates:
            return StepResult(
                step_id=step.id,
                kind=self.kind,
                ok=False,
                summary="no capable tool found",
                error="no_tool_match",
            )

        tried: list[str] = []
        last: ToolResult | None = None
        for candidate in candidates[: self._max_fallbacks + 1]:
            result = await self._manager.invoke(
                identity, candidate.server, candidate.tool, arguments
            )
            tried.append(candidate.ref)
            last = result
            if result.ok:
                return self._to_step_result(step, candidate, result, tried)
            # Denied / invalid args are not retryable via fallback with the same
            # arguments unless another tool has a different permission surface;
            # transport/circuit failures are exactly what fallback is for (AP-8).

        assert last is not None
        return StepResult(
            step_id=step.id,
            kind=self.kind,
            ok=False,
            summary=f"all {len(tried)} candidate tool(s) failed ({last.reason})",
            error=f"{last.reason}: {last.error}"[:200],
        )

    def _to_step_result(
        self, step: PlanStep, candidate: ToolCandidate, result: ToolResult, tried: list[str]
    ) -> StepResult:
        # Output is SEC-06-wrapped: {"untrusted": True, "data": ...}. Summarise the
        # payload for the finding; it is evidence/data, never an instruction.
        payload = result.output.get("data") if isinstance(result.output, dict) else result.output
        fallback_of = tried[0] if len(tried) > 1 else ""
        finding = Finding(
            claim=f"{candidate.tool}: {_clip(payload)}",
            source="tool",
            step_id=step.id,
            kind="tool_result",
            evidence=[Evidence(kind="tool", ref=candidate.ref, snippet=_clip(payload, 160))],
            detail={
                "server": candidate.server,
                "tool": candidate.tool,
                "from_cache": result.from_cache,
                "latency_ms": round(result.latency_ms, 3),
                "fallback_of": fallback_of,
                "untrusted": True,
            },
        )
        summary = f"{candidate.ref} ok" + (f" (fallback from {fallback_of})" if fallback_of else "")
        return StepResult(
            step_id=step.id, kind=self.kind, ok=True, summary=summary, findings=[finding]
        )
