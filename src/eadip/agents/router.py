"""Router (FR-004): pick the next batch of steps to run.

Deterministic dependency-aware scheduling — a step is runnable once all its
`depends_on` are complete; independent runnable steps form one batch (the safe
parallel branches), capped by `max_parallelism`. Ordering is by id so batching
is reproducible (important for checkpoint/resume and tests).
"""

from __future__ import annotations

from eadip.orchestrator.models import Plan, PlanStep


class Router:
    def next_batch(self, plan: Plan, completed: set[str], max_parallelism: int) -> list[PlanStep]:
        runnable = [
            s
            for s in plan.steps
            if s.id not in completed and all(dep in completed for dep in s.depends_on)
        ]
        runnable.sort(key=lambda s: s.id)
        return runnable[:max_parallelism]

    def is_blocked(self, plan: Plan, completed: set[str]) -> bool:
        """True when steps remain but none are runnable (unsatisfiable deps)."""
        remaining = [s for s in plan.steps if s.id not in completed]
        return bool(remaining) and not self.next_batch(plan, completed, 1)
