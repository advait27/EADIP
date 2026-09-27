"""Verify tenants' audit hash chains against Postgres (SEC-08).

CLI: `eadip-audit-verify <tenant-uuid> [<tenant-uuid> ...]`. Prints one chain
report per tenant; exits 1 if any chain is broken. A passing report proves only
that the chained rows are internally consistent — compare the printed head hash
with an externally anchored copy to detect a full rewrite or tail truncation.
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from eadip.adapters.postgres import Database
from eadip.adapters.postgres_audit_log import PostgresAuditLog
from eadip.security.audit import ChainReport


def format_report(tenant_id: UUID, report: ChainReport) -> str:
    status = "OK" if report.ok else "BROKEN"
    line = f"{tenant_id}: {status} checked={report.checked} head={report.head_hash or '-'}"
    if not report.anchored:
        line += f" anchor={report.anchor_prev_hash} (not genesis; unverifiable from DB)"
    if not report.ok:
        line += f" break_index={report.break_index} reason={report.reason}"
    return line


async def _run(tenant_ids: list[UUID]) -> int:
    from eadip.config.settings import get_settings

    settings = get_settings()
    db = Database(settings.postgres_dsn.get_secret_value())
    await db.connect()
    try:
        log = PostgresAuditLog(db)
        broken = False
        for tenant_id in tenant_ids:
            report = await log.verify(tenant_id)
            print(format_report(tenant_id, report))
            broken = broken or not report.ok
    finally:
        await db.close()
    return 1 if broken else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify EADIP audit hash chains")
    parser.add_argument("tenant_ids", nargs="+", type=UUID, metavar="TENANT_ID")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.tenant_ids)))


if __name__ == "__main__":
    main()
