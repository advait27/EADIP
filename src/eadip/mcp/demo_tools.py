"""A deterministic in-process demo MCP server (Phase 8 demo + tests).

Ships two read-only tools so the orchestrator has a governed external capability
to call offline — the "register a sample MCP server, orchestrator calls it within
permissions" demo — with no external process and no network. Real connectors
(Jira, ServiceNow, a data API, …) register the same way over http/stdio; these
just make the governed path exercisable deterministically.

`fx_rate` is read-only (auto-authorized for analysts). `open_ticket` is a
write-effect tool: analysts are denied it by RBAC (it needs the `approver` grant),
which is exactly the deny-by-default boundary Phase 9 turns into an approval gate.
"""

from __future__ import annotations

from typing import Any

from eadip.mcp.models import ToolPermission, ToolServerConfig, ToolSpec
from eadip.mcp.transport import InProcessTransport
from eadip.security.rbac import Effect

_RATES: dict[str, float] = {"EUR": 1.08, "GBP": 1.27, "JPY": 0.0064, "USD": 1.0}

FX_TOOL = ToolSpec(
    name="fx_rate",
    description="Look up the USD exchange rate for a currency code (finance FX reference data).",
    input_schema={
        "type": "object",
        "properties": {"currency": {"type": "string", "enum": sorted(_RATES)}},
        "required": ["currency"],
        "additionalProperties": False,
    },
    permission=ToolPermission(effect=Effect.READ, domains=("metrics",)),
    idempotent=True,
)

TICKET_TOOL = ToolSpec(
    name="open_ticket",
    description="Open a remediation ticket in the enterprise tracker (write action).",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 3, "maxLength": 200},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
    permission=ToolPermission(effect=Effect.WRITE, domains=("tickets",)),
    idempotent=False,
)


async def _fx_rate(args: dict[str, Any]) -> Any:
    currency = str(args["currency"]).upper()
    return {"currency": currency, "usd_rate": _RATES[currency]}


async def _open_ticket(args: dict[str, Any]) -> Any:
    return {
        "ticket_id": "TICK-0001",
        "title": args["title"],
        "priority": args.get("priority", "low"),
    }


DEMO_CONFIG = ToolServerConfig(
    name="enterprise-tools",
    description="Demo enterprise MCP server: FX reference data + ticketing.",
    transport="inprocess",
)


def build_demo_transport(*, healthy: bool = True) -> InProcessTransport:
    return InProcessTransport(
        specs=[FX_TOOL, TICKET_TOOL],
        handlers={"fx_rate": _fx_rate, "open_ticket": _open_ticket},
        healthy=healthy,
    )
