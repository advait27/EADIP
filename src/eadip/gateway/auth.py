"""Authentication (SEC-02, FR-050).

Two modes, selected by `EADIP_AUTH_MODE`:
- "oidc": verify a Bearer JWT against the IdP and map verified claims to Identity.
- "dev":  trust `X-Tenant-ID` / `X-User-ID` / `X-Roles` headers for local use.

The dev mode is the single seam that must never run in production.
"""

from __future__ import annotations

from functools import lru_cache
from uuid import NAMESPACE_DNS, UUID, uuid5

from fastapi import Header, HTTPException, Request

from eadip.config.settings import get_settings
from eadip.security.authn.oidc import OIDCError, OIDCVerifier, identity_from_claims
from eadip.security.identity import Identity

_DEV_TENANT = uuid5(NAMESPACE_DNS, "dev-tenant.eadip")
_DEV_USER = uuid5(NAMESPACE_DNS, "dev-user.eadip")


def _parse_uuid(value: str | None, fallback: UUID) -> UUID:
    if not value:
        return fallback
    try:
        return UUID(value)
    except ValueError:
        return fallback


@lru_cache
def _get_verifier() -> OIDCVerifier:
    s = get_settings()
    if not (s.oidc_issuer and s.oidc_audience and s.oidc_jwks_uri):
        raise RuntimeError("auth_mode='oidc' requires EADIP_OIDC_ISSUER, _AUDIENCE and _JWKS_URI")
    return OIDCVerifier(issuer=s.oidc_issuer, audience=s.oidc_audience, jwks_uri=s.oidc_jwks_uri)


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return token


async def get_identity(
    request: Request,
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_roles: str | None = Header(default=None),
) -> Identity:
    settings = get_settings()
    if settings.auth_mode == "oidc":
        try:
            claims = _get_verifier().verify(_bearer_token(request))
        except OIDCError as exc:
            raise HTTPException(status_code=401, detail="invalid token") from exc
        return identity_from_claims(
            claims,
            tenant_claim=settings.oidc_tenant_claim,
            roles_claim=settings.oidc_roles_claim,
        )

    # dev mode
    return Identity(
        user_id=_parse_uuid(x_user_id, _DEV_USER),
        tenant_id=_parse_uuid(x_tenant_id, _DEV_TENANT),
        roles=tuple(x_roles.split()) if x_roles else ("analyst",),
    )
