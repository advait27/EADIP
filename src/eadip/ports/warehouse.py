"""Analytical data-plane port (AP-10, TAD Ch 8.1).

The warehouse exposes a *governed* relational surface for NL->SQL: a typed
schema (so the safety gate knows which tables/columns exist) and a read-only
`execute` that runs already-validated SQL under a row cap + statement timeout.
Concrete engines (DuckDB in-process, Postgres read-replica) live in
`eadip.adapters` and are imported lazily.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    type: str  # logical type label, e.g. "text" | "number" | "date"

    @property
    def is_numeric(self) -> bool:
        return self.type == "number"


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: tuple[ColumnSchema, ...]
    # Interpretation hints for uploaded datasets (Glass Box). All optional so the
    # demo/finance schema is unchanged: the time axis, candidate driver
    # dimensions by ascending cardinality, and small value sets per text column
    # (so a filter value can be lifted from a question without touching data).
    period_column: str | None = None
    dimensions: tuple[str, ...] = ()
    sample_values: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def column_names(self) -> frozenset[str]:
        return frozenset(c.name.lower() for c in self.columns)

    def numeric_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.is_numeric)

    def values_for(self, column: str) -> tuple[str, ...]:
        return next((v for c, v in self.sample_values if c == column), ())


@dataclass(frozen=True)
class WarehouseSchema:
    """The tables/columns NL->SQL may target, plus the tenant-scoping column.

    The safety validator is configured from this object (deny-by-default: only
    these tables and columns are reachable), so the schema is the allowlist.
    """

    tables: tuple[TableSchema, ...]
    tenant_column: str = "tenant_id"

    def table_names(self) -> frozenset[str]:
        return frozenset(t.name.lower() for t in self.tables)

    def all_columns(self) -> frozenset[str]:
        cols: set[str] = set()
        for t in self.tables:
            cols |= t.column_names()
        return frozenset(cols)

    def table(self, name: str) -> TableSchema | None:
        lname = name.lower()
        return next((t for t in self.tables if t.name.lower() == lname), None)


@dataclass(frozen=True)
class QueryResult:
    """A query's result set, bound (by the caller) to the exact SQL that produced
    it — the provenance seed every downstream claim carries (FR-024)."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool = False  # row cap hit -> not the full result

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def column_index(self, name: str) -> int:
        return self.columns.index(name)

    def column(self, name: str) -> list[Any]:
        i = self.column_index(name)
        return [r[i] for r in self.rows]

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r, strict=True)) for r in self.rows]


def query_result(
    columns: Iterable[str], rows: Iterable[Sequence[Any]], *, truncated: bool = False
) -> QueryResult:
    return QueryResult(
        columns=tuple(columns),
        rows=tuple(tuple(r) for r in rows),
        truncated=truncated,
    )


class Warehouse(Protocol):
    """Read-only analytical store. Implementations MUST run statements read-only
    at the engine level (defence in depth behind the AST validator)."""

    async def schema(self, tenant_id: UUID) -> WarehouseSchema: ...

    async def execute(
        self,
        tenant_id: UUID,
        sql: str,
        *,
        row_cap: int,
        timeout_s: float,
    ) -> QueryResult: ...


@dataclass(frozen=True)
class WarehouseError(Exception):
    """Raised when the engine refuses or fails to run a (validated) statement."""

    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message
