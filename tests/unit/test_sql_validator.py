"""The NL->SQL safety gate (FR-021): the security core of Phase 5.

These tests are the proof behind the DoD "no non-read-only SQL can execute" and
"every query is tenant-scoped". They run with no engine — the validator is the
only thing under test.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from eadip.analytics.sql_validator import SqlSafetyValidator, SqlValidationError
from eadip.ports.warehouse import ColumnSchema, TableSchema, WarehouseSchema

SCHEMA = WarehouseSchema(
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
            ),
        ),
        TableSchema(
            "dim_region",
            (
                ColumnSchema("tenant_id", "text"),
                ColumnSchema("region", "text"),
                ColumnSchema("manager", "text"),
            ),
        ),
    ),
)
T = UUID("11111111-1111-1111-1111-111111111111")
OTHER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def v() -> SqlSafetyValidator:
    return SqlSafetyValidator(SCHEMA, dialect="duckdb", max_join_tables=3)


def _w() -> str:
    return f"tenant_id = '{T}'"


# --- accepted ----------------------------------------------------------------
def test_accepts_tenant_scoped_aggregate(v: SqlSafetyValidator) -> None:
    validated = v.validate(
        f"SELECT product_line, SUM(revenue - cogs) AS m FROM finance_metrics "
        f"WHERE {_w()} AND region = 'EMEA' GROUP BY product_line",
        tenant_id=T,
    )
    assert "finance_metrics" in validated.tables
    assert validated.sql  # re-rendered SQL is what executes


def test_accepts_count_star_and_cte(v: SqlSafetyValidator) -> None:
    v.validate(f"SELECT COUNT(*) AS n FROM finance_metrics WHERE {_w()}", tenant_id=T)
    v.validate(
        f"WITH q AS (SELECT region, SUM(revenue) AS r FROM finance_metrics WHERE {_w()} "
        f"GROUP BY region) SELECT region, r FROM q WHERE r > 0",
        tenant_id=T,
    )


def test_accepts_keyed_join_within_table_budget(v: SqlSafetyValidator) -> None:
    v.validate(
        f"SELECT f.region, SUM(f.revenue) AS r FROM finance_metrics f "
        f"JOIN dim_region d ON f.region = d.region AND d.tenant_id = '{T}' "
        f"WHERE f.tenant_id = '{T}' GROUP BY f.region",
        tenant_id=T,
    )


# --- read-only enforcement ---------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM finance_metrics",
        "UPDATE finance_metrics SET revenue = 0",
        "INSERT INTO finance_metrics VALUES (1)",
        "DROP TABLE finance_metrics",
        "ALTER TABLE finance_metrics ADD COLUMN x INT",
        "TRUNCATE finance_metrics",
    ],
)
def test_rejects_non_select(v: SqlSafetyValidator, sql: str) -> None:
    with pytest.raises(SqlValidationError):
        v.validate(sql, tenant_id=T)


def test_rejects_multiple_statements(v: SqlSafetyValidator) -> None:
    with pytest.raises(SqlValidationError, match="one statement"):
        v.validate(
            f"SELECT region FROM finance_metrics WHERE {_w()}; DROP TABLE finance_metrics",
            tenant_id=T,
        )


def test_rejects_set_operations_and_or(v: SqlSafetyValidator) -> None:
    with pytest.raises(SqlValidationError):
        v.validate(
            f"SELECT region FROM finance_metrics WHERE {_w()} "
            f"UNION SELECT manager FROM dim_region WHERE tenant_id = '{T}'",
            tenant_id=T,
        )
    with pytest.raises(SqlValidationError, match="OR"):
        v.validate(
            f"SELECT region FROM finance_metrics WHERE tenant_id = '{T}' OR tenant_id = '{OTHER}'",
            tenant_id=T,
        )


# --- schema allowlist --------------------------------------------------------
def test_rejects_select_star(v: SqlSafetyValidator) -> None:
    with pytest.raises(SqlValidationError, match="SELECT \\*"):
        v.validate(f"SELECT * FROM finance_metrics WHERE {_w()}", tenant_id=T)


def test_rejects_unknown_table_and_column(v: SqlSafetyValidator) -> None:
    with pytest.raises(SqlValidationError, match="table"):
        v.validate(f"SELECT region FROM secret WHERE {_w()}", tenant_id=T)
    with pytest.raises(SqlValidationError, match="column"):
        v.validate(f"SELECT salary FROM finance_metrics WHERE {_w()}", tenant_id=T)


def test_rejects_schema_qualified_table(v: SqlSafetyValidator) -> None:
    with pytest.raises(SqlValidationError):
        v.validate(f"SELECT region FROM pg_catalog.pg_tables WHERE {_w()}", tenant_id=T)
    with pytest.raises(SqlValidationError):
        v.validate(f"SELECT tablename FROM information_schema.tables WHERE {_w()}", tenant_id=T)


def test_rejects_file_and_system_functions(v: SqlSafetyValidator) -> None:
    # The whole point: engine file/system access is denied (they parse unknown).
    for fn in ("read_csv_auto('/etc/passwd')", "pg_read_file('x')", "glob('*')"):
        with pytest.raises(SqlValidationError, match="function"):
            v.validate(f"SELECT {fn} AS x FROM finance_metrics WHERE {_w()}", tenant_id=T)


# --- tenant scoping ----------------------------------------------------------
def test_requires_tenant_predicate(v: SqlSafetyValidator) -> None:
    with pytest.raises(SqlValidationError, match="tenant predicate is required"):
        v.validate("SELECT region FROM finance_metrics WHERE region = 'EMEA'", tenant_id=T)


def test_rejects_foreign_or_loose_tenant_predicate(v: SqlSafetyValidator) -> None:
    with pytest.raises(SqlValidationError, match="match"):
        v.validate(f"SELECT region FROM finance_metrics WHERE tenant_id = '{OTHER}'", tenant_id=T)
    for pred in (
        f"tenant_id != '{T}'",
        f"tenant_id IN ('{T}', '{OTHER}')",
        f"tenant_id LIKE '{T}'",
    ):
        with pytest.raises(SqlValidationError):
            v.validate(f"SELECT region FROM finance_metrics WHERE {pred}", tenant_id=T)


# --- cost guard --------------------------------------------------------------
def test_rejects_cartesian_join(v: SqlSafetyValidator) -> None:
    with pytest.raises(SqlValidationError, match="cartesian"):
        v.validate(
            f"SELECT region FROM finance_metrics f, dim_region d WHERE f.tenant_id = '{T}'",
            tenant_id=T,
        )


def test_rejects_too_many_tables() -> None:
    val = SqlSafetyValidator(SCHEMA, dialect="duckdb", max_join_tables=1)
    with pytest.raises(SqlValidationError, match="too many"):
        val.validate(
            f"SELECT f.region FROM finance_metrics f "
            f"JOIN dim_region d ON f.region = d.region AND d.tenant_id = '{T}' "
            f"WHERE f.tenant_id = '{T}'",
            tenant_id=T,
        )
