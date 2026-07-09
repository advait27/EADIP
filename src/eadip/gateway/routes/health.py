"""Liveness/readiness probes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from eadip.config.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    if get_settings().database_enabled:
        from eadip.gateway.dependencies import get_database

        if not await get_database().ping():
            raise HTTPException(status_code=503, detail="database not ready")
    return {"status": "ready"}
