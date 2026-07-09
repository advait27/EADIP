"""Secrets access (SEC-05).

Secrets and connector credentials come from a managed vault — never from
prompts, logs, or source. This module defines the `SecretsProvider` port and two
adapters: an env-backed provider for local dev and a static one for tests. A
real HashiCorp/AWS/CSI-vault adapter implements the same port in production.
"""

from __future__ import annotations

import os
from typing import Protocol


class MissingSecretError(RuntimeError):
    pass


class SecretsProvider(Protocol):
    def get(self, key: str) -> str | None: ...

    def require(self, key: str) -> str: ...


class _RequireMixin:
    def get(self, key: str) -> str | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def require(self, key: str) -> str:
        value = self.get(key)
        if value is None:
            raise MissingSecretError(f"required secret not found: {key}")
        return value


class EnvSecretsProvider(_RequireMixin):
    """Reads secrets from environment variables (local dev only).

    Keys are upper-cased and prefixed, e.g. key "service-jwt-signing-key" ->
    env var "EADIP_SECRET_SERVICE_JWT_SIGNING_KEY".
    """

    def __init__(self, prefix: str = "EADIP_SECRET_") -> None:
        self._prefix = prefix

    def _env_name(self, key: str) -> str:
        return self._prefix + key.upper().replace("-", "_")

    def get(self, key: str) -> str | None:
        return os.environ.get(self._env_name(key))


class StaticSecretsProvider(_RequireMixin):
    """In-memory provider for tests and deterministic dev defaults."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = dict(secrets)

    def get(self, key: str) -> str | None:
        return self._secrets.get(key)
