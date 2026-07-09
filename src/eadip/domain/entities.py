"""Framework-free domain entities (TAD §18.2).

Kept free of FastAPI/Pydantic so domain logic stays isolated from frameworks
(AP-10). API-boundary models live in eadip.gateway.models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class RunStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    EXECUTING = "executing"
    AWAITING_APPROVAL = "awaiting_approval"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Run:
    """A single investigation run."""

    tenant_id: UUID
    user_id: UUID
    question: str
    id: UUID = field(default_factory=uuid4)
    status: RunStatus = RunStatus.QUEUED
    cost_usd: float = 0.0
    created_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None
