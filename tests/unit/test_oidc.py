from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from eadip.security.authn.oidc import OIDCError, OIDCVerifier, identity_from_claims

ISSUER = "https://idp.example"
AUDIENCE = "eadip"


def _keypair() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, pub


def _token(priv: bytes, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(uuid4()),
        "tenant": str(uuid4()),
        "roles": ["analyst", "approver"],
        "email": "p@example.com",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    claims.update(overrides)
    return jwt.encode(claims, priv, algorithm="RS256")


def test_verify_and_map_claims() -> None:
    priv, pub = _keypair()
    token = _token(priv)
    verifier = OIDCVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: pub)
    claims = verifier.verify(token)
    identity = identity_from_claims(claims, tenant_claim="tenant", roles_claim="roles")
    assert identity.roles == ("analyst", "approver")
    assert identity.email == "p@example.com"
    assert str(identity.user_id) == claims["sub"]


def test_bad_signature_raises() -> None:
    priv1, _ = _keypair()
    _, pub2 = _keypair()
    verifier = OIDCVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: pub2)
    with pytest.raises(OIDCError):
        verifier.verify(_token(priv1))


def test_wrong_audience_raises() -> None:
    priv, pub = _keypair()
    verifier = OIDCVerifier(issuer=ISSUER, audience="someone-else", key_resolver=lambda _t: pub)
    with pytest.raises(OIDCError):
        verifier.verify(_token(priv))


def test_expired_token_raises() -> None:
    priv, pub = _keypair()
    past = datetime.now(UTC) - timedelta(hours=2)
    token = _token(priv, iat=past, exp=past + timedelta(minutes=1))
    verifier = OIDCVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: pub)
    with pytest.raises(OIDCError):
        verifier.verify(token)


def test_missing_tenant_claim_raises() -> None:
    with pytest.raises(OIDCError):
        identity_from_claims(
            {"sub": "u", "iss": ISSUER}, tenant_claim="tenant", roles_claim="roles"
        )
