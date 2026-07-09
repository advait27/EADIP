from __future__ import annotations

import pytest

from eadip.config.settings import Settings


def test_settings_defaults() -> None:
    s = Settings()
    assert s.environment == "dev"
    assert s.max_iterations == 6
    assert s.max_run_cost_usd == 2.50
    assert s.run_deadline_s == 480


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EADIP_ENVIRONMENT", "staging")
    monkeypatch.setenv("EADIP_MAX_ITERATIONS", "10")
    s = Settings()
    assert s.environment == "staging"
    assert s.max_iterations == 10
