"""Shared LLM helper for the agents: complete + parse a JSON object, defensively.

Returns ``None`` on any failure (no model, transport error, unparseable output)
so callers can fall back to their deterministic heuristic — the orchestrator
never hard-depends on a model being reachable.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, overload

from eadip.observability.logging import get_logger

log = get_logger(__name__)

# Fetches the governed instruction text for an agent (Phase 11 prompt
# management): the active registry version, or None to use the shipped default.
InstructionProvider = Callable[[], Awaitable[str | None]]


async def resolve_instruction(provider: InstructionProvider | None, default: str) -> str:
    """The active governed prompt when a provider is wired, else the shipped
    default. Resolved per call so a promote/rollback applies immediately."""
    if provider is None:
        return default
    try:
        resolved = await provider()
    except Exception as exc:  # noqa: BLE001 — registry unavailable -> shipped default
        log.warning("agent.prompt_resolve_failed", error=str(exc))
        return default
    return resolved or default


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("```"))
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


@overload
async def complete_json(
    client: object,
    prompt: str,
    *,
    model: str | None = None,
    validate: None = None,
    repair_attempts: int = 1,
) -> dict[str, Any] | None: ...


@overload
async def complete_json[T](
    client: object,
    prompt: str,
    *,
    model: str | None = None,
    validate: Callable[[dict[str, Any]], T],
    repair_attempts: int = 1,
) -> T | None: ...


async def complete_json[T](
    client: object,
    prompt: str,
    *,
    model: str | None = None,
    validate: Callable[[dict[str, Any]], T] | None = None,
    repair_attempts: int = 1,
) -> dict[str, Any] | T | None:
    """Ask for a JSON object; optionally validate it into ``T``.

    When ``validate`` raises (bad shape), the error is fed back to the model
    and the call is retried up to ``repair_attempts`` times — the same
    generate→validate→repair loop the SQL generator uses. Any transport
    failure, unparseable output, or exhausted repair returns ``None`` so the
    caller falls back to its deterministic heuristic.
    """
    instruction = prompt + "\n\nRespond with a single JSON object and nothing else."
    feedback = ""
    for attempt in range(repair_attempts + 1):
        try:
            resp = await client.complete(instruction + feedback, model=model)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — model unreachable -> caller falls back
            log.warning("agent.llm_failed", error=str(exc))
            return None
        data = _extract_json(getattr(resp, "text", ""))
        if data is None:
            feedback = "\n\nYour previous reply was not a single JSON object. Reply with JSON only."
            if attempt >= repair_attempts:
                return None
            continue
        if validate is None:
            return data
        try:
            return validate(data)
        except Exception as exc:  # noqa: BLE001 — shape error -> repair or give up
            log.warning("agent.llm_invalid_shape", error=str(exc)[:200], attempt=attempt + 1)
            feedback = f"\n\nYour previous reply was rejected: {str(exc)[:300]}\nFix it."
    return None
