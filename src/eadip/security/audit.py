"""Append-only, hash-chained audit trail (FR-051, SEC-08).

Records authentication, authorization decisions, queries, tool calls, and
approvals. This module defines the event, the port, and the per-tenant hash
chain; storage adapters enforce the rest.

What is actually guaranteed, and what is not:

- Append-only: in Postgres a BEFORE UPDATE/DELETE trigger (migration 0002)
  rejects mutation for every role. It does NOT stop a superuser or the table
  owner from disabling the trigger (or TRUNCATE, which it does not cover) and
  rewriting rows.
- Tamper-evident, partially: from migration 0009 on, each event carries
  `prev_hash` and `hash = sha256(canonical(prev_hash, event))`, chained per
  tenant. `verify_chain` detects an edited, deleted, inserted, or reordered
  event in the chained range. It does NOT detect an attacker who rewrites the
  chain consistently (recomputes every hash after the edit), nor truncation of
  the newest events. Detecting those requires anchoring the latest `hash`
  outside the database (e.g. a periodic signed export) and comparing against
  it. Rows written before 0009 have no hash and are not covered at all.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Context, Decimal
from typing import Any, Protocol
from uuid import UUID

from eadip.security.redaction import redact

GENESIS_HASH = "0" * 64


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AuditEvent:
    tenant_id: UUID
    actor: str  # user id, or "system:<agent>"
    action: str  # e.g. "authz.decision", "run.create", "sql.execute"
    detail: dict[str, Any] = field(default_factory=dict)
    run_id: UUID | None = None
    at: datetime = field(default_factory=_utcnow)


def make_event(
    *,
    tenant_id: UUID,
    actor: str,
    action: str,
    detail: dict[str, Any] | None = None,
    run_id: UUID | None = None,
) -> AuditEvent:
    """Build an audit event, redacting any sensitive fields in `detail`."""
    return AuditEvent(
        tenant_id=tenant_id,
        actor=actor,
        action=action,
        detail=redact(detail or {}),
        run_id=run_id,
    )


class AuditLog(Protocol):
    async def record(self, event: AuditEvent) -> None: ...


# Hash chain -----------------------------------------------------------------


def _canonical_number(value: int | float | Decimal) -> str:
    """Numbers are hashed by value, not spelling: Postgres JSONB (numeric)
    re-spells them — 1e20 comes back as 100000000000000000000 (an int on
    reload), -0.0 as 0 — so both sides are reduced to a normalized Decimal.
    Consequence: 1, 1.0 and 1e0 hash identically (same value)."""
    if isinstance(value, float):
        if not math.isfinite(value):
            # Not representable in JSONB (the insert fails there); hash as a tag.
            return json.dumps(repr(value))
        value = Decimal(repr(value))
    number = Decimal(value)
    if number == 0:
        return "0"
    # Precision = digit count, so normalizing never rounds (large ints stay exact).
    return str(number.normalize(Context(prec=len(number.as_tuple().digits))))


def _json_key(key: Any) -> str:
    # Same key coercion as json.dumps (what the JSONB column actually stores).
    if key is None:
        return "null"
    if isinstance(key, bool):
        return "true" if key else "false"
    return str(key)


def _canonical_json(value: Any) -> str:
    """Compact, sorted-key JSON whose output is stable across a JSONB round
    trip (key order, whitespace and number spelling are normalized)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float | Decimal):
        return _canonical_number(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, dict):
        items = sorted((_json_key(k), v) for k, v in value.items())
        return "{" + ",".join(f"{json.dumps(k)}:{_canonical_json(v)}" for k, v in items) + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(_canonical_json(v) for v in value) + "]"
    return json.dumps(str(value))  # UUID, datetime, ...: same as json.dumps(default=str)


def _canonical_at(at: datetime) -> str:
    # Naive timestamps are taken as UTC; microsecond precision matches timestamptz.
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).isoformat(timespec="microseconds")


def event_hash(prev_hash: str, event: AuditEvent) -> str:
    """sha256 hex over the canonical JSON of the event linked to `prev_hash`."""
    payload = {
        "prev": prev_hash,
        "tenant_id": str(event.tenant_id),
        "run_id": str(event.run_id) if event.run_id is not None else None,
        "actor": event.actor,
        "action": event.action,
        "detail": event.detail,
        "at": _canonical_at(event.at),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainedAuditRecord:
    event: AuditEvent
    prev_hash: str
    hash: str


def chain(prev_hash: str, event: AuditEvent) -> ChainedAuditRecord:
    return ChainedAuditRecord(event=event, prev_hash=prev_hash, hash=event_hash(prev_hash, event))


@dataclass(frozen=True)
class ChainReport:
    """Result of verifying one tenant's chain.

    `anchored` is False when the first checked record does not start at the
    genesis hash (older events were archived out, or the head was removed); its
    `anchor_prev_hash` then cannot be verified from the database alone. `ok`
    covers only the links between the records that were checked, and says
    nothing about events newer than `head_hash` — compare `head_hash` against an
    externally anchored value to detect tail truncation or a full rewrite.
    """

    ok: bool
    checked: int
    anchored: bool
    anchor_prev_hash: str | None = None
    head_hash: str | None = None
    break_index: int | None = None
    reason: str | None = None


def verify_chain(records: Sequence[ChainedAuditRecord]) -> ChainReport:
    """Verify records of ONE tenant, in append order."""
    if not records:
        return ChainReport(ok=True, checked=0, anchored=True)
    anchor = records[0].prev_hash
    anchored = anchor == GENESIS_HASH
    tenant = records[0].event.tenant_id

    def broken(index: int, reason: str) -> ChainReport:
        return ChainReport(
            ok=False,
            checked=index,
            anchored=anchored,
            anchor_prev_hash=anchor,
            head_hash=records[index - 1].hash if index else None,
            break_index=index,
            reason=reason,
        )

    for i, record in enumerate(records):
        if record.event.tenant_id != tenant:
            return broken(i, "record belongs to a different tenant")
        if i and record.prev_hash != records[i - 1].hash:
            return broken(i, "prev_hash does not match the preceding record (missing/reordered)")
        if record.hash != event_hash(record.prev_hash, record.event):
            return broken(i, "hash does not match record contents (modified)")
    return ChainReport(
        ok=True,
        checked=len(records),
        anchored=anchored,
        anchor_prev_hash=anchor,
        head_hash=records[-1].hash,
    )
