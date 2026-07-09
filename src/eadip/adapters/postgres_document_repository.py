"""Tenant-scoped document lineage repository backed by PostgreSQL + RLS."""

from __future__ import annotations

from eadip.adapters.postgres import Database
from eadip.ports.documents import DocumentMeta

_INSERT = (
    "INSERT INTO document "
    "(id, tenant_id, source_ref, source_type, acl_tags, chunk_count, "
    " embed_model_version, valid_from, valid_to) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)"
)


class PostgresDocumentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, meta: DocumentMeta) -> None:
        async with self._db.tenant_connection(meta.tenant_id) as conn:
            await conn.execute(
                _INSERT,
                meta.id,
                meta.tenant_id,
                meta.source_ref,
                meta.source_type,
                list(meta.acl_tags),
                meta.chunk_count,
                meta.embed_model_version,
                meta.valid_from,
                meta.valid_to,
            )
