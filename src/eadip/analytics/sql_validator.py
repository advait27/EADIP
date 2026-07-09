"""NL->SQL safety gate (FR-021, TAD Ch 8.1, NFR-06) — the analytics crown jewel.

Every candidate query (heuristic- or LLM-generated) passes through here before a
single row can be read. Deny-by-default: the query is parsed to an AST with
sqlglot and rejected unless it satisfies *all* of:

  1. exactly one statement, and it is a read-only ``SELECT`` (no DML/DDL/commands
     anywhere in the tree);
  2. no set operations (UNION/EXCEPT/INTERSECT) and no ``OR`` — so the WHERE
     analysis below is sound and cannot be widened past the tenant;
  3. only tables from the warehouse schema (no schema-qualified / catalog access),
     and only columns that exist in the schema or are defined as aliases;
  4. only allow-listed functions — this is what blocks DuckDB/Postgres file and
     system functions (``read_csv``, ``glob``, ``pg_read_file``, …);
  5. a required tenant predicate ``<tenant_col> = '<this tenant>'`` and *no*
     comparison of the tenant column to any other value;
  6. a cost guard: every JOIN is keyed (no cartesian product) and the number of
     base tables is bounded.

The validator returns the *re-rendered* SQL (comments and incidental formatting
stripped), which is what actually executes — so comment-based tricks die here
too. Engine-level read-only execution (see the warehouse adapters) is the second
line of defence behind this gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import sqlglot
from sqlglot import exp

from eadip.ports.warehouse import WarehouseSchema

# Aggregates + safe scalar/date/math functions. Anything else (notably the
# engines' file/system functions, which parse as unknown/anonymous) is denied.
DEFAULT_ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "SUM", "AVG", "COUNT", "MIN", "MAX", "MEDIAN",
        "STDDEV", "STDDEV_POP", "STDDEV_SAMP",
        "VARIANCE", "VAR_POP", "VAR_SAMP",
        "ABS", "ROUND", "CEIL", "CEILING", "FLOOR", "SIGN",
        "POWER", "POW", "SQRT", "EXP", "LN", "LOG", "MOD",
        "COALESCE", "NULLIF", "GREATEST", "LEAST", "CAST", "TRY_CAST",
        "EXTRACT", "DATE_TRUNC", "DATE_PART",
        "LOWER", "UPPER", "LENGTH",
    }
)  # fmt: skip

# Statement / clause node types that must never appear anywhere in the tree.
_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Merge",
    "Command", "Set", "SetItem", "Pragma", "Use", "TruncateTable",
    "Copy", "AlterTable", "Grant", "Transaction", "Commit", "Rollback",
    "Attach", "Detach", "LoadData",
)  # fmt: skip
_FORBIDDEN_NODES: tuple[type, ...] = tuple(
    getattr(exp, name) for name in _FORBIDDEN_NODE_NAMES if hasattr(exp, name)
)

_TENANT_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Is)


@dataclass(frozen=True)
class ValidatedSql:
    """A query proven safe to execute, plus what it touches (for the audit log)."""

    sql: str  # re-rendered, sanitised SQL — this is what runs
    dialect: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]


@dataclass(frozen=True)
class SqlValidationError(Exception):
    """A query failed the safety gate. ``reason`` is safe to surface/audit."""

    reason: str
    detail: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.reason


class SqlSafetyValidator:
    def __init__(
        self,
        schema: WarehouseSchema,
        *,
        dialect: str = "duckdb",
        max_join_tables: int = 4,
        allowed_functions: frozenset[str] = DEFAULT_ALLOWED_FUNCTIONS,
    ) -> None:
        self._schema = schema
        self._dialect = dialect
        self._max_tables = max_join_tables
        self._allowed_functions = allowed_functions
        self._tenant_col = schema.tenant_column.lower()

    def validate(self, sql: str, *, tenant_id: UUID) -> ValidatedSql:
        root = self._parse_single_select(sql)
        self._reject_forbidden_nodes(root)
        self._reject_set_ops_and_or(root)
        self._reject_star(root)
        tables = self._check_tables(root)
        self._check_functions(root)
        columns = self._check_columns(root)
        self._check_tenant_predicate(root, tenant_id)
        self._check_cost_guard(root, tables)
        return ValidatedSql(
            sql=root.sql(dialect=self._dialect),
            dialect=self._dialect,
            tables=tuple(sorted(tables)),
            columns=tuple(sorted(columns)),
        )

    # --- parsing --------------------------------------------------------------
    def _parse_single_select(self, sql: str) -> exp.Select:
        try:
            statements = sqlglot.parse(sql, read=self._dialect)
        except Exception as exc:  # noqa: BLE001 — any parse failure => reject
            raise SqlValidationError("sql failed to parse", {"error": str(exc)}) from exc
        statements = [s for s in statements if s is not None]
        if len(statements) != 1:
            raise SqlValidationError(
                "exactly one statement is allowed", {"count": str(len(statements))}
            )
        root = statements[0]
        if not isinstance(root, exp.Select):
            raise SqlValidationError(
                "only read-only SELECT statements are allowed",
                {"statement": type(root).__name__},
            )
        return root

    # --- structural rejections ------------------------------------------------
    @staticmethod
    def _reject_forbidden_nodes(root: exp.Select) -> None:
        for node in root.walk():
            if isinstance(node, _FORBIDDEN_NODES):
                raise SqlValidationError(
                    "non-read-only statement is not allowed",
                    {"node": type(node).__name__},
                )

    @staticmethod
    def _reject_set_ops_and_or(root: exp.Select) -> None:
        if any(root.find_all(exp.Union, exp.Except, exp.Intersect)):
            raise SqlValidationError("set operations (UNION/EXCEPT/INTERSECT) are not allowed")
        if any(root.find_all(exp.Or)):
            raise SqlValidationError("OR is not allowed (keeps tenant scoping sound)")

    @staticmethod
    def _reject_star(root: exp.Select) -> None:
        # `SELECT *` / `t.*` are rejected (explicit columns only, for provenance);
        # `COUNT(*)` is fine — its Star is an argument to the Count node.
        for star in root.find_all(exp.Star):
            if not isinstance(star.parent, exp.Count):
                raise SqlValidationError("SELECT * is not allowed; list explicit columns")

    # --- allowlists -----------------------------------------------------------
    @staticmethod
    def _derived_names(root: exp.Select) -> set[str]:
        names: set[str] = set()
        for cte in root.find_all(exp.CTE):
            if cte.alias:
                names.add(cte.alias.lower())
        for sub in root.find_all(exp.Subquery):
            if sub.alias:
                names.add(sub.alias.lower())
        return names

    def _check_tables(self, root: exp.Select) -> set[str]:
        derived = self._derived_names(root)
        allowed = self._schema.table_names()
        seen: set[str] = set()
        for tbl in root.find_all(exp.Table):
            name = tbl.name.lower()
            if name in derived:
                continue  # reference to a CTE / derived subquery, not a base table
            if tbl.args.get("db") or tbl.args.get("catalog"):
                raise SqlValidationError(
                    "schema/catalog-qualified tables are not allowed", {"table": tbl.sql()}
                )
            if name not in allowed:
                raise SqlValidationError("unknown table", {"table": name})
            seen.add(name)
        if not seen:
            raise SqlValidationError("query references no known table")
        return seen

    def _check_functions(self, root: exp.Select) -> None:
        for func in root.find_all(exp.Func):
            # In sqlglot, operators (AND/OR/arithmetic) also subclass Func; those
            # are not callable functions, so the allowlist applies only to real
            # function calls (aggregates, scalars, and unknown/anonymous funcs).
            if isinstance(func, (exp.Binary, exp.Unary)):
                continue
            name = func.name if isinstance(func, exp.Anonymous) else func.sql_name()
            if name.upper() not in self._allowed_functions:
                raise SqlValidationError("function is not allowed", {"function": name})

    def _check_columns(self, root: exp.Select) -> set[str]:
        known = self._schema.all_columns()
        defined = {a.alias.lower() for a in root.find_all(exp.Alias) if a.alias}
        defined |= {t.alias.lower() for t in root.find_all(exp.TableAlias) if t.alias}
        seen: set[str] = set()
        for col in root.find_all(exp.Column):
            name = col.name.lower()
            if not name:  # e.g. a bare `t.*` handled by the star check
                continue
            if name not in known and name not in defined:
                raise SqlValidationError("unknown column", {"column": name})
            seen.add(name)
        return seen

    # --- tenant scoping -------------------------------------------------------
    def _check_tenant_predicate(self, root: exp.Select, tenant_id: UUID) -> None:
        expected = str(tenant_id)
        bound_to_expected = False
        for cmp_node in root.find_all(*_TENANT_COMPARISONS):
            operands = (cmp_node.this, cmp_node.args.get("expression"))
            if not any(self._is_tenant_column(o) for o in operands):
                continue
            if not isinstance(cmp_node, exp.EQ):
                raise SqlValidationError(
                    "the tenant column may only be compared with =",
                    {"op": type(cmp_node).__name__},
                )
            other = cmp_node.args.get("expression")
            if self._is_tenant_column(cmp_node.this) is False:
                other = cmp_node.this
            if not (isinstance(other, exp.Literal) and other.is_string):
                raise SqlValidationError("tenant predicate must compare to a string literal")
            if other.this != expected:
                raise SqlValidationError(
                    "tenant predicate does not match the caller's tenant",
                    {"found": str(other.this)},
                )
            bound_to_expected = True
        # IN / LIKE on the tenant column are never acceptable.
        for in_node in root.find_all(exp.In, exp.Like, exp.ILike):
            if self._is_tenant_column(in_node.this):
                raise SqlValidationError("the tenant column may only be compared with =")
        if not bound_to_expected:
            raise SqlValidationError("a tenant predicate is required (deny-by-default)")

    def _is_tenant_column(self, node: exp.Expression | None) -> bool:
        return isinstance(node, exp.Column) and node.name.lower() == self._tenant_col

    # --- cost guard -----------------------------------------------------------
    def _check_cost_guard(self, root: exp.Select, tables: set[str]) -> None:
        for join in root.find_all(exp.Join):
            if not (join.args.get("on") or join.args.get("using")):
                raise SqlValidationError("cartesian/cross joins are not allowed")
        # Count *references* (a self-join of one table still counts twice).
        derived = self._derived_names(root)
        refs = sum(1 for t in root.find_all(exp.Table) if t.name.lower() not in derived)
        if refs > self._max_tables:
            raise SqlValidationError(
                "too many joined tables", {"tables": str(refs), "max": str(self._max_tables)}
            )
