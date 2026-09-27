"""Forward-only SQL migration runner.

Applies `sql/*.sql` in lexical order, tracking applied files in
`schema_migrations`. Each file runs in its own transaction. Re-running is safe:
already-applied files are skipped, and the SQL itself uses idempotent guards.

CLI: `eadip-migrate` (or `python -m eadip.adapters.migrations`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from eadip.adapters.postgres import Database

SQL_DIR = Path(__file__).parent / "sql"

_TRACK_TABLE = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "  name TEXT PRIMARY KEY,"
    "  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    ")"
)


async def apply_migrations(db: Database) -> list[str]:
    async with db.connection() as conn:
        await conn.execute(_TRACK_TABLE)
        applied = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        ran: list[str] = []
        for path in sorted(SQL_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
            ran.append(path.name)
        return ran


def main() -> None:
    from eadip.config.settings import get_settings

    async def _run() -> None:
        settings = get_settings()
        # Schema changes run as the owner, not the app role (RLS/least privilege).
        dsn = settings.postgres_admin_dsn or settings.postgres_dsn
        db = Database(dsn.get_secret_value())
        await db.connect()
        try:
            ran = await apply_migrations(db)
            print("migrations applied:", ", ".join(ran) if ran else "none (up to date)")
        finally:
            await db.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
