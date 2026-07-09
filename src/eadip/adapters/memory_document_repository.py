"""In-memory document lineage repository for dev/tests."""

from __future__ import annotations

from eadip.ports.documents import DocumentMeta


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self._docs: list[DocumentMeta] = []

    async def add(self, meta: DocumentMeta) -> None:
        self._docs.append(meta)

    @property
    def documents(self) -> list[DocumentMeta]:
        return list(self._docs)
