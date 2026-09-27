"""Bring your own data (Glass Box): upload a CSV, ask about it by name.

``POST /v1/datasets`` (multipart ``file``, optional ``name``) parses, normalises
and types the CSV, then registers it as a tenant-scoped table behind the same
NL->SQL safety gate as the demo data. DuckDB backend only in this cut (501 on
Postgres). The demo table's name is reserved (409); an upload over the configured
caps is a 413. Every upload is audited.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from eadip.adapters.demo_finance import FINANCE_SCHEMA
from eadip.adapters.duckdb_warehouse import DuckDBWarehouse
from eadip.gateway.authz import require
from eadip.gateway.dependencies import AuditLogDep, ResidencyGuardDep, SettingsDep, WarehouseDep
from eadip.gateway.models import DatasetColumn, DatasetItem
from eadip.ingestion.datasets import (
    DatasetEntry,
    DatasetError,
    DatasetTooLarge,
    parse_csv,
    slugify_table,
)
from eadip.observability.logging import get_logger
from eadip.ports.warehouse import Warehouse
from eadip.security.audit import make_event
from eadip.security.identity import Identity
from eadip.security.rbac import Effect

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])
log = get_logger(__name__)

DatasetWriterDep = Annotated[Identity, Depends(require("dataset", "*", Effect.WRITE))]
DatasetReaderDep = Annotated[Identity, Depends(require("dataset", "*", Effect.READ))]

_CHUNK = 1 << 20


def _item(entry: DatasetEntry) -> DatasetItem:
    return DatasetItem(
        name=entry.name,
        table=entry.physical,
        columns=[DatasetColumn(name=c.name, type=c.type) for c in entry.schema.columns],
        row_count=entry.row_count,
        period_column=entry.schema.period_column,
        dimensions=list(entry.schema.dimensions),
    )


def _duckdb(warehouse: Warehouse) -> DuckDBWarehouse:
    if not isinstance(warehouse, DuckDBWarehouse):
        raise HTTPException(status_code=501, detail="datasets require the DuckDB warehouse backend")
    return warehouse


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload without ever holding more than the cap in memory: refuse
    on the declared size first, then stop reading the moment the cap is passed."""
    if file.size is not None and file.size > max_bytes:
        raise DatasetTooLarge(f"file exceeds {max_bytes} bytes")
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise DatasetTooLarge(f"file exceeds {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", status_code=201, response_model=DatasetItem)
async def upload_dataset(
    identity: DatasetWriterDep,
    _residency: ResidencyGuardDep,
    warehouse: WarehouseDep,
    settings: SettingsDep,
    audit: AuditLogDep,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> DatasetItem:
    wh = _duckdb(warehouse)
    try:
        data = await _read_capped(file, settings.dataset_max_bytes)
        # CPU-bound parsing/typing of up to the cap: off the event loop so the
        # SSE tails and background runs sharing it are not stalled.
        parsed = await asyncio.to_thread(
            parse_csv,
            data,
            max_bytes=settings.dataset_max_bytes,
            max_columns=settings.dataset_max_columns,
            max_rows=settings.dataset_max_rows,
        )
        slug = slugify_table(name or file.filename or "dataset")
    except DatasetTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from None
    except DatasetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if slug in FINANCE_SCHEMA.table_names():
        raise HTTPException(status_code=409, detail=f"'{slug}' is a reserved table name")
    entry = await wh.register_dataset(identity.tenant_id, slug, parsed)
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="dataset.upload",
            detail={
                "name": entry.name,
                "rows": entry.row_count,
                "columns": [c.name for c in entry.schema.columns],
                "period_column": entry.schema.period_column,
            },
        )
    )
    log.info("dataset.uploaded", name=entry.name, rows=entry.row_count)
    return _item(entry)


@router.get("", response_model=list[DatasetItem])
async def list_datasets(
    identity: DatasetReaderDep, _residency: ResidencyGuardDep, warehouse: WarehouseDep
) -> list[DatasetItem]:
    if not isinstance(warehouse, DuckDBWarehouse):
        return []
    return [_item(e) for e in warehouse.datasets.entries(identity.tenant_id)]


@router.delete("/{name}", status_code=204)
async def delete_dataset(
    name: str,
    identity: DatasetWriterDep,
    _residency: ResidencyGuardDep,
    warehouse: WarehouseDep,
    audit: AuditLogDep,
) -> None:
    wh = _duckdb(warehouse)
    # The same normalisation as upload, so the name a client uploaded under
    # ("Q3 Sales") and the registered slug ("q3_sales") both delete it.
    try:
        slug = slugify_table(name)
    except DatasetError:
        raise HTTPException(status_code=404, detail="dataset not found") from None
    if not await wh.drop_dataset(identity.tenant_id, slug):
        raise HTTPException(status_code=404, detail="dataset not found")
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="dataset.delete",
            detail={"name": slug},
        )
    )
