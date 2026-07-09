"""Repository ports (data-access interfaces)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from eadip.domain.entities import Run


class RunRepository(Protocol):
    async def add(self, run: Run) -> None: ...

    async def get(self, run_id: UUID) -> Run | None: ...
