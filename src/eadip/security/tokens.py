"""Short-lived service JWTs for internal/downstream calls (SEC-02).

The gateway mints a short-lived token carrying the caller's identity so
downstream services act on-behalf-of the user. The signing key is supplied by
the vault, never hard-coded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from eadip.security.identity import Identity


class ServiceTokenIssuer:
    def __init__(self, signing_key: str, *, ttl_s: int = 300, issuer: str = "eadip") -> None:
        self._key = signing_key
        self._ttl_s = ttl_s
        self._issuer = issuer

    def mint(self, identity: Identity) -> str:
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            "sub": str(identity.user_id),
            "tenant": str(identity.tenant_id),
            "roles": list(identity.roles),
            "iss": self._issuer,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self._ttl_s)).timestamp()),
        }
        return jwt.encode(claims, self._key, algorithm="HS256")

    def verify(self, token: str) -> dict[str, Any]:
        return jwt.decode(token, self._key, algorithms=["HS256"], issuer=self._issuer)
