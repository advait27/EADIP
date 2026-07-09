"""Value objects for the document/embedding pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class IngestRequest:
    tenant_id: UUID
    raw: bytes
    content_type: str
    source_ref: str
    source_type: str = "document"
    acl_tags: tuple[str, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    filename: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    content_type: str


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str


@dataclass(frozen=True)
class IngestResult:
    document_id: UUID
    collection: str
    chunk_count: int
    cache_hits: int
    pii_types_found: tuple[str, ...] = field(default_factory=tuple)
