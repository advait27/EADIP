from __future__ import annotations

from uuid import uuid4

from eadip.security.identity import Identity
from eadip.security.policy import PolicyDecisionPoint
from eadip.security.rbac import Effect, default_catalog


def _ident(*roles: str) -> Identity:
    return Identity(user_id=uuid4(), tenant_id=uuid4(), roles=roles)


def _pdp() -> PolicyDecisionPoint:
    return PolicyDecisionPoint(default_catalog())


def test_analyst_can_read_runs() -> None:
    d = _pdp().decide(_ident("analyst"), resource="run", domain="runs", effect=Effect.READ)
    assert d.permit


def test_analyst_cannot_write_tool() -> None:
    d = _pdp().decide(_ident("analyst"), resource="tool", domain="jira", effect=Effect.WRITE)
    assert not d.permit


def test_approver_can_write_tool() -> None:
    d = _pdp().decide(_ident("approver"), resource="tool", domain="jira", effect=Effect.WRITE)
    assert d.permit


def test_unknown_role_denied_by_default() -> None:
    d = _pdp().decide(_ident("nobody"), resource="run", domain="runs", effect=Effect.READ)
    assert not d.permit
    assert "deny-by-default" in d.reason


def test_no_roles_denied() -> None:
    assert not _pdp().decide(_ident(), resource="run", domain="runs", effect=Effect.READ).permit


def test_admin_wildcard_grants_high_impact() -> None:
    d = _pdp().decide(_ident("admin"), resource="anything", domain="x", effect=Effect.HIGH_IMPACT)
    assert d.permit
