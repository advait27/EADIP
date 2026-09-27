"""NL->SQL generation (FR-020). Two implementations behind one port:

- ``TemplateSqlGenerator`` (deterministic default): renders a structured
  :class:`QueryPlan` to SQL, always injecting the tenant predicate. No model
  needed, so the pipeline is fully testable and the demo runs offline.
- ``LLMSqlGenerator`` (production seam): asks a model for SQL from the question
  + schema + safety rules, threading validator feedback on repair.

Whichever generates, the output is *always* run through ``SqlSafetyValidator``
before execution — the safety guarantee does not depend on the generator.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from eadip.analytics.models import AnalyticsRequest, QueryPlan
from eadip.ports.model_client import ModelClient
from eadip.ports.warehouse import WarehouseSchema


def _sql_str(value: str) -> str:
    """Render a single-quoted SQL string literal, escaping embedded quotes."""
    return "'" + value.replace("'", "''") + "'"


class SqlGenerator(Protocol):
    async def generate(
        self,
        request: AnalyticsRequest,
        schema: WarehouseSchema,
        plan: QueryPlan,
        *,
        tenant_id: UUID,
        feedback: str | None = None,
    ) -> str: ...


class TemplateSqlGenerator:
    """Deterministic renderer of a QueryPlan. Always emits a tenant-scoped query."""

    backend = "template"  # recorded on every SqlArtifact (provenance)

    async def generate(
        self,
        request: AnalyticsRequest,
        schema: WarehouseSchema,
        plan: QueryPlan,
        *,
        tenant_id: UUID,
        feedback: str | None = None,
    ) -> str:
        return self.render(plan, schema.tenant_column, tenant_id)

    @staticmethod
    def render(plan: QueryPlan, tenant_column: str, tenant_id: UUID) -> str:
        select_items = [*plan.dimensions, *(f"{expr} AS {alias}" for expr, alias in plan.measures)]
        distinct = "DISTINCT " if plan.distinct else ""
        sql = [f"SELECT {distinct}" + ", ".join(select_items), f"FROM {plan.table}"]

        where = [f"{tenant_column} = {_sql_str(str(tenant_id))}"]
        where += [f"{col} = {_sql_str(val)}" for col, val in plan.filters]
        if plan.period_in and plan.period_in[1]:
            col, values = plan.period_in
            joined = ", ".join(_sql_str(v) for v in values)
            where.append(f"{col} IN ({joined})")
        sql.append("WHERE " + " AND ".join(where))

        if plan.dimensions and plan.group_by:
            sql.append("GROUP BY " + ", ".join(plan.dimensions))
        if plan.order_by:
            sql.append("ORDER BY " + ", ".join(f"{c} {d}" for c, d in plan.order_by))
        if plan.limit is not None:
            sql.append(f"LIMIT {int(plan.limit)}")
        return "\n".join(sql)


_LLM_PROMPT = """\
You translate a business question into ONE read-only SQL SELECT for the
{dialect} dialect. Output SQL only — no prose, no code fences.

Hard rules (violations are rejected):
- a single SELECT statement; no INSERT/UPDATE/DELETE/DDL, no semicolons, no
  comments, no UNION, no OR;
- only these tables/columns:
{schema}
- you MUST filter to the caller's tenant: {tenant_col} = '{tenant}'
- list explicit columns (no SELECT *); aggregates need a GROUP BY.

Intent ({purpose}): {intent}
Question: {question}
{feedback}"""


class LLMSqlGenerator:
    """Production NL->SQL via a model (lazy ModelClient). Uses the QueryPlan as a
    structured intent description and feeds validator errors back on repair."""

    backend = "llm"  # recorded on every SqlArtifact (provenance)

    def __init__(self, client: ModelClient, model: str | None = None) -> None:
        self._client = client
        self._model = model

    async def generate(
        self,
        request: AnalyticsRequest,
        schema: WarehouseSchema,
        plan: QueryPlan,
        *,
        tenant_id: UUID,
        feedback: str | None = None,
    ) -> str:
        prompt = _LLM_PROMPT.format(
            dialect="duckdb",
            schema=self._schema_text(schema),
            tenant_col=schema.tenant_column,
            tenant=tenant_id,
            purpose=plan.purpose,
            intent=self._intent_text(plan),
            question=request.question,
            feedback=f"Your previous attempt was rejected: {feedback}\nFix it." if feedback else "",
        )
        resp = await self._client.complete(prompt, model=self._model)
        return self._strip(resp.text)

    @staticmethod
    def _schema_text(schema: WarehouseSchema) -> str:
        return "\n".join(
            f"  {t.name}({', '.join(c.name for c in t.columns)})" for t in schema.tables
        )

    @staticmethod
    def _intent_text(plan: QueryPlan) -> str:
        parts = [f"aggregate {[a for _, a in plan.measures]}"]
        if plan.dimensions:
            parts.append(f"grouped by {list(plan.dimensions)}")
        if plan.filters:
            parts.append("filtered by " + ", ".join(f"{c}={v}" for c, v in plan.filters))
        if plan.period_in:
            col, values = plan.period_in
            parts.append(f"for {col} in {list(values)}")
        return "; ".join(parts)

    @staticmethod
    def _strip(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()
        return text.rstrip(";").strip()
