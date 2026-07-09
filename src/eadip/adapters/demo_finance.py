"""Deterministic demo finance star schema + data, shared by the warehouse
adapters (DuckDB in-process, Postgres replica) and the integration tests.

The dataset is engineered so EMEA gross margin falls from 2026-Q1 to 2026-Q2,
driven mostly by Hardware (a COGS spike) — the canonical "why did EMEA margin
fall?" demo. AMER/APAC are stable so the region filter clearly matters.
"""

from __future__ import annotations

from uuid import UUID

from eadip.ports.warehouse import ColumnSchema, TableSchema, WarehouseSchema

FINANCE_SCHEMA = WarehouseSchema(
    tables=(
        TableSchema(
            "finance_metrics",
            (
                ColumnSchema("tenant_id", "text"),
                ColumnSchema("region", "text"),
                ColumnSchema("product_line", "text"),
                ColumnSchema("period", "text"),
                ColumnSchema("revenue", "number"),
                ColumnSchema("cogs", "number"),
                ColumnSchema("opex", "number"),
            ),
        ),
    ),
    tenant_column="tenant_id",
)

PERIODS = ("2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2")

# region -> product_line -> (revenue per period, cogs per period)
_DATA: dict[str, dict[str, tuple[tuple[float, ...], tuple[float, ...]]]] = {
    "EMEA": {
        # Hardware COGS spikes in 2026-Q2 (560->820) -> the dominant driver.
        "Hardware": ((1000, 1000, 1000, 1000, 1000, 1000), (560, 580, 600, 600, 600, 820)),
        "Software": ((800, 800, 810, 810, 800, 820), (200, 205, 205, 210, 200, 210)),
        "Services": ((500, 500, 490, 500, 500, 480), (240, 245, 245, 250, 250, 250)),
    },
    "AMER": {
        "Hardware": ((1200,) * 6, (700,) * 6),
        "Software": ((900,) * 6, (250,) * 6),
        "Services": ((600,) * 6, (300,) * 6),
    },
    "APAC": {
        "Hardware": ((800,) * 6, (480,) * 6),
        "Software": ((600,) * 6, (180,) * 6),
        "Services": ((400,) * 6, (210,) * 6),
    },
}

# Column order matches FINANCE_SCHEMA and the 0004 migration's INSERT.
COLUMNS = ("tenant_id", "region", "product_line", "period", "revenue", "cogs", "opex")


def demo_finance_rows(tenant_id: UUID) -> list[tuple[str, str, str, str, float, float, float]]:
    """The full demo dataset for one tenant (54 rows)."""
    rows: list[tuple[str, str, str, str, float, float, float]] = []
    tid = str(tenant_id)
    for region, lines in _DATA.items():
        for product_line, (revenues, cogs_series) in lines.items():
            for i, period in enumerate(PERIODS):
                revenue = float(revenues[i])
                cogs = float(cogs_series[i])
                rows.append(
                    (tid, region, product_line, period, revenue, cogs, round(revenue * 0.12, 2))
                )
    return rows
