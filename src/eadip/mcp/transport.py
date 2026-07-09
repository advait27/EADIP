"""Tool transports (AP-10): the wire between the registry and an MCP server.

A `ToolTransport` does three things — `discover()` (list tools + their JSON
schemas), `invoke()` (call one tool), and `ping()` (health probe). The default
`InProcessTransport` hosts tools as plain Python callables, so the whole Phase 8
stack is offline-deterministic and unit-testable; real stdio/HTTP MCP transports
slot in behind the same Protocol (imported lazily, keeping the MCP SDK optional).

Tool callables never see raw secrets — the registry resolves `secret_ref` via
the vault (SEC-05) and this layer is where a real transport would inject the
resolved credential into the connection, never logging it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from eadip.mcp.models import ToolSpec

# A hosted tool is an async callable: validated args in, JSON-able data out.
ToolFn = Callable[[dict[str, Any]], Awaitable[Any]]


class TransportError(Exception):
    """The transport could not reach / complete the call (distinct from a tool
    returning an error payload). Drives the circuit breaker."""


class ToolTransport(Protocol):
    async def discover(self) -> list[ToolSpec]: ...

    async def invoke(self, tool: str, arguments: dict[str, Any]) -> Any: ...

    async def ping(self) -> None: ...


class InProcessTransport:
    """Deterministic in-memory MCP server: tools are async Python callables with
    pre-declared specs. This is the reference transport the tests/demo use, and
    the mechanism by which built-in "native" tools are exposed through the same
    governed path as external MCP servers."""

    def __init__(
        self,
        specs: list[ToolSpec],
        handlers: dict[str, ToolFn],
        *,
        healthy: bool = True,
    ) -> None:
        missing = {s.name for s in specs} - set(handlers)
        if missing:
            raise ValueError(f"handlers missing for tools: {sorted(missing)}")
        self._specs = specs
        self._handlers = handlers
        self._healthy = healthy

    def set_healthy(self, healthy: bool) -> None:
        """Test/demo hook to simulate a server going down (drives the breaker)."""
        self._healthy = healthy

    async def discover(self) -> list[ToolSpec]:
        if not self._healthy:
            raise TransportError("server unreachable")
        return list(self._specs)

    async def invoke(self, tool: str, arguments: dict[str, Any]) -> Any:
        if not self._healthy:
            raise TransportError("server unreachable")
        handler = self._handlers.get(tool)
        if handler is None:
            raise TransportError(f"unknown tool '{tool}'")
        return await handler(arguments)

    async def ping(self) -> None:
        if not self._healthy:
            raise TransportError("ping failed")


def build_transport(
    transport: str, endpoint: str, *, credential: str | None = None
) -> ToolTransport:
    """Construct a non-inprocess transport lazily (keeps the MCP SDK optional).

    `credential` is the resolved secret value (from the vault) — passed to the
    connection here and never returned or logged. Only the in-process transport
    is exercised offline; http/stdio require the optional `mcp` extra.
    """
    if transport in ("http", "stdio"):  # pragma: no cover - requires the mcp extra
        raise NotImplementedError(
            f"the '{transport}' MCP transport requires the 'mcp' extra; "
            "register an in-process server for offline/dev use"
        )
    raise ValueError(f"unknown transport '{transport}'")
