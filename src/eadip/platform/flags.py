"""Runtime feature flags (Phase 11, FR-057): decouple deploy from release.

Seeded from `settings.feature_flags` at startup; admins flip flags at runtime
through the portal without a redeploy. Unknown flags read as False
(deny-by-default, consistent with the platform's governance posture).
"""

from __future__ import annotations


class FeatureFlagService:
    def __init__(self, seed: dict[str, bool] | None = None) -> None:
        self._flags: dict[str, bool] = dict(seed or {})

    def is_enabled(self, name: str) -> bool:
        return self._flags.get(name, False)

    def set(self, name: str, enabled: bool) -> None:
        self._flags[name] = enabled

    def all(self) -> dict[str, bool]:
        return dict(sorted(self._flags.items()))
