"""Versioned, immutable prompt management (Phase 11, FR-052, US-D2, TAD Ch 14).

Prompts are platform artifacts under the same governance as code:
  - **immutable versions** — registering always creates version N+1; content is
    sealed by its sha256 and can never be edited in place.
  - **eval-gated promotion** — a version cannot serve traffic (canary or active)
    until an eval-harness score at/above the threshold is recorded for it.
  - **canary** — an eval-passed CANDIDATE serves a deterministic fraction of
    traffic (hash-bucketed on a stable key, e.g. the tenant id) beside ACTIVE.
  - **one-click rollback** — demote ACTIVE and reinstate the most recently
    active RETIRED version; the demoted version is marked ROLLED_BACK and is
    never auto-re-picked.

The in-memory store is the offline-deterministic default; a Postgres store
(`prompt_version` table, immutability enforced by trigger) sits behind the same
port for production.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Protocol

from eadip.platform.models import PromptStage, PromptVersion


class PromptError(Exception):
    """Raised on a governance violation (missing eval, unknown prompt, ...)."""


class PromptStore(Protocol):
    async def add(self, version: PromptVersion) -> None: ...

    async def save(self, version: PromptVersion) -> None:
        """Persist lifecycle metadata changes (stage, eval, canary) — not content."""
        ...

    async def get(self, name: str, version: int) -> PromptVersion | None: ...

    async def versions(self, name: str) -> list[PromptVersion]: ...

    async def names(self) -> list[str]: ...


class InMemoryPromptStore:
    def __init__(self) -> None:
        self._by_name: dict[str, dict[int, PromptVersion]] = {}

    async def add(self, version: PromptVersion) -> None:
        self._by_name.setdefault(version.name, {})[version.version] = version

    async def save(self, version: PromptVersion) -> None:
        self._by_name.setdefault(version.name, {})[version.version] = version

    async def get(self, name: str, version: int) -> PromptVersion | None:
        return self._by_name.get(name, {}).get(version)

    async def versions(self, name: str) -> list[PromptVersion]:
        return [v for _, v in sorted(self._by_name.get(name, {}).items())]

    async def names(self) -> list[str]:
        return sorted(self._by_name)


def _bucket(key: str) -> float:
    """Deterministic [0,1) bucket for canary routing (stable per key)."""
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


class PromptRegistry:
    def __init__(
        self,
        store: PromptStore,
        *,
        eval_threshold: float = 0.8,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._threshold = eval_threshold
        self._now = now or (lambda: 0.0)

    # --- lifecycle -------------------------------------------------------------
    async def register(self, name: str, content: str, *, actor: str = "") -> PromptVersion:
        """Create the next immutable version of `name` (DRAFT until eval'd)."""
        existing = await self._store.versions(name)
        version = PromptVersion(
            name=name,
            version=(existing[-1].version + 1) if existing else 1,
            content=content,
            created_by=actor,
            created_at_s=self._now(),
        )
        await self._store.add(version)
        return version

    async def record_eval(
        self, name: str, version: int, *, score: float, source: str = "eadip-eval"
    ) -> PromptVersion:
        """Attach an eval-harness score to a version. This is the promotion gate's
        evidence — promote/canary refuse without a passing recorded score."""
        pv = await self._require(name, version)
        pv.eval_score = score
        pv.eval_source = source
        await self._store.save(pv)
        return pv

    async def start_canary(self, name: str, version: int, *, fraction: float) -> PromptVersion:
        """Serve `fraction` of traffic from an eval-passed version (CANDIDATE)."""
        if not 0.0 < fraction <= 0.5:
            raise PromptError("canary fraction must be in (0, 0.5]")
        pv = await self._require(name, version)
        self._require_eval_pass(pv)
        # Only one candidate at a time — a new canary supersedes the old one.
        for other in await self._store.versions(name):
            if other.stage is PromptStage.CANDIDATE and other.version != version:
                other.stage = PromptStage.DRAFT
                other.canary_fraction = 0.0
                await self._store.save(other)
        pv.stage = PromptStage.CANDIDATE
        pv.canary_fraction = fraction
        await self._store.save(pv)
        return pv

    async def promote(self, name: str, version: int) -> PromptVersion:
        """Make an eval-passed version ACTIVE; the old active is RETIRED (kept
        immutable as the rollback target)."""
        pv = await self._require(name, version)
        self._require_eval_pass(pv)
        active = await self.active(name)
        if active is not None and active.version != version:
            active.stage = PromptStage.RETIRED
            await self._store.save(active)
        pv.stage = PromptStage.ACTIVE
        pv.canary_fraction = 0.0
        pv.promoted_at_s = self._now()
        await self._store.save(pv)
        return pv

    async def rollback(self, name: str) -> PromptVersion:
        """One-click rollback: demote ACTIVE (ROLLED_BACK) and reinstate the most
        recently active RETIRED version."""
        active = await self.active(name)
        if active is None:
            raise PromptError(f"prompt '{name}' has no active version")
        retired = [
            v
            for v in await self._store.versions(name)
            if v.stage is PromptStage.RETIRED and v.promoted_at_s is not None
        ]
        if not retired:
            raise PromptError(f"prompt '{name}' has no prior version to roll back to")
        target = max(retired, key=lambda v: v.promoted_at_s or 0.0)
        active.stage = PromptStage.ROLLED_BACK
        active.canary_fraction = 0.0
        await self._store.save(active)
        target.stage = PromptStage.ACTIVE
        target.promoted_at_s = self._now()
        await self._store.save(target)
        return target

    # --- serving ---------------------------------------------------------------
    async def active(self, name: str) -> PromptVersion | None:
        for v in await self._store.versions(name):
            if v.stage is PromptStage.ACTIVE:
                return v
        return None

    async def resolve(self, name: str, *, key: str = "") -> PromptVersion | None:
        """The version the runtime should serve: the canary CANDIDATE for keys
        that fall inside its fraction, otherwise ACTIVE."""
        versions = await self._store.versions(name)
        candidate = next((v for v in versions if v.stage is PromptStage.CANDIDATE), None)
        if candidate is not None and key and _bucket(f"{name}:{key}") < candidate.canary_fraction:
            return candidate
        return next((v for v in versions if v.stage is PromptStage.ACTIVE), None)

    async def overview(self) -> list[PromptVersion]:
        out: list[PromptVersion] = []
        for name in await self._store.names():
            out.extend(await self._store.versions(name))
        return out

    async def seed_default(self, name: str, content: str) -> PromptVersion:
        """Bootstrap: install content as version 1 ACTIVE if `name` is empty.
        Seeds are the shipped defaults (governed like code via the repo); every
        change after bootstrap goes through the eval gate."""
        existing = await self._store.versions(name)
        if existing:
            active = await self.active(name)
            return active if active is not None else existing[-1]
        version = PromptVersion(
            name=name,
            version=1,
            content=content,
            stage=PromptStage.ACTIVE,
            created_by="seed",
            created_at_s=self._now(),
            promoted_at_s=self._now(),
        )
        await self._store.add(version)
        return version

    # --- helpers ---------------------------------------------------------------
    async def _require(self, name: str, version: int) -> PromptVersion:
        pv = await self._store.get(name, version)
        if pv is None:
            raise PromptError(f"prompt '{name}' v{version} not found")
        return pv

    def _require_eval_pass(self, pv: PromptVersion) -> None:
        if pv.eval_score is None:
            raise PromptError(
                f"prompt '{pv.name}' v{pv.version} has no recorded eval — run the "
                f"harness and record a score before serving traffic"
            )
        if pv.eval_score < self._threshold:
            raise PromptError(
                f"prompt '{pv.name}' v{pv.version} eval score {pv.eval_score:.2f} "
                f"is below the promotion threshold {self._threshold:.2f}"
            )
