from __future__ import annotations

from uuid import uuid4

import jwt
import pytest

from eadip.security.identity import Identity
from eadip.security.tokens import ServiceTokenIssuer


def _identity() -> Identity:
    return Identity(user_id=uuid4(), tenant_id=uuid4(), roles=("analyst",))


_KEY_A = "test-signing-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # >=32 bytes (RFC 7518)
_KEY_B = "test-signing-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_mint_verify_roundtrip() -> None:
    issuer = ServiceTokenIssuer(_KEY_A, ttl_s=300)
    identity = _identity()
    claims = issuer.verify(issuer.mint(identity))
    assert claims["sub"] == str(identity.user_id)
    assert claims["tenant"] == str(identity.tenant_id)
    assert claims["roles"] == ["analyst"]


def test_wrong_key_fails() -> None:
    token = ServiceTokenIssuer(_KEY_A).mint(_identity())
    with pytest.raises(jwt.PyJWTError):
        ServiceTokenIssuer(_KEY_B).verify(token)
