"""OIDC token verification + claim mapping (SEC-02, FR-050).

Verifies a Bearer JWT against the enterprise IdP and maps verified claims to an
Identity. The signing-key resolver is injectable so verification is unit-testable
offline (tests sign with a local key); in production it resolves via the IdP's
JWKS endpoint.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import jwt

from eadip.security.identity import Identity

KeyResolver = Callable[[str], Any]


class OIDCError(Exception):
    """Token could not be verified."""


def _as_uuid(value: str, *, seed: str) -> UUID:
    """Use the claim verbatim if it is a UUID, else derive a stable one."""
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return uuid5(NAMESPACE_URL, f"{seed}:{value}")


def _parse_roles(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(r for r in value.split() if r)
    if isinstance(value, (list, tuple)):
        return tuple(str(r) for r in value)
    return ()


def identity_from_claims(
    claims: dict[str, Any], *, tenant_claim: str, roles_claim: str
) -> Identity:
    issuer = str(claims.get("iss", "eadip"))
    sub = claims.get("sub")
    tenant = claims.get(tenant_claim)
    if not sub or not tenant:
        raise OIDCError(f"token missing required claims: sub and {tenant_claim}")
    return Identity(
        user_id=_as_uuid(str(sub), seed=issuer),
        tenant_id=_as_uuid(str(tenant), seed=issuer),
        roles=_parse_roles(claims.get(roles_claim)),
        email=claims.get("email"),
    )


class OIDCVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_uri: str | None = None,
        key_resolver: KeyResolver | None = None,
        algorithms: tuple[str, ...] = ("RS256",),
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_uri = jwks_uri
        self._algorithms = list(algorithms)
        self._key_resolver = key_resolver
        self._jwk_client: Any | None = None

    def _resolve_key(self, token: str) -> Any:
        if self._key_resolver is not None:
            return self._key_resolver(token)
        if self._jwks_uri is None:
            raise OIDCError("no key_resolver and no jwks_uri configured")
        if self._jwk_client is None:
            self._jwk_client = jwt.PyJWKClient(self._jwks_uri)
        return self._jwk_client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> dict[str, Any]:
        try:
            key = self._resolve_key(token)
            return jwt.decode(
                token,
                key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.PyJWTError as exc:  # invalid signature / exp / aud / iss
            raise OIDCError(str(exc)) from exc
