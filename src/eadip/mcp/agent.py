"""Tool Agent (FR-033, AP-8) — pick the right tool for an intent.

Given a natural-language intent and the tenant's registered servers, it:
  1. matches capability by content-token overlap between the intent and each
     tool's name+description (deterministic; an LLM matcher can slot in behind
     the same `select` signature later);
  2. filters out non-invocable servers (Failed/Draining) and tools the caller
     could never be granted (a fast, advisory ACL-tag pre-filter — the registry
     still does the authoritative RBAC check at invoke);
  3. ranks survivors, breaking ties by health, then latency, then cost — so the
     healthiest, fastest, cheapest capable tool is tried first;
  4. returns the ranked candidates, so the executor can fall back to the next one
     when the top choice is denied or fails (narrowed-scope continuation).

Pure and offline-deterministic; it reads registry state, it does not call tools.
"""

from __future__ import annotations

from dataclasses import dataclass

from eadip.mcp.models import RegisteredServer, ServerState, ToolSpec
from eadip.retrieval.text import content_tokens, jaccard
from eadip.security.identity import Identity

# State health preference (higher is better) for the primary tie-break.
_STATE_RANK = {ServerState.HEALTHY: 2, ServerState.DEGRADED: 1}


@dataclass(frozen=True)
class ToolCandidate:
    server: str
    tool: str
    spec: ToolSpec
    score: float  # capability match in [0, 1]

    @property
    def ref(self) -> str:
        return f"{self.server}.{self.tool}"


class ToolAgent:
    def __init__(self, *, min_match: float = 0.05) -> None:
        self._min_match = min_match

    def select(
        self, intent: str, servers: list[RegisteredServer], identity: Identity
    ) -> list[ToolCandidate]:
        """Ranked candidates for `intent`, best first. Empty when nothing matches
        or nothing is invocable."""
        intent_tokens = set(content_tokens(intent))
        caller_tags = set(identity.roles)
        scored: list[tuple[float, float, float, float, ToolCandidate]] = []

        for server in servers:
            if not server.health.invocable:  # Failed / Draining are skipped
                continue
            for spec in server.tools:
                # Advisory ACL pre-filter (authoritative check is at invoke time).
                if not set(spec.permission.acl_tags) <= caller_tags:
                    continue
                match = self._capability_match(intent_tokens, spec)
                if match < self._min_match:
                    continue
                candidate = ToolCandidate(
                    server=server.config.name, tool=spec.name, spec=spec, score=round(match, 4)
                )
                # Sort key: match desc, health desc, latency asc, cost asc, ref.
                scored.append(
                    (
                        -match,
                        -_STATE_RANK.get(server.health.state, 0),
                        server.health.last_latency_ms * server.config.weight_latency,
                        server.config.weight_cost,
                        candidate,
                    )
                )

        scored.sort(key=lambda t: (t[0], t[1], t[2], t[3], t[4].ref))
        return [c for *_rest, c in scored]

    @staticmethod
    def _capability_match(intent_tokens: set[str], spec: ToolSpec) -> float:
        cap_tokens = set(content_tokens(f"{spec.name} {spec.description}"))
        return jaccard(intent_tokens, cap_tokens)
