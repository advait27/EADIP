"""Prompt management (Phase 11, FR-052, US-D2): immutable versions, eval-gated
promotion, deterministic canary routing, one-click rollback."""

from __future__ import annotations

import pytest

from eadip.platform.models import PromptStage
from eadip.platform.prompts import InMemoryPromptStore, PromptError, PromptRegistry


def _registry(threshold: float = 0.8) -> PromptRegistry:
    clock = {"t": 0.0}

    def now() -> float:
        clock["t"] += 1.0
        return clock["t"]

    return PromptRegistry(InMemoryPromptStore(), eval_threshold=threshold, now=now)


async def test_register_creates_incrementing_immutable_versions() -> None:
    reg = _registry()
    v1 = await reg.register("agent/planner", "plan carefully", actor="lena")
    v2 = await reg.register("agent/planner", "plan very carefully", actor="lena")
    assert (v1.version, v2.version) == (1, 2)
    assert v1.stage is PromptStage.DRAFT
    assert v1.content_sha and v1.content_sha != v2.content_sha  # content sealed by sha


async def test_promotion_refused_without_eval_then_allowed_after_pass() -> None:
    reg = _registry()
    await reg.register("p", "v1 content")
    with pytest.raises(PromptError, match="no recorded eval"):
        await reg.promote("p", 1)
    await reg.record_eval("p", 1, score=0.75)  # below the 0.8 gate
    with pytest.raises(PromptError, match="below the promotion threshold"):
        await reg.promote("p", 1)
    await reg.record_eval("p", 1, score=0.95)
    promoted = await reg.promote("p", 1)
    assert promoted.stage is PromptStage.ACTIVE


async def test_promote_retires_previous_active() -> None:
    reg = _registry()
    await reg.register("p", "one")
    await reg.register("p", "two")
    await reg.record_eval("p", 1, score=1.0)
    await reg.record_eval("p", 2, score=1.0)
    await reg.promote("p", 1)
    await reg.promote("p", 2)
    versions = {v.version: v.stage for v in await reg.overview()}
    assert versions == {1: PromptStage.RETIRED, 2: PromptStage.ACTIVE}


async def test_one_click_rollback_restores_previous_active() -> None:
    reg = _registry()
    await reg.register("p", "one")
    await reg.register("p", "two")
    await reg.record_eval("p", 1, score=1.0)
    await reg.record_eval("p", 2, score=1.0)
    await reg.promote("p", 1)
    await reg.promote("p", 2)
    restored = await reg.rollback("p")
    assert restored.version == 1 and restored.stage is PromptStage.ACTIVE
    demoted = await reg._store.get("p", 2)  # type: ignore[attr-defined]
    assert demoted is not None and demoted.stage is PromptStage.ROLLED_BACK
    resolved = await reg.resolve("p")
    assert resolved is not None and resolved.content == "one"


async def test_rollback_without_prior_version_is_refused() -> None:
    reg = _registry()
    await reg.register("p", "only")
    await reg.record_eval("p", 1, score=1.0)
    await reg.promote("p", 1)
    with pytest.raises(PromptError, match="no prior version"):
        await reg.rollback("p")


async def test_canary_requires_eval_and_routes_a_deterministic_fraction() -> None:
    reg = _registry()
    await reg.register("p", "active content")
    await reg.record_eval("p", 1, score=1.0)
    await reg.promote("p", 1)
    v2 = await reg.register("p", "candidate content")
    with pytest.raises(PromptError, match="no recorded eval"):
        await reg.start_canary("p", 2, fraction=0.3)
    await reg.record_eval("p", 2, score=0.9)
    await reg.start_canary("p", 2, fraction=0.3)

    served = {key: (await reg.resolve("p", key=key)) for key in (f"tenant-{i}" for i in range(40))}
    candidate_share = sum(1 for v in served.values() if v and v.version == v2.version) / 40
    assert 0.05 < candidate_share < 0.6  # some keys ride the canary, not all
    # Deterministic: the same key always gets the same version.
    a = await reg.resolve("p", key="tenant-7")
    b = await reg.resolve("p", key="tenant-7")
    assert a is not None and b is not None and a.version == b.version
    # No key -> the stable ACTIVE version.
    default = await reg.resolve("p")
    assert default is not None and default.version == 1


async def test_seed_default_is_idempotent_and_never_overwrites() -> None:
    reg = _registry()
    seeded = await reg.seed_default("agent/planner", "shipped instruction")
    assert seeded.stage is PromptStage.ACTIVE and seeded.version == 1
    again = await reg.seed_default("agent/planner", "different text")
    assert again.version == 1 and again.content == "shipped instruction"
