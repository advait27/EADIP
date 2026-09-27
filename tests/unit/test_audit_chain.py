"""Per-tenant audit hash chain (SEC-08): digest determinism and tamper detection."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from eadip.adapters.memory_audit_log import InMemoryAuditLog
from eadip.security.audit import (
    GENESIS_HASH,
    AuditEvent,
    ChainedAuditRecord,
    chain,
    event_hash,
    make_event,
    verify_chain,
)

AT = datetime(2026, 9, 27, 12, 0, 0, 123456, tzinfo=UTC)


def _event(tenant=None, action="run.create", detail=None, at=AT) -> AuditEvent:
    return AuditEvent(
        tenant_id=tenant or uuid4(),
        actor="user-1",
        action=action,
        detail=detail if detail is not None else {"k": "v"},
        at=at,
    )


def _build(events: list[AuditEvent]) -> list[ChainedAuditRecord]:
    records: list[ChainedAuditRecord] = []
    prev = GENESIS_HASH
    for event in events:
        records.append(chain(prev, event))
        prev = records[-1].hash
    return records


def test_event_hash_is_deterministic_and_order_insensitive() -> None:
    tenant = uuid4()
    a = _event(tenant, detail={"b": 1, "a": {"y": [1, 2], "x": None}})
    b = _event(tenant, detail={"a": {"x": None, "y": [1, 2]}, "b": 1})
    assert event_hash(GENESIS_HASH, a) == event_hash(GENESIS_HASH, a)
    assert event_hash(GENESIS_HASH, a) == event_hash(GENESIS_HASH, b)
    assert len(event_hash(GENESIS_HASH, a)) == 64
    # Every field and the link participate.
    assert event_hash("1" * 64, a) != event_hash(GENESIS_HASH, a)
    assert event_hash(GENESIS_HASH, replace(a, actor="other")) != event_hash(GENESIS_HASH, a)
    assert event_hash(GENESIS_HASH, replace(a, run_id=uuid4())) != event_hash(GENESIS_HASH, a)


def test_event_hash_survives_jsonb_style_normalization() -> None:
    tenant = uuid4()
    # What Python writes vs what a JSONB round trip reads back (parse_float=Decimal).
    written = _event(tenant, detail={"big": 1e20, "one": 1.0, "neg0": -0.0, "tiny": 1.5e-7})
    read_back = _event(
        tenant,
        detail={
            "big": 100000000000000000000,
            "one": Decimal("1.0"),
            "neg0": Decimal("0.0"),
            "tiny": Decimal("0.00000015"),
        },
    )
    assert event_hash(GENESIS_HASH, written) == event_hash(GENESIS_HASH, read_back)


def test_event_hash_normalizes_timezone() -> None:
    tenant = uuid4()
    utc = _event(tenant)
    plus_two = replace(utc, at=AT.astimezone(timezone(timedelta(hours=2))))
    assert event_hash(GENESIS_HASH, utc) == event_hash(GENESIS_HASH, plus_two)


def test_verify_chain_ok() -> None:
    tenant = uuid4()
    records = _build([_event(tenant, action=f"a{i}") for i in range(5)])
    report = verify_chain(records)
    assert report.ok
    assert report.checked == 5
    assert report.anchored
    assert report.head_hash == records[-1].hash


def test_verify_chain_empty_is_ok() -> None:
    report = verify_chain([])
    assert report.ok and report.checked == 0


def test_verify_chain_detects_edited_detail() -> None:
    tenant = uuid4()
    records = _build([_event(tenant, action=f"a{i}") for i in range(4)])
    tampered = replace(records[2].event, detail={"k": "tampered"})
    records[2] = replace(records[2], event=tampered)
    report = verify_chain(records)
    assert not report.ok
    assert report.break_index == 2
    assert "modified" in (report.reason or "")


def test_verify_chain_detects_deleted_middle_event() -> None:
    tenant = uuid4()
    records = _build([_event(tenant, action=f"a{i}") for i in range(4)])
    del records[1]
    report = verify_chain(records)
    assert not report.ok
    assert report.break_index == 1
    assert "prev_hash" in (report.reason or "")


def test_verify_chain_detects_reordered_events() -> None:
    tenant = uuid4()
    records = _build([_event(tenant, action=f"a{i}") for i in range(4)])
    records[1], records[2] = records[2], records[1]
    report = verify_chain(records)
    assert not report.ok
    assert report.break_index == 1


def test_verify_chain_detects_foreign_tenant_record() -> None:
    tenant = uuid4()
    records = _build([_event(tenant, action=f"a{i}") for i in range(2)])
    records.append(chain(records[-1].hash, _event(uuid4())))
    report = verify_chain(records)
    assert not report.ok
    assert report.break_index == 2


def test_verify_chain_reports_unanchored_start() -> None:
    # Oldest rows archived out: the remainder still verifies, but its first
    # prev_hash is not genesis and cannot be checked from the records alone.
    tenant = uuid4()
    records = _build([_event(tenant, action=f"a{i}") for i in range(4)])
    report = verify_chain(records[2:])
    assert report.ok
    assert not report.anchored
    assert report.anchor_prev_hash == records[1].hash


def test_verify_chain_cannot_detect_consistent_rewrite() -> None:
    # Documented limitation: recomputing the whole chain passes verification;
    # only a head hash anchored outside the store reveals it.
    tenant = uuid4()
    original = _build([_event(tenant, action=f"a{i}") for i in range(3)])
    rewritten = _build(
        [original[0].event, replace(original[1].event, detail={"k": "x"}), original[2].event]
    )
    assert verify_chain(rewritten).ok
    assert rewritten[-1].hash != original[-1].hash


async def test_inmemory_log_keeps_per_tenant_chains() -> None:
    log = InMemoryAuditLog()
    ta, tb = uuid4(), uuid4()
    await log.record(make_event(tenant_id=ta, actor="u", action="a1"))
    await log.record(make_event(tenant_id=tb, actor="u", action="b1"))
    await log.record(make_event(tenant_id=ta, actor="u", action="a2", detail={"n": 1}))

    a_records = log.records(ta)
    b_records = log.records(tb)
    assert [r.event.action for r in a_records] == ["a1", "a2"]
    assert [r.event.action for r in b_records] == ["b1"]
    # Each tenant's chain starts at genesis and links only its own events.
    assert a_records[0].prev_hash == GENESIS_HASH
    assert b_records[0].prev_hash == GENESIS_HASH
    assert a_records[1].prev_hash == a_records[0].hash
    assert log.verify(ta).ok and log.verify(ta).checked == 2
    assert log.verify(tb).ok and log.verify(tb).checked == 1
    assert log.verify(uuid4()).checked == 0
    # The flat event list is unchanged (all tenants, insertion order).
    assert [e.action for e in log.events] == ["a1", "b1", "a2"]
    # Returned records are a copy.
    a_records.clear()
    assert len(log.records(ta)) == 2
