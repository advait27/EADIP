"""Bring your own data (Glass Box): CSV parsing, registration, isolation, and
analytics that understands the uploaded table."""

from __future__ import annotations

import importlib.util
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from eadip.analytics.sql_validator import SqlSafetyValidator, SqlValidationError
from eadip.ingestion.datasets import (
    DatasetError,
    normalise_header,
    parse_csv,
    slugify_table,
)
from eadip.ports.warehouse import ColumnSchema, TableSchema, WarehouseError, WarehouseSchema

_DUCKDB = importlib.util.find_spec("duckdb") is not None

CSV = b"""Region,Channel,Quarter,Sales,Cost
EMEA,Online,2026-Q1,100,60
EMEA,Retail,2026-Q1,80,50
EMEA,Online,2026-Q2,70,60
EMEA,Retail,2026-Q2,90,50
AMER,Online,2026-Q1,120,70
AMER,Retail,2026-Q1,110,60
AMER,Online,2026-Q2,125,70
AMER,Retail,2026-Q2,112,60
"""


# --- headers ------------------------------------------------------------------------
def test_normalise_header_rules() -> None:
    taken: set[str] = set()
    assert normalise_header("Gross Margin %", 1, taken) == "gross_margin"
    assert normalise_header("order-date", 2, taken) == "order_date"
    assert normalise_header("123abc", 3, taken) == "abc"
    assert normalise_header("###", 4, taken) == "col_4"
    assert normalise_header("SUM", 5, taken) == "sum_col"
    assert normalise_header("order", 6, taken) == "order_col"
    assert normalise_header("gross margin", 7, taken) == "gross_margin_2"
    assert normalise_header("Gross-Margin", 8, taken) == "gross_margin_3"
    with pytest.raises(DatasetError):
        normalise_header("Tenant ID", 9, taken)


def test_slugify_table() -> None:
    assert slugify_table("Shop Sales (2026).csv") == "shop_sales_2026"
    assert slugify_table("select.csv") == "select_data"
    assert slugify_table("  2026 numbers.CSV ") == "numbers"
    with pytest.raises(DatasetError):
        slugify_table("###.csv")


# --- parsing ------------------------------------------------------------------------
def test_parse_csv_infers_types_period_dimensions_and_profile() -> None:
    d = parse_csv(CSV)
    assert [c.name for c in d.columns] == ["region", "channel", "quarter", "sales", "cost"]
    assert [c.type for c in d.columns] == ["text", "text", "text", "number", "number"]
    assert d.period_column == "quarter"
    assert d.row_count == 8 and d.rows[0] == ["EMEA", "Online", "2026-Q1", 100.0, 60.0]
    assert d.dimensions == ("channel", "region")  # ascending cardinality, then name
    assert d.profile["region"] == ("AMER", "EMEA")
    schema = d.table_schema("shop_sales")
    assert schema.columns[0] == ColumnSchema("tenant_id", "text")
    assert schema.period_column == "quarter" and schema.values_for("region") == ("AMER", "EMEA")
    assert schema.numeric_columns() == ("sales", "cost")


def test_parse_csv_numeric_looking_period_is_text_and_blanks_are_null() -> None:
    d = parse_csv(b"year,amount\n2024,10\n2025,\n2026,30\n")
    assert [c.type for c in d.columns] == ["text", "number"]
    assert d.period_column == "year"
    assert d.rows[1] == ["2025", None]


def test_parse_csv_without_period_hint_uses_first_text_column() -> None:
    d = parse_csv(b"shop,revenue\na,1\nb,2\n")
    assert d.period_column == "shop" and d.dimensions == ()


def test_parse_csv_mixed_column_is_text_and_thousands_separators_parse() -> None:
    d = parse_csv(b'period,x,y\n2026-Q1,"1,200",n/a\n2026-Q2,5,7\n')
    assert [c.type for c in d.columns] == ["text", "number", "text"]
    assert d.rows[0][1] == 1200.0


@pytest.mark.parametrize(
    "data, reason",
    [
        (b"", "empty"),
        (b"a,b\n", "no data rows"),
        (b" , \n1,2\n", "header row is empty"),
        (b"p,q\nx,y\n", "no numeric column"),
        (b"\xff\xfe", "not UTF-8"),
    ],
)
def test_parse_csv_rejections(data: bytes, reason: str) -> None:
    with pytest.raises(DatasetError, match=reason):
        parse_csv(data)


def test_parse_csv_limits() -> None:
    with pytest.raises(DatasetError, match="bytes"):
        parse_csv(CSV, max_bytes=10)
    with pytest.raises(DatasetError, match="columns"):
        parse_csv(CSV, max_columns=2)
    with pytest.raises(DatasetError, match="rows"):
        parse_csv(CSV, max_rows=3)


# --- validator: per-table column allowlist ------------------------------------------
def test_validator_checks_columns_per_table_when_one_table_is_referenced() -> None:
    tenant = uuid4()
    schema = WarehouseSchema(
        tables=(
            TableSchema(
                "finance_metrics",
                (ColumnSchema("tenant_id", "text"), ColumnSchema("revenue", "number")),
            ),
            TableSchema(
                "shop_sales", (ColumnSchema("tenant_id", "text"), ColumnSchema("sales", "number"))
            ),
        )
    )
    v = SqlSafetyValidator(schema)
    ok = v.validate(f"SELECT sales FROM shop_sales WHERE tenant_id = '{tenant}'", tenant_id=tenant)
    assert ok.tables == ("shop_sales",)
    with pytest.raises(SqlValidationError, match="unknown column"):
        v.validate(
            f"SELECT sales FROM finance_metrics WHERE tenant_id = '{tenant}'", tenant_id=tenant
        )


# --- warehouse ------------------------------------------------------------------------
@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
async def test_duckdb_registers_dataset_with_logical_names_and_tenant_isolation() -> None:
    from eadip.adapters.duckdb_warehouse import DuckDBWarehouse

    wh = DuckDBWarehouse()
    a, b = uuid4(), uuid4()
    entry = await wh.register_dataset(a, "shop_sales", parse_csv(CSV))
    assert entry.physical.startswith("ds_") and entry.row_count == 8
    assert "shop_sales" in (await wh.schema(a)).table_names()
    assert "shop_sales" not in (await wh.schema(b)).table_names()
    # Logical name in SQL, physical table underneath; tenant literal must match.
    sql = (
        f"SELECT quarter, SUM(sales) AS sales FROM shop_sales WHERE tenant_id = '{a}' "
        "GROUP BY quarter ORDER BY quarter"
    )
    r = await wh.execute(a, sql, row_cap=100, timeout_s=5)
    assert [tuple(x) for x in r.rows] == [("2026-Q1", 410.0), ("2026-Q2", 397.0)]
    # Another tenant cannot see the rows even by name (no registration -> no rewrite).
    with pytest.raises(WarehouseError):
        await wh.execute(b, sql.replace(str(a), str(b)), row_cap=100, timeout_s=5)
    # Re-upload replaces; drop removes.
    await wh.register_dataset(a, "shop_sales", parse_csv(b"quarter,sales\n2026-Q1,1\n"))
    assert (
        await wh.execute(
            a, f"SELECT quarter FROM shop_sales WHERE tenant_id = '{a}'", row_cap=10, timeout_s=5
        )
    ).row_count == 1
    assert await wh.drop_dataset(a, "shop_sales") is True
    assert await wh.drop_dataset(a, "shop_sales") is False
    assert "shop_sales" not in (await wh.schema(a)).table_names()


# --- gateway ----------------------------------------------------------------------------
def _upload(
    client: TestClient, data: bytes = CSV, filename: str = "shop_sales.csv", **headers: str
):
    return client.post(
        "/v1/datasets", files={"file": (filename, data, "text/csv")}, headers=headers
    )


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_upload_list_delete_and_rbac(client: TestClient) -> None:
    assert _upload(client, **{"X-Roles": "compliance"}).status_code == 403
    assert _upload(client, **{"X-Roles": "viewer"}).status_code == 403
    assert _upload(client, data=b"x,y\n", filename="bad.csv").status_code == 400
    assert _upload(client, filename="finance_metrics.csv").status_code == 409
    r = _upload(client)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["name"] == "shop_sales" and item["row_count"] == 8
    assert item["period_column"] == "quarter" and item["dimensions"] == ["channel", "region"]
    listed = client.get("/v1/datasets", headers={"X-Roles": "viewer"}).json()
    assert [d["name"] for d in listed] == ["shop_sales"]
    other = {"X-Tenant-ID": str(uuid4())}
    assert client.get("/v1/datasets", headers=other).json() == []
    assert client.delete("/v1/datasets/shop_sales", headers=other).status_code == 404
    assert client.delete("/v1/datasets/shop_sales").status_code == 204
    assert client.get("/v1/datasets").json() == []


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_analytics_and_a_full_run_over_an_uploaded_table(client: TestClient) -> None:
    assert _upload(client).status_code == 201
    # Direct analytics: table, metric, dimension, period and filter all come from the CSV.
    r = client.post("/v1/analytics", json={"question": "Why did sales fall in EMEA in shop_sales?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metric"] == "sales"
    assert "EMEA sales fell by 20" in body["headline"]
    labels = {d["label"]: d["delta"] for d in body["drivers"]}
    assert labels == {"Online": -30.0, "Retail": 10.0}
    assert all("shop_sales" in q["sql"] and "ds_" not in q["sql"] for q in body["queries"])
    # A full governed run, verified brief, and a browser-verifiable bundle.
    run_id = client.post(
        "/v1/runs", json={"question": "Why did sales fall in EMEA in shop_sales?"}
    ).json()["id"]
    with client.stream("GET", f"/v1/runs/{run_id}/events") as s:
        text = "".join(s.iter_text())
    assert "run.done" in text
    report = client.get(f"/v1/runs/{run_id}/report").json()
    assert "EMEA sales fell by 20" in report["brief"]["headline"]
    analytics_claims = [
        c for c in report["brief"]["drill_down"]["claims"] if c["source"] == "analytics"
    ]
    assert analytics_claims and all(c["status"] == "verified" for c in analytics_claims)
    bundle = client.get(f"/v1/runs/{run_id}/evidence-bundle").json()
    assert [t["name"] for t in bundle["tables"]] == ["shop_sales"]
    assert bundle["tables"][0]["row_count"] == 8
    client.delete("/v1/datasets/shop_sales")


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_dataset_without_a_dimension_still_measures_the_movement(client: TestClient) -> None:
    assert (
        _upload(
            client,
            data=b"month,visits\n2026-01,100\n2026-02,120\n2026-03,90\n",
            filename="traffic.csv",
        ).status_code
        == 201
    )
    r = client.post("/v1/analytics", json={"question": "Why did visits change in traffic?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["drivers"] == [] and "visits fell by 30" in body["headline"]
    kinds = [f["kind"] for f in body["findings"]]
    assert "headline" in kinds and "correlation" not in kinds
    client.delete("/v1/datasets/traffic")
