"""DuckDB warehouse: read-only execution + row-cap truncation (Phase 5).

DuckDB ships in the `data` extra; these skip when it is not installed.
"""

from __future__ import annotations

import importlib.util
from uuid import uuid4

import pytest

from eadip.ports.warehouse import WarehouseError

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("duckdb") is None, reason="duckdb not installed"
)


async def test_seeded_query_returns_tenant_rows() -> None:
    from eadip.adapters.duckdb_warehouse import DuckDBWarehouse

    wh = DuckDBWarehouse()
    t = uuid4()
    result = await wh.execute(
        t,
        f"SELECT region, SUM(revenue) AS rev FROM finance_metrics "
        f"WHERE tenant_id = '{t}' GROUP BY region",
        row_cap=100,
        timeout_s=5,
    )
    regions = {row[0] for row in result.rows}
    assert {"EMEA", "AMER", "APAC"} <= regions
    assert not result.truncated


async def test_write_statements_are_structurally_refused() -> None:
    from eadip.adapters.duckdb_warehouse import DuckDBWarehouse

    wh = DuckDBWarehouse()
    t = uuid4()
    await wh.execute(
        t, f"SELECT region FROM finance_metrics WHERE tenant_id = '{t}'", row_cap=10, timeout_s=5
    )
    # Defence in depth (Phase 12): every statement is executed as a table
    # subquery — anything that is not a SELECT is a syntax error at the engine,
    # even though the AST validator would already have blocked it upstream.
    with pytest.raises(WarehouseError):
        await wh.execute(
            t,
            "INSERT INTO finance_metrics VALUES ('a','b','c','d',1,1,1)",
            row_cap=10,
            timeout_s=5,
        )
    # And the data is unchanged.
    count = await wh.execute(
        t, "SELECT count(*) FROM finance_metrics WHERE tenant_id = 'a'", row_cap=10, timeout_s=5
    )
    assert count.rows[0][0] == 0


async def test_concurrent_multi_tenant_queries_do_not_serialize_errors() -> None:
    """Regression for the Phase 12 hot-path fix: concurrent seeding + querying
    across many tenants runs on MVCC cursors without conflicts."""
    import asyncio

    from eadip.adapters.duckdb_warehouse import DuckDBWarehouse

    wh = DuckDBWarehouse()
    tenants = [uuid4() for _ in range(12)]

    async def query(t):  # type: ignore[no-untyped-def]
        return await wh.execute(
            t,
            f"SELECT region, SUM(revenue) FROM finance_metrics "
            f"WHERE tenant_id = '{t}' GROUP BY region",
            row_cap=100,
            timeout_s=5,
        )

    results = await asyncio.gather(*(query(t) for t in tenants for _ in range(3)))
    assert all(len(r.rows) >= 3 for r in results)  # every tenant sees its rows


async def test_row_cap_truncation() -> None:
    from eadip.adapters.duckdb_warehouse import DuckDBWarehouse

    wh = DuckDBWarehouse()
    t = uuid4()
    result = await wh.execute(
        t,
        f"SELECT region, product_line, period FROM finance_metrics WHERE tenant_id = '{t}'",
        row_cap=2,
        timeout_s=5,
    )
    assert result.truncated is True
    assert result.row_count == 2


async def test_execution_error_becomes_warehouse_error() -> None:
    from eadip.adapters.duckdb_warehouse import DuckDBWarehouse

    wh = DuckDBWarehouse()
    t = uuid4()
    with pytest.raises(WarehouseError):
        await wh.execute(t, "SELECT nope FROM nonexistent_table", row_cap=10, timeout_s=5)
