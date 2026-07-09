"""Build the Memory Agent from settings (Phase 11, FR-045).

In-memory stores are the offline-deterministic default; `memory_backend =
"postgres"` swaps in the RLS-scoped Postgres stores behind the same ports.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from eadip.config.settings import Settings
from eadip.memory.agent import MemoryAgent
from eadip.memory.ports import InMemoryEpisodicStore, InMemorySemanticStore

if TYPE_CHECKING:
    from eadip.adapters.postgres import Database


def build_memory_agent(
    settings: Settings, *, database: Database | None = None
) -> MemoryAgent | None:
    """None when memory is disabled — the engine then neither recalls nor
    consolidates (runs behave exactly as they did through Phase 10)."""
    if not settings.memory_enabled:
        return None
    if settings.memory_backend == "postgres" and database is not None:
        from eadip.adapters.postgres_memory_store import (
            PostgresEpisodicStore,
            PostgresSemanticStore,
        )

        return MemoryAgent(
            episodic=PostgresEpisodicStore(database),
            semantic=PostgresSemanticStore(database),
            min_fact_confidence=settings.memory_min_fact_confidence,
            now=time.time,
        )
    return MemoryAgent(
        episodic=InMemoryEpisodicStore(),
        semantic=InMemorySemanticStore(),
        min_fact_confidence=settings.memory_min_fact_confidence,
        now=time.time,
    )
