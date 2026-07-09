"""Document lineage port (governed ingestion metadata).

A relational record of every ingested document for provenance/audit, scoped by
tenant (RLS). The chunk vectors live in the vector store; this is the lineage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class DocumentMeta:
    id: UUID
    tenant_id: UUID
    source_ref: str
    source_type: str
    acl_tags: tuple[str, ...]
    chunk_count: int
    embed_model_version: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class DocumentRepository(Protocol):
    async def add(self, meta: DocumentMeta) -> None: ...
