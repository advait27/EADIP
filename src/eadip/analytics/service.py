"""Analytics orchestration (TAD Ch 8.1/8.2, FR-020..024).

For a metric-movement question the service:
  1. interprets the question into a metric + dimension + filter + two periods
     (heuristically, or from explicit hints);
  2. generates SQL (template or LLM) for each query it needs;
  3. runs every query through the safety gate, then the read-only warehouse,
     with a bounded repair loop on validation/empty results and a result cache;
  4. computes driver attribution, anomaly detection, a forecast, and
     correlations (with multiple-comparison control);
  5. binds every finding to the exact SQL that produced it (provenance seed).

The safety gate is the single chokepoint: no query reaches the warehouse without
passing :class:`SqlSafetyValidator`, so non-read-only / cross-tenant SQL can
never execute.
"""

from __future__ import annotations

import re

from eadip.analytics.cache import SqlResultCache
from eadip.analytics.models import (
    AnalysisResult,
    AnalyticsRequest,
    Finding,
    QueryPlan,
    SqlArtifact,
)
from eadip.analytics.sql_generator import SqlGenerator
from eadip.analytics.sql_validator import SqlSafetyValidator, SqlValidationError
from eadip.analytics.statistics import (
    Driver,
    attribute_change,
    correlate,
    detect_anomalies,
    linear_forecast,
)
from eadip.observability.logging import get_logger
from eadip.ports.warehouse import (
    QueryResult,
    TableSchema,
    Warehouse,
    WarehouseError,
    WarehouseSchema,
)

log = get_logger(__name__)

# Logical metric name -> (additive SQL expression, human label).
_METRICS: dict[str, tuple[str, str]] = {
    "gross_margin": ("revenue - cogs", "gross margin"),
    "margin": ("revenue - cogs", "gross margin"),
    "revenue": ("revenue", "revenue"),
    "cogs": ("cogs", "COGS"),
    "opex": ("opex", "operating expense"),
}
# Region-like filter tokens we can lift straight out of the question.
_FILTER_TOKENS = ("EMEA", "AMER", "APAC", "LATAM", "NA", "EU", "US", "UK")


class AnalyticsService:
    def __init__(
        self,
        *,
        warehouse: Warehouse,
        generator: SqlGenerator,
        cache: SqlResultCache,
        row_cap: int = 10000,
        statement_timeout_s: float = 5.0,
        max_join_tables: int = 4,
        max_repair_attempts: int = 2,
        correlation_alpha: float = 0.05,
        default_table: str = "finance_metrics",
        default_metric: str = "gross_margin",
        default_dimension: str = "product_line",
    ) -> None:
        self._wh = warehouse
        self._gen = generator
        self._cache = cache
        self._row_cap = row_cap
        self._timeout = statement_timeout_s
        self._max_tables = max_join_tables
        self._max_repairs = max_repair_attempts
        self._alpha = correlation_alpha
        self._default_table = default_table
        self._default_metric = default_metric
        self._default_dimension = default_dimension

    async def analyze(self, request: AnalyticsRequest) -> AnalysisResult:
        schema = await self._wh.schema(request.tenant_id)
        validator = SqlSafetyValidator(schema, dialect="duckdb", max_join_tables=self._max_tables)
        plan = self._interpret(request, schema)
        warnings: list[str] = []
        queries: list[SqlArtifact] = []

        baseline_p, current_p = await self._resolve_periods(
            request, plan, schema, validator, queries, warnings
        )

        drivers: list[Driver] = []
        findings: list[Finding] = []
        verified = False
        headline = ""
        metric_label = plan.metric_label

        # --- driver attribution over baseline vs current --------------------
        total: float | None = None
        if baseline_p and current_p:
            driver_plan = self._driver_plan(plan, (baseline_p, current_p))
            art, result = await self._run(driver_plan, request, schema, validator)
            if art:
                queries.append(art)
            if result and result.row_count and plan.dimension is not None:
                drivers, findings_d, headline, verified = self._analyze_drivers(
                    result, plan, baseline_p, current_p, art.sql if art else ""
                )
                findings.extend(findings_d)
                total = sum(d.delta for d in drivers) if drivers else None
            elif result and result.row_count:
                # No driver dimension (a dataset with only a time axis): the
                # movement is still measured, just not attributed.
                findings_d, headline, verified, total = self._analyze_totals(
                    result, plan, baseline_p, current_p, art.sql if art else ""
                )
                findings.extend(findings_d)
            else:
                warnings.append("no rows for the requested periods; cannot attribute the change")
                headline = f"Could not compute a {metric_label} movement (no data)."
        else:
            warnings.append("could not determine two comparison periods")
            headline = f"Could not compute a {metric_label} movement (insufficient periods)."

        # --- trend-based findings (anomaly + forecast) ----------------------
        if request.effort != "simple":
            trend_plan = self._trend_plan(plan)
            art, result = await self._run(trend_plan, request, schema, validator)
            if art:
                queries.append(art)
            if result and result.row_count >= 3:
                findings.extend(self._analyze_trend(result, plan, art.sql if art else ""))

        # --- correlation across dimension members (assoc. only) -------------
        if (request.effort == "standard" or request.effort == "deep") and plan.dimension:
            corr_plan = self._correlation_plan(plan)
            art, result = await self._run(corr_plan, request, schema, validator)
            if art:
                queries.append(art)
            if result and result.row_count:
                findings.extend(self._analyze_correlation(result, plan, art.sql if art else ""))

        return AnalysisResult(
            question=request.question,
            metric=plan.metric_name,
            headline=headline,
            verified=verified,
            drivers=drivers,
            findings=findings,
            queries=queries,
            warnings=warnings,
            total=total,
            period_column=plan.period_column,
            dimension=plan.dimension,
        )

    # --- interpretation -------------------------------------------------------
    def _interpret(self, request: AnalyticsRequest, schema: WarehouseSchema) -> _Plan:
        table = self._choose_table(request, schema)
        if table is not None and table.name != self._default_table:
            return self._interpret_dataset(request, table)
        q = request.question.lower()
        metric_name = request.metric or self._default_metric
        if not request.metric:
            for name in _METRICS:
                if name.replace("_", " ") in q or name in q:
                    metric_name = name
                    break
        expr, label = _METRICS.get(metric_name, _METRICS[self._default_metric])
        # Normalise alias to the canonical metric key.
        metric_key = "gross_margin" if metric_name in ("margin", "gross_margin") else metric_name

        dimension = request.dimension or self._default_dimension
        filter_value = request.filter_value
        if filter_value is None:
            for tok in _FILTER_TOKENS:
                if re.search(rf"\b{tok}\b", request.question, re.IGNORECASE):
                    filter_value = tok
                    break
        filters: tuple[tuple[str, str], ...] = (
            ((request.filter_column, filter_value),) if filter_value else ()
        )
        return _Plan(
            table=self._default_table,
            metric_name=metric_key,
            metric_expr=expr,
            metric_label=label,
            dimension=dimension,
            period_column=request.period_column,
            filters=filters,
        )

    def _choose_table(
        self, request: AnalyticsRequest, schema: WarehouseSchema
    ) -> TableSchema | None:
        """An uploaded dataset named in the question (or requested explicitly)
        wins over the default table; the longest matching name wins ties."""
        if request.table:
            return schema.table(request.table)
        q = request.question.lower()
        best: TableSchema | None = None
        for t in schema.tables:
            if t.name == self._default_table:
                continue
            for pattern in (t.name, t.name.replace("_", " ")):
                if re.search(rf"\b{re.escape(pattern)}\b", q):
                    if best is None or len(t.name) > len(best.name):
                        best = t
                    break
        return best

    def _interpret_dataset(self, request: AnalyticsRequest, table: TableSchema) -> _Plan:
        """Interpretation over an uploaded dataset (Glass Box): every role comes
        from the table's own schema + hints, never from the finance defaults."""
        q = request.question.lower()
        numeric = table.numeric_columns()
        names = table.column_names()

        def mentioned(col: str) -> bool:
            return any(re.search(rf"\b{re.escape(p)}\b", q) for p in (col, col.replace("_", " ")))

        metric = request.metric if request.metric in numeric else None
        if metric is None:
            metric = next((c for c in numeric if mentioned(c)), numeric[0])
        period = table.period_column
        if period is None:
            period = (
                request.period_column
                if request.period_column in names
                else next(
                    (c.name for c in table.columns if not c.is_numeric and c.name != "tenant_id"),
                    "",
                )
            )
        dimension: str | None = None
        if request.dimension in names and request.dimension != period:
            dimension = request.dimension
        elif table.dimensions:
            dimension = table.dimensions[0]

        filters: tuple[tuple[str, str], ...] = ()
        if request.filter_value and request.filter_column in names:
            filters = ((request.filter_column, request.filter_value),)
        else:
            best: tuple[str, str] | None = None
            for col, values in table.sample_values:
                if col in (period, dimension, "tenant_id"):
                    continue
                for v in values:
                    if re.search(rf"\b{re.escape(v.lower())}\b", q) and (
                        best is None or len(v) > len(best[1])
                    ):
                        best = (col, v)
            if best is not None:
                filters = (best,)
        return _Plan(
            table=table.name,
            metric_name=metric,
            metric_expr=metric,
            metric_label=metric.replace("_", " "),
            dimension=dimension,
            period_column=period,
            filters=filters,
        )

    async def _resolve_periods(
        self,
        request: AnalyticsRequest,
        plan: _Plan,
        schema: WarehouseSchema,
        validator: SqlSafetyValidator,
        queries: list[SqlArtifact],
        warnings: list[str],
    ) -> tuple[str | None, str | None]:
        if request.baseline_period and request.current_period:
            return request.baseline_period, request.current_period
        discover = QueryPlan(
            purpose="periods",
            table=plan.table,
            dimensions=(plan.period_column,),
            distinct=True,
            filters=plan.filters,
            order_by=((plan.period_column, "DESC"),),
            limit=24,
        )
        art, result = await self._run(discover, request, schema, validator)
        if art:
            queries.append(art)
        if not result or result.row_count < 2:
            return None, None
        periods = [str(r[0]) for r in result.rows]
        return periods[1], periods[0]  # baseline (older), current (latest)

    # --- query plans ----------------------------------------------------------
    def _driver_plan(self, plan: _Plan, periods: tuple[str, str]) -> QueryPlan:
        dims = (plan.dimension, plan.period_column) if plan.dimension else (plan.period_column,)
        return QueryPlan(
            purpose="drivers",
            table=plan.table,
            dimensions=dims,
            measures=((f"SUM({plan.metric_expr})", plan.metric_name),),
            filters=plan.filters,
            period_in=(plan.period_column, periods),
            order_by=tuple((d, "ASC") for d in dims),
        )

    def _trend_plan(self, plan: _Plan) -> QueryPlan:
        return QueryPlan(
            purpose="trend",
            table=plan.table,
            dimensions=(plan.period_column,),
            measures=((f"SUM({plan.metric_expr})", plan.metric_name),),
            filters=plan.filters,
            order_by=((plan.period_column, "ASC"),),
        )

    def _correlation_plan(self, plan: _Plan) -> QueryPlan:
        assert plan.dimension is not None
        return QueryPlan(
            purpose="correlation",
            table=plan.table,
            dimensions=(plan.period_column, plan.dimension),
            measures=((f"SUM({plan.metric_expr})", plan.metric_name),),
            filters=plan.filters,
            order_by=((plan.period_column, "ASC"),),
        )

    # --- generate -> validate -> execute, with repair + cache -----------------
    async def _run(
        self,
        plan: QueryPlan,
        request: AnalyticsRequest,
        schema: WarehouseSchema,
        validator: SqlSafetyValidator,
    ) -> tuple[SqlArtifact | None, QueryResult | None]:
        feedback: str | None = None
        last_sql: str | None = None
        attempts = 0
        for _ in range(self._max_repairs + 1):
            attempts += 1
            raw = await self._gen.generate(
                request, schema, plan, tenant_id=request.tenant_id, feedback=feedback
            )
            try:
                validated = validator.validate(raw, tenant_id=request.tenant_id)
            except SqlValidationError as exc:
                log.warning("analytics.sql_rejected", purpose=plan.purpose, reason=exc.reason)
                feedback = exc.reason
                if raw == last_sql:  # deterministic generator won't change — stop early
                    break
                last_sql = raw
                continue

            if validated.sql == last_sql:
                # Re-emitted identical SQL after an empty-result repair: stop.
                break
            last_sql = validated.sql

            result = await self._cache.get(request.tenant_id, validated.sql)
            if result is None:
                try:
                    result = await self._wh.execute(
                        request.tenant_id,
                        validated.sql,
                        row_cap=self._row_cap,
                        timeout_s=self._timeout,
                    )
                except WarehouseError as exc:
                    log.warning("analytics.execute_failed", purpose=plan.purpose, error=str(exc))
                    feedback = f"execution failed: {exc}"
                    continue
                await self._cache.put(request.tenant_id, validated.sql, result)

            if result.row_count == 0 and attempts <= self._max_repairs:
                feedback = "the query returned no rows; check the column/value names"
                continue
            return (
                SqlArtifact(
                    purpose=plan.purpose,
                    sql=validated.sql,
                    row_count=result.row_count,
                    truncated=result.truncated,
                    attempts=attempts,
                ),
                result,
            )
        return None, None

    # --- statistics + provenance ---------------------------------------------
    def _analyze_totals(
        self, result: QueryResult, plan: _Plan, baseline_p: str, current_p: str, sql: str
    ) -> tuple[list[Finding], str, bool, float | None]:
        pi = result.column_index(plan.period_column)
        mi = result.column_index(plan.metric_name)
        base = sum(float(r[mi]) for r in result.rows if str(r[pi]) == baseline_p)
        cur = sum(float(r[mi]) for r in result.rows if str(r[pi]) == current_p)
        total = cur - base
        scope = f"{plan.filters[0][1]} " if plan.filters else ""
        verb = "fell" if total < 0 else "rose"
        headline = (
            f"{scope}{plan.metric_label} {verb} by {abs(total):,.0f} "
            f"({base:,.0f} → {cur:,.0f}) from {baseline_p} to {current_p}"
        )
        finding = Finding(
            kind="headline",
            statement=headline,
            magnitude=total,
            evidence_sql=sql,
            detail={"total": total, "baseline": base, "current": cur},
        )
        return [finding], headline, True, total

    def _analyze_drivers(
        self,
        result: QueryResult,
        plan: _Plan,
        baseline_p: str,
        current_p: str,
        sql: str,
    ) -> tuple[list[Driver], list[Finding], str, bool]:
        assert plan.dimension is not None
        di = result.column_index(plan.dimension)
        pi = result.column_index(plan.period_column)
        mi = result.column_index(plan.metric_name)
        baseline: dict[str, float] = {}
        current: dict[str, float] = {}
        for row in result.rows:
            member, period, value = str(row[di]), str(row[pi]), float(row[mi])
            if period == baseline_p:
                baseline[member] = value
            elif period == current_p:
                current[member] = value
        if not baseline and not current:
            return [], [], f"No {plan.metric_label} data for the chosen periods.", False

        total, drivers = attribute_change(baseline, current)
        base_total, cur_total = sum(baseline.values()), sum(current.values())
        scope = f"{plan.filters[0][1]} " if plan.filters else ""
        verb = "fell" if total < 0 else "rose"
        headline = (
            f"{scope}{plan.metric_label} {verb} by {abs(total):,.0f} "
            f"from {baseline_p} ({base_total:,.0f}) to {current_p} ({cur_total:,.0f})."
        )
        findings: list[Finding] = []
        for d in drivers:
            if abs(d.share) < 0.01 and abs(d.delta) < 1:
                continue
            findings.append(
                Finding(
                    kind="driver",
                    statement=(
                        f"{plan.dimension} '{d.label}' contributed {d.delta:+,.0f} "
                        f"({d.share:.0%} of the {plan.metric_label} change)."
                    ),
                    magnitude=d.delta,
                    evidence_sql=sql,
                    detail={
                        "member": d.label,
                        "baseline": d.baseline,
                        "current": d.current,
                        "share": d.share,
                    },
                )
            )
        return drivers, findings, headline, True

    def _analyze_trend(self, result: QueryResult, plan: _Plan, sql: str) -> list[Finding]:
        pi = result.column_index(plan.period_column)
        mi = result.column_index(plan.metric_name)
        periods = [str(r[pi]) for r in result.rows]
        series = [float(r[mi]) for r in result.rows]
        findings: list[Finding] = []
        for a in detect_anomalies(series):
            findings.append(
                Finding(
                    kind="anomaly",
                    statement=(
                        f"{plan.metric_label} at {periods[a.index]} is anomalous "
                        f"({a.value:,.0f}, robust z={a.score:+.1f})."
                    ),
                    magnitude=a.value,
                    evidence_sql=sql,
                    detail={"period": periods[a.index], "z": a.score},
                )
            )
        fc = linear_forecast(series)
        if fc is not None:
            findings.append(
                Finding(
                    kind="forecast",
                    statement=(
                        f"Next-period {plan.metric_label} forecast {fc.next_value:,.0f} "
                        f"({fc.confidence:.0%} PI {fc.lower:,.0f}-{fc.upper:,.0f}, "
                        f"R²={fc.r_squared:.2f})."
                    ),
                    magnitude=fc.next_value,
                    evidence_sql=sql,
                    detail={
                        "slope": fc.slope,
                        "lower": fc.lower,
                        "upper": fc.upper,
                        "r_squared": fc.r_squared,
                    },
                )
            )
        return findings

    def _analyze_correlation(self, result: QueryResult, plan: _Plan, sql: str) -> list[Finding]:
        assert plan.dimension is not None
        pi = result.column_index(plan.period_column)
        di = result.column_index(plan.dimension)
        mi = result.column_index(plan.metric_name)
        # member -> {period -> value}, aligned on the sorted period axis.
        by_member: dict[str, dict[str, float]] = {}
        periods: list[str] = []
        for row in result.rows:
            period, member, value = str(row[pi]), str(row[di]), float(row[mi])
            if period not in periods:
                periods.append(period)
            by_member.setdefault(member, {})[period] = value
        periods.sort()
        series = {
            m: [vals.get(p, 0.0) for p in periods]
            for m, vals in by_member.items()
            if len(vals) >= 3
        }
        findings: list[Finding] = []
        for c in correlate(series, alpha=self._alpha):
            if not c.significant:
                continue
            findings.append(
                Finding(
                    kind="correlation",
                    statement=(
                        f"{plan.metric_label} of '{c.x}' and '{c.y}' move together "
                        f"(r={c.r:+.2f}, p={c.p_value:.3f}) — association, not causation."
                    ),
                    magnitude=c.r,
                    evidence_sql=sql,
                    association_only=True,
                    detail={"x": c.x, "y": c.y, "r": c.r, "p_value": c.p_value, "n": c.n},
                )
            )
        return findings


class _Plan:
    """Resolved interpretation of a request (internal)."""

    __slots__ = (
        "table",
        "metric_name",
        "metric_expr",
        "metric_label",
        "dimension",
        "period_column",
        "filters",
    )

    def __init__(
        self,
        *,
        table: str,
        metric_name: str,
        metric_expr: str,
        metric_label: str,
        dimension: str | None,
        period_column: str,
        filters: tuple[tuple[str, str], ...],
    ) -> None:
        self.table = table
        self.metric_name = metric_name
        self.metric_expr = metric_expr
        self.metric_label = metric_label
        self.dimension = dimension
        self.period_column = period_column
        self.filters = filters
