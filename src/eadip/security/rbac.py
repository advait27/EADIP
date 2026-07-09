"""RBAC model: roles grant permissions over (resource, data-domain, effect).

Least-privilege and deny-by-default (SEC-03, AP-5). In production the catalog is
loaded from the role/permission tables; Phase 2 ships a sensible default catalog
so the gateway is governed out of the box.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

WILDCARD = "*"


class Effect(StrEnum):
    READ = "read"
    WRITE = "write"
    HIGH_IMPACT = "high_impact"


@dataclass(frozen=True)
class Permission:
    """A grant. `resource`/`domain` may be the `*` wildcard; `effect` is exact."""

    resource: str
    domain: str
    effect: Effect

    def matches(self, *, resource: str, domain: str, effect: Effect) -> bool:
        return (
            self.effect == effect
            and self.resource in (resource, WILDCARD)
            and self.domain in (domain, WILDCARD)
        )


@dataclass(frozen=True)
class Role:
    name: str
    permissions: frozenset[Permission]


class RoleCatalog:
    """Maps role names to roles. Unknown roles contribute no permissions."""

    def __init__(self, roles: Iterable[Role]) -> None:
        self._roles: dict[str, Role] = {r.name: r for r in roles}

    def get(self, name: str) -> Role | None:
        return self._roles.get(name)

    def upsert(self, role: Role) -> None:
        """Install or replace a role (Phase 11 admin self-serve RBAC, FR-057).
        Deny-by-default is preserved: a role only ever *adds* explicit grants,
        and identities naming unknown roles still get nothing."""
        self._roles[role.name] = role

    def all(self) -> list[Role]:
        return [self._roles[name] for name in sorted(self._roles)]

    def effective_permissions(self, role_names: Iterable[str]) -> frozenset[Permission]:
        perms: set[Permission] = set()
        for name in role_names:
            role = self._roles.get(name)
            if role is not None:
                perms |= role.permissions
        return frozenset(perms)


def default_catalog() -> RoleCatalog:
    """Built-in roles. Tenancy is enforced separately by RLS, not by RBAC."""
    analyst = Role(
        name="analyst",
        permissions=frozenset(
            {
                Permission("run", "runs", Effect.READ),
                Permission("knowledge", "*", Effect.READ),
                Permission("metrics", "*", Effect.READ),
                # Read-only governed tools (Phase 8) are a read tier, like the
                # knowledge/metrics reads; write/high-impact tools stay approver-only.
                Permission("tool", "*", Effect.READ),
            }
        ),
    )
    # An approver may additionally authorize write/high-impact actions (Phase 9).
    approver = Role(
        name="approver",
        permissions=analyst.permissions
        | frozenset(
            {Permission("tool", "*", Effect.WRITE), Permission("tool", "*", Effect.HIGH_IMPACT)}
        ),
    )
    viewer = Role(name="viewer", permissions=frozenset({Permission("run", "runs", Effect.READ)}))
    compliance = Role(
        name="compliance", permissions=frozenset({Permission("audit", "*", Effect.READ)})
    )
    admin = Role(
        name="admin", permissions=frozenset({Permission(WILDCARD, WILDCARD, e) for e in Effect})
    )
    return RoleCatalog([analyst, approver, viewer, compliance, admin])
