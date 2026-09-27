"""Bring your own data (Glass Box): CSV -> governed, tenant-scoped warehouse table.

A CSV becomes a per-tenant table the NL->SQL engine can query, behind the same
safety gate as the demo data: the table joins the tenant's schema allowlist, a
``tenant_id`` column is injected, and the logical name in provenance SQL is
rewritten to the physical, tenant-prefixed table only at execution time.

Header normalisation makes every column a bare, unquoted SQL identifier
(``[a-z][a-z0-9_]*``) so the template generator and the validator work
unchanged: reserved words and allow-listed function names get a ``_col``
suffix, an unusable header becomes ``col_<position>``, duplicates get ``_2``,
``_3``…, and a source column named ``tenant_id`` is refused (it is ours).
Types are inferred per column (``number`` when every non-empty value parses as
a float, else ``text``); the period column is forced to ``text`` so the
verifier's "one numeric column" recompute inference keeps holding.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from eadip.analytics.sql_validator import DEFAULT_ALLOWED_FUNCTIONS
from eadip.ports.warehouse import ColumnSchema, TableSchema

PERIOD_HINTS = ("period", "quarter", "month", "date", "year", "week", "fiscal")
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED = frozenset(
    {
        "select",
        "from",
        "where",
        "group",
        "by",
        "order",
        "limit",
        "as",
        "and",
        "or",
        "not",
        "in",
        "is",
        "null",
        "distinct",
        "having",
        "join",
        "on",
        "union",
        "all",
        "case",
        "when",
        "then",
        "else",
        "end",
        "table",
        "column",
        "insert",
        "update",
        "delete",
        "create",
        "drop",
        "into",
        "values",
        "set",
        "between",
        "like",
        "exists",
        "default",
        "primary",
        "key",
        "index",
        "user",
        "with",
        "asc",
        "desc",
        "true",
        "false",
        "cast",
        "type",
        "over",
        "partition",
        "window",
        "using",
        "natural",
        "left",
        "right",
        "full",
        "inner",
        "outer",
        "cross",
        "offset",
        "fetch",
        "first",
        "last",
        "rows",
        "row",
        "range",
    }
    | {f.lower() for f in DEFAULT_ALLOWED_FUNCTIONS}
)
MAX_PROFILE_VALUES = 64


class DatasetError(ValueError):
    """Client-safe reason a CSV could not become a table."""


class DatasetTooLarge(DatasetError):
    """The upload exceeds a configured byte/row/column cap (HTTP 413)."""


def normalise_header(raw: str, position: int, taken: set[str]) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    name = re.sub(r"^[^a-z]+", "", name)
    if not name or not _IDENT.match(name):
        name = f"col_{position}"
    if name == "tenant_id":
        raise DatasetError("a source column may not be named tenant_id (it is injected)")
    if name in _RESERVED:
        name = f"{name}_col"
    base, n = name, 2
    while name in taken:
        name = f"{base}_{n}"
        n += 1
    taken.add(name)
    return name


def slugify_table(filename: str) -> str:
    stem = re.sub(r"\.[a-z0-9]+$", "", filename.strip().lower())
    slug = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    slug = re.sub(r"^[^a-z]+", "", slug)
    if not slug or not _IDENT.match(slug):
        raise DatasetError("could not derive a table name from the filename")
    if slug in _RESERVED:
        slug = f"{slug}_data"
    return slug[:48]


def _is_number(v: str) -> bool:
    """Finite numbers only: ``nan``/``inf`` parse as floats but are not valid
    JSON and would poison every aggregate, so they make the column text."""
    try:
        return math.isfinite(float(v.replace(",", "")))
    except ValueError:
        return False


@dataclass
class ParsedDataset:
    columns: list[ColumnSchema]
    rows: list[list[Any]]  # typed: floats for number columns, str (or None) for text
    period_column: str | None
    # text column -> distinct values (up to MAX_PROFILE_VALUES; None when more)
    profile: dict[str, tuple[str, ...] | None] = field(default_factory=dict)
    dimensions: tuple[str, ...] = ()  # text columns by ascending cardinality, period excluded

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def table_schema(self, name: str) -> TableSchema:
        return TableSchema(
            name,
            (ColumnSchema("tenant_id", "text"), *self.columns),
            period_column=self.period_column,
            dimensions=self.dimensions,
            sample_values=tuple((c, v) for c, v in self.profile.items() if v is not None),
        )


def parse_csv(
    data: bytes,
    *,
    max_bytes: int = 5_000_000,
    max_columns: int = 32,
    max_rows: int = 100_000,
) -> ParsedDataset:
    if len(data) > max_bytes:
        raise DatasetTooLarge(f"file exceeds {max_bytes} bytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DatasetError("file is not UTF-8 text") from exc
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise DatasetError("file is empty") from None
    except csv.Error as exc:
        raise DatasetError(f"malformed CSV: {exc}") from None
    if not any(h.strip() for h in header):
        raise DatasetError("header row is empty")
    if len(header) > max_columns:
        raise DatasetTooLarge(f"more than {max_columns} columns")
    taken: set[str] = set()
    names = [normalise_header(h, i + 1, taken) for i, h in enumerate(header)]

    raw_rows: list[list[str]] = []
    try:
        for row in reader:
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) < len(names):
                row = row + [""] * (len(names) - len(row))
            raw_rows.append([c.strip() for c in row[: len(names)]])
            if len(raw_rows) > max_rows:
                raise DatasetTooLarge(f"more than {max_rows} rows")
    except csv.Error as exc:
        raise DatasetError(f"malformed CSV: {exc}") from None
    if not raw_rows:
        raise DatasetError("no data rows")

    period = next((n for n in names if any(h in n for h in PERIOD_HINTS)), None)
    types: dict[str, str] = {}
    for i, n in enumerate(names):
        values = [r[i] for r in raw_rows if r[i] != ""]
        numeric = bool(values) and all(_is_number(v) for v in values)
        types[n] = "text" if n == period else ("number" if numeric else "text")
    if period is None:
        period = next((n for n in names if types[n] == "text"), None)
    if not any(t == "number" for t in types.values()):
        raise DatasetError("no numeric column found; nothing to measure")
    if period is None:
        # Analytics always compares across a period/dimension column; a table
        # of nothing but numbers can be stored but never questioned.
        raise DatasetError(
            "no period or text column found; add a date, quarter, month or category column"
        )

    rows: list[list[Any]] = []
    for r in raw_rows:
        typed: list[Any] = []
        for i, n in enumerate(names):
            v = r[i]
            if types[n] == "number":
                typed.append(float(v.replace(",", "")) if v != "" else None)
            else:
                typed.append(v if v != "" else None)
        rows.append(typed)

    profile: dict[str, tuple[str, ...] | None] = {}
    cardinality: dict[str, int] = {}
    for i, n in enumerate(names):
        if types[n] != "text":
            continue
        counts = Counter(str(r[i]) for r in rows if r[i] is not None)
        cardinality[n] = len(counts)
        profile[n] = tuple(sorted(counts)) if len(counts) <= MAX_PROFILE_VALUES else None
    dimensions = tuple(
        sorted((n for n in cardinality if n != period), key=lambda n: (cardinality[n], n))
    )
    return ParsedDataset(
        columns=[ColumnSchema(n, types[n]) for n in names],
        rows=rows,
        period_column=period,
        profile=profile,
        dimensions=dimensions,
    )


@dataclass(frozen=True)
class DatasetEntry:
    tenant_id: UUID
    name: str  # logical (what the question and provenance SQL use)
    physical: str  # what DuckDB stores (tenant-prefixed)
    schema: TableSchema
    row_count: int


class DatasetVocabulary(Protocol):
    """What a tenant can ask about beyond the built-in metrics: each uploaded
    table with its numeric (measurable) columns. Consulted by the goal
    interpreter so a question naming a dataset gets an analytics step."""

    def vocabulary(self, tenant_id: UUID) -> list[tuple[str, tuple[str, ...]]]: ...


class DatasetRegistry(DatasetVocabulary, Protocol):
    def register(self, entry: DatasetEntry) -> None: ...

    def get(self, tenant_id: UUID, name: str) -> DatasetEntry | None: ...

    def entries(self, tenant_id: UUID) -> list[DatasetEntry]: ...

    def remove(self, tenant_id: UUID, name: str) -> DatasetEntry | None: ...


class InMemoryDatasetRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[UUID, str], DatasetEntry] = {}

    def register(self, entry: DatasetEntry) -> None:
        self._entries[(entry.tenant_id, entry.name)] = entry

    def get(self, tenant_id: UUID, name: str) -> DatasetEntry | None:
        return self._entries.get((tenant_id, name.lower()))

    def entries(self, tenant_id: UUID) -> list[DatasetEntry]:
        return sorted(
            (e for (t, _), e in self._entries.items() if t == tenant_id), key=lambda e: e.name
        )

    def remove(self, tenant_id: UUID, name: str) -> DatasetEntry | None:
        return self._entries.pop((tenant_id, name.lower()), None)

    def vocabulary(self, tenant_id: UUID) -> list[tuple[str, tuple[str, ...]]]:
        return [(e.name, e.schema.numeric_columns()) for e in self.entries(tenant_id)]


def physical_name(tenant_id: UUID, name: str) -> str:
    # The full tenant id: a prefix could collide across tenants, and
    # register_dataset DROPs + recreates the physical table it maps to.
    return f"ds_{tenant_id.hex}_{name}"
