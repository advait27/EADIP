"""Shared LLM helper for the agents: complete + parse a JSON object, defensively.

Returns ``None`` on any failure (no model, transport error, unparseable output)
so callers can fall back to their deterministic heuristic — the orchestrator
never hard-depends on a model being reachable. The fallback is never silent:
``complete_json_traced`` also returns *why* it gave up, and callers stamp
``produced_by``/``fallback_reason`` on their output and log ``agent.fallback``.
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

# Provenance values stamped on agent outputs (Goal/Plan/Reflection/Recommendation)
# so a trace shows whether the model actually produced them.
PRODUCED_BY_LLM = "llm"
PRODUCED_BY_HEURISTIC = "heuristic"
PRODUCED_BY_FALLBACK = "heuristic_fallback"


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
    on_fallback: Callable[[str], None] | None = None,
) -> dict[str, Any] | None: ...


@overload
async def complete_json[T](
    client: object,
    prompt: str,
    *,
    model: str | None = None,
    validate: Callable[[dict[str, Any]], T],
    repair_attempts: int = 1,
    on_fallback: Callable[[str], None] | None = None,
) -> T | None: ...


async def complete_json[T](
    client: object,
    prompt: str,
    *,
    model: str | None = None,
    validate: Callable[[dict[str, Any]], T] | None = None,
    repair_attempts: int = 1,
    on_fallback: Callable[[str], None] | None = None,
) -> dict[str, Any] | T | None:
    """Ask for a JSON object; optionally validate it into ``T``.

    When ``validate`` raises (bad shape), the error is fed back to the model
    and the call is retried up to ``repair_attempts`` times — the same
    generate→validate→repair loop the SQL generator uses. Any transport
    failure, unparseable output, or exhausted repair returns ``None`` so the
    caller falls back to its deterministic heuristic; ``on_fallback`` (when
    given) is called once with the reason just before that ``None`` is returned.
    """

    def give_up(reason: str) -> None:
        if on_fallback is not None:
            on_fallback(reason)

    instruction = prompt + "\n\nRespond with a single JSON object and nothing else."
    feedback = ""
    last_error = ""
    for attempt in range(repair_attempts + 1):
        try:
            resp = await client.complete(instruction + feedback, model=model)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — model unreachable -> caller falls back
            log.warning("agent.llm_failed", error=str(exc))
            give_up(f"llm_error: {str(exc)[:200] or type(exc).__name__}")
            return None
        data = _extract_json(getattr(resp, "text", ""))
        if data is None:
            feedback = "\n\nYour previous reply was not a single JSON object. Reply with JSON only."
            if attempt >= repair_attempts:
                give_up("unparseable_json: reply was not a single JSON object")
                return None
            continue
        if validate is None:
            return data
        try:
            return validate(data)
        except Exception as exc:  # noqa: BLE001 — shape error -> repair or give up
            log.warning("agent.llm_invalid_shape", error=str(exc)[:200], attempt=attempt + 1)
            last_error = str(exc)[:200]
            feedback = f"\n\nYour previous reply was rejected: {str(exc)[:300]}\nFix it."
    give_up(f"invalid_shape: {last_error}")
    return None


async def complete_json_traced[T](
    client: object,
    prompt: str,
    *,
    model: str | None = None,
    validate: Callable[[dict[str, Any]], T],
    repair_attempts: int = 1,
) -> tuple[T | None, str | None]:
    """``complete_json`` that also says why it fell back: ``(value, None)`` on
    success, ``(None, reason)`` on any failure."""
    reasons: list[str] = []
    value = await complete_json(
        client,
        prompt,
        model=model,
        validate=validate,
        repair_attempts=repair_attempts,
        on_fallback=reasons.append,
    )
    if value is not None:
        return value, None
    return None, reasons[-1] if reasons else "no model output"


def log_fallback(agent: str, reason: str) -> None:
    """The structured record that an LLM agent's output came from its heuristic."""
    log.warning("agent.fallback", agent=agent, reason=reason)
