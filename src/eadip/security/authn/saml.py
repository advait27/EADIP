"""SAML authentication seam (SEC-02).

OIDC is the implemented default. SAML support targets enterprises that mandate
it; this module fixes the interface so it slots in behind the same Identity
mapping. Full assertion parsing/signature validation is a follow-up sprint.
"""

from __future__ import annotations

from eadip.security.identity import Identity


class SAMLNotConfiguredError(NotImplementedError):
    pass


class SAMLVerifier:
    def verify(self, saml_response: str) -> Identity:  # noqa: ARG002
        raise SAMLNotConfiguredError(
            "SAML SSO is not yet implemented; use auth_mode='oidc' (see roadmap)."
        )
