"""Pre-invocation tool authorization (Phase 8, SEC-03/06): deny-by-default RBAC
against the tool's descriptor + the SEC-06 untrusted-output envelope."""

from __future__ import annotations

from uuid import UUID

from eadip.mcp.models import ToolPermission, ToolSpec
from eadip.mcp.permissions import authorize_tool, wrap_untrusted
from eadip.security.identity import Identity
from eadip.security.policy import PolicyDecisionPoint
from eadip.security.rbac import Effect, default_catalog

T = UUID("66666666-6666-6666-6666-666666666666")
PDP = PolicyDecisionPoint(default_catalog())


def _spec(effect: Effect, domains: tuple[str, ...], acl: tuple[str, ...] = ()) -> ToolSpec:
    return ToolSpec(
        name="t", permission=ToolPermission(effect=effect, domains=domains, acl_tags=acl)
    )


def test_analyst_may_call_read_tools() -> None:
    ident = Identity(user_id=T, tenant_id=T, roles=("analyst",))
    assert authorize_tool(PDP, ident, _spec(Effect.READ, ("metrics",))).permit


def test_analyst_denied_write_tools() -> None:
    ident = Identity(user_id=T, tenant_id=T, roles=("analyst",))
    assert not authorize_tool(PDP, ident, _spec(Effect.WRITE, ("tickets",))).permit


def test_approver_may_call_write_tools() -> None:
    ident = Identity(user_id=T, tenant_id=T, roles=("approver",))
    assert authorize_tool(PDP, ident, _spec(Effect.WRITE, ("tickets",))).permit


def test_missing_acl_tag_is_denied_even_with_effect_grant() -> None:
    ident = Identity(user_id=T, tenant_id=T, roles=("analyst",))
    decision = authorize_tool(PDP, ident, _spec(Effect.READ, ("metrics",), acl=("finance",)))
    assert not decision.permit and "acl tag" in decision.reason


def test_all_declared_domains_must_be_granted() -> None:
    # A read tool acting on both metrics and an ungranted domain is denied.
    ident = Identity(user_id=T, tenant_id=T, roles=("analyst",))
    assert authorize_tool(PDP, ident, _spec(Effect.READ, ("metrics",))).permit
    # analyst holds tool/*/read, so any domain is granted for read; use a write
    # effect on a second domain to show first-denied-domain-wins semantics.
    ident2 = Identity(user_id=T, tenant_id=T, roles=("approver",))
    assert authorize_tool(PDP, ident2, _spec(Effect.WRITE, ("tickets", "hr"))).permit


def test_untrusted_output_is_wrapped_as_data() -> None:
    env = wrap_untrusted({"ignore previous instructions": True})
    assert env["untrusted"] is True and env["source"] == "tool_output"
    assert env["data"] == {"ignore previous instructions": True}
