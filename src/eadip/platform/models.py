"""Platform services value objects (Phase 11, FR-052/053/056/057, TAD Ch 10/14).

Pure Pydantic — no IO. Prompt artifacts, model-routing choices, tenant budgets,
notifications and feature flags all live here so the services and the admin
portal share one vocabulary.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# --- prompt management (FR-052, US-D2) ---------------------------------------
class PromptStage(StrEnum):
    DRAFT = "draft"  # registered, not yet eval'd
    CANDIDATE = "candidate"  # eval-passed, serving a canary fraction
    ACTIVE = "active"  # the version the platform serves
    RETIRED = "retired"  # previously active (rollback target)
    ROLLED_BACK = "rolled_back"  # demoted by rollback; never auto-re-picked


class PromptVersion(BaseModel):
    """One immutable version of a named prompt. Content never changes after
    registration (`content_sha` seals it); only lifecycle metadata moves."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    version: int
    content: str
    content_sha: str = ""
    stage: PromptStage = PromptStage.DRAFT
    eval_score: float | None = None  # recorded harness score; gates promotion
    eval_source: str = ""  # which gold set / harness produced the score
    canary_fraction: float = 0.0  # share of traffic served while CANDIDATE
    created_by: str = ""
    created_at_s: float = 0.0
    promoted_at_s: float | None = None  # ordering key for one-click rollback

    def model_post_init(self, __context: object) -> None:
        if not self.content_sha:
            self.content_sha = hashlib.sha256(self.content.encode()).hexdigest()


# --- model routing (FR-053, US-D3) --------------------------------------------
class TaskKind(StrEnum):
    INTERPRET = "interpret"
    PLAN = "plan"
    REFLECT = "reflect"
    VERIFY = "verify"
    RECOMMEND = "recommend"
    SQL_GENERATE = "sql_generate"
    QUERY_EXPAND = "query_expand"
    RERANK = "rerank"
    EMBED = "embed"


class ModelTier(StrEnum):
    STRONG = "strong"
    STANDARD = "standard"
    LIGHT = "light"


class ModelChoice(BaseModel):
    task: TaskKind
    tier: ModelTier
    model: str
    pinned: bool = False  # planning/verification never route below STRONG
    reason: str = ""


# --- per-tenant budgets (FR-053) ----------------------------------------------
class TenantBudget(BaseModel):
    tenant_id: UUID
    monthly_cap_usd: float = 0.0  # 0 = no cap configured
    spent_usd: float = 0.0

    @property
    def remaining_usd(self) -> float:
        if self.monthly_cap_usd <= 0:
            return float("inf")
        return max(0.0, self.monthly_cap_usd - self.spent_usd)

    @property
    def exhausted(self) -> bool:
        return self.monthly_cap_usd > 0 and self.spent_usd >= self.monthly_cap_usd


# --- notifications (FR-056) ----------------------------------------------------
class NotificationKind(StrEnum):
    RUN_COMPLETED = "run.completed"
    APPROVAL_REQUIRED = "approval.required"
    ANOMALY_DETECTED = "anomaly.detected"
    BUDGET_EXHAUSTED = "budget.exhausted"


class Notification(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    kind: NotificationKind
    severity: str = "info"  # "info" | "action_required" | "warning"
    title: str
    body: str = ""
    run_id: UUID | None = None
    created_at_s: float = 0.0
    channel: str = ""  # which channel delivered it
    delivered: bool = False
