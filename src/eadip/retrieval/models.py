"""Retrieval value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Query:
    tenant_id: UUID
    text: str
    acl_tags: tuple[str, ...] = ()  # caller's allowed ACL tags (identity-scoped)
    sources: tuple[str, ...] = ()
    as_of: datetime | None = None
    top_k: int = 8
    effort: str = "standard"  # "simple" | "standard" | "deep"


@dataclass(frozen=True)
class Passage:
    id: UUID
    text: str
    score: float
    source_ref: str = ""
    source_type: str = ""
    document_id: str | None = None
    ordinal: int | None = None
    acl_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalResult:
    passages: list[Passage] = field(default_factory=list)
    sub_queries: list[str] = field(default_factory=list)
    candidates_considered: int = 0
