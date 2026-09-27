"""Data retention sweeper (Phase 12, PRD §21, GDPR storage limitation).

Deletes tenant data past its retention window, per artifact class:
  - runs + checkpoints  (`retention_run_days`)     — investigation state
  - long-term memory    (`retention_memory_days`)  — episodic + semantic tiers
  - document lineage    (`retention_document_days`)— ingestion records

`audit_event` is deliberately NOT swept: the audit trail is append-only
compliance evidence with its own (longer) retention, archived by the ops
pipeline — deleting it here would destroy the evidence retention exists for.

Pure planning logic (`plan`) is deterministic and unit-tested; `sweep` executes
the plan against Postgres. CLI: `eadip-retention` (dry-run by default; `--apply`
deletes; requires `EADIP_DATABASE_ENABLED=true`). Scheduled in production as a
CronJob next to the DR backups.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from eadip.adapters.postgres import Database
from eadip.config.settings import Settings, get_settings
from eadip.observability.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RetentionStatement:
    """One class of data to expire: the DELETE and its cutoff timestamp."""

    artifact: str
    sql: str  # parameterised: $1 = cutoff timestamp
    cutoff: datetime


def plan(settings: Settings, *, now: datetime | None = None) -> list[RetentionStatement]:
    """The sweep plan for the configured windows. A window of 0 disables that
    class (retain indefinitely)."""
    now = now or datetime.now(UTC)
    statements: list[RetentionStatement] = []
    if settings.retention_run_days > 0:
        cutoff = now - timedelta(days=settings.retention_run_days)
        statements.append(
            RetentionStatement(
                "run_checkpoints", "DELETE FROM run_checkpoint WHERE updated_at < $1", cutoff
            )
        )
        statements.append(
            RetentionStatement("runs", "DELETE FROM run WHERE created_at < $1", cutoff)
        )
    if settings.retention_memory_days > 0:
        cutoff = now - timedelta(days=settings.retention_memory_days)
        statements.append(
            RetentionStatement(
                "episodic_memory", "DELETE FROM episodic_memory WHERE updated_at < $1", cutoff
            )
        )
        statements.append(
            RetentionStatement(
                "semantic_facts", "DELETE FROM semantic_fact WHERE created_at < $1", cutoff
            )
        )
    if settings.retention_document_days > 0:
        cutoff = now - timedelta(days=settings.retention_document_days)
        statements.append(
            RetentionStatement(
                "document_lineage", "DELETE FROM document WHERE ingested_at < $1", cutoff
            )
        )
    return statements


async def sweep(db: Database, settings: Settings, *, apply: bool = False) -> dict[str, int]:
    """Execute (or dry-run count) the retention plan. Returns rows per class."""
    results: dict[str, int] = {}
    async with db.connection() as conn:
        for stmt in plan(settings):
            if apply:
                outcome = await conn.execute(stmt.sql, stmt.cutoff)
                results[stmt.artifact] = int(outcome.split()[-1]) if outcome else 0
            else:
                count_sql = stmt.sql.replace("DELETE FROM", "SELECT count(*) FROM", 1)
                row = await conn.fetchrow(count_sql, stmt.cutoff)
                results[stmt.artifact] = int(row[0]) if row else 0
            log.info(
                "retention.sweep",
                artifact=stmt.artifact,
                cutoff=stmt.cutoff.isoformat(),
                rows=results[stmt.artifact],
                applied=apply,
            )
    return results


async def _run(apply: bool) -> int:
    settings = get_settings()
    mode = "APPLY" if apply else "DRY-RUN"
    if not settings.database_enabled:
        print(f"retention [{mode}]: database disabled — plan only:")
        for stmt in plan(settings):
            print(f"  {stmt.artifact:<18} expire rows older than {stmt.cutoff.date()}")
        return 0
    # Cross-tenant maintenance: RLS would hide every row from the app role, so
    # the sweep runs as the owner (postgres_admin_dsn; falls back to postgres_dsn).
    db = Database((settings.postgres_admin_dsn or settings.postgres_dsn).get_secret_value())
    await db.connect()
    try:
        results = await sweep(db, settings, apply=apply)
    finally:
        await db.close()
    verb = "deleted" if apply else "would delete"
    for artifact, rows in results.items():
        print(f"retention [{mode}]: {artifact}: {verb} {rows} row(s)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="EADIP data-retention sweeper")
    parser.add_argument(
        "--apply", action="store_true", help="execute the deletes (default: dry-run counts)"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.apply)))


if __name__ == "__main__":
    main()
