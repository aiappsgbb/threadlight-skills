"""Narrow live CA-configuration verifier, not proof of a new per-transaction factor."""
import base64
from datetime import datetime, timezone

from .confirmation import ConfirmationUnavailable
from .models import canonical, strict_json


class ClaimsChallenge(ConfirmationUnavailable):
    def __init__(self, context, tenant):
        super().__init__("confirmation_authentication_context_required")
        claims = base64.b64encode(canonical({
            "access_token": {"acrs": {"essential": True, "value": context}}})).decode()
        self.header = ('Bearer error="insufficient_claims", '
            f'authorization_uri="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize", '
            f'claims="{claims}"')


def verify_configuration(profile, subject, context, policy):
    if (context.get("id") != profile.authentication_context or context.get("isAvailable") is not True
            or policy.get("id") != profile.conditional_access_policy_id or policy.get("state") != "enabled"):
        raise ConfirmationUnavailable("confirmation_ca_not_protected")
    conditions = policy.get("conditions", {})
    if not isinstance(conditions, dict) or conditions.get("clientAppTypes") != ["all"]:
        raise ConfirmationUnavailable("confirmation_ca_unsupported")
    known = {"applications", "users", "clientAppTypes", "signInRiskLevels", "userRiskLevels",
             "servicePrincipalRiskLevels", "platforms", "locations", "devices",
             "clientApplications", "authenticationFlows", "insiderRiskLevels"}
    if set(conditions) - known or any(value not in (None, []) for key, value in conditions.items()
        if key not in {"applications", "users", "clientAppTypes"}):
        raise ConfirmationUnavailable("confirmation_ca_unsupported")
    applications = conditions.get("applications", {})
    if (not isinstance(applications, dict)
            or applications.get("includeAuthenticationContextClassReferences") != [profile.authentication_context]
            or any(value not in (None, []) for key, value in applications.items()
                   if key != "includeAuthenticationContextClassReferences")):
        raise ConfirmationUnavailable("confirmation_ca_unsupported")
    users = conditions.get("users", {})
    if (not isinstance(users, dict) or not isinstance(users.get("includeUsers"), list)
            or not isinstance(users.get("excludeUsers", []), list)
            or not set(users["includeUsers"]).intersection({subject, "All"})
            or set(users.get("excludeUsers", [])).intersection({subject, "All"})
            or any(value not in (None, []) for key, value in users.items()
                   if key not in {"includeUsers", "excludeUsers"})):
        raise ConfirmationUnavailable("confirmation_ca_user_not_covered")
    grant = policy.get("grantControls", {})
    # Minimal shipped capability: mandatory built-in MFA only. Strength/group/risk
    # policy evaluation is deliberately unsupported rather than guessed.
    if (not isinstance(grant, dict) or grant.get("operator") != "AND"
            or grant.get("builtInControls") != ["mfa"]
            or any(value not in (None, []) for key, value in grant.items()
                   if key not in {"operator", "builtInControls"})):
        raise ConfirmationUnavailable("confirmation_ca_mfa_not_required")


class EntraConditionalAccess:
    def __init__(self, *, tenant, issuer, credential, http):
        self.tenant, self.issuer, self.credential, self.http = tenant, issuer, credential, http

    async def read(self, path, user):
        token = await self.credential.get_token("https://graph.microsoft.com/.default")
        user.fresh()
        if token.expires_on <= datetime.now(timezone.utc).timestamp():
            raise ConfirmationUnavailable("confirmation_ca_unavailable")
        async with self.http.stream("GET", "https://graph.microsoft.com/v1.0/" + path,
                headers={"Authorization": "Bearer " + token.token}, follow_redirects=False) as response:
            if response.status_code != 200:
                raise ConfirmationUnavailable("confirmation_ca_unavailable")
            raw = bytearray()
            async for part in response.aiter_bytes():
                raw.extend(part)
                if len(raw) > 32768:
                    raise ConfirmationUnavailable("confirmation_ca_unavailable")
        value = strict_json(bytes(raw))
        if not isinstance(value, dict) or "@odata.nextLink" in value:
            raise ConfirmationUnavailable("confirmation_ca_unavailable")
        user.fresh()
        return value

    async def verify(self, profile, user):
        user.fresh()
        if user.issuer != self.issuer:
            raise ConfirmationUnavailable("confirmation_ca_issuer_mismatch")
        if profile.authentication_context not in user.auth_contexts:
            raise ClaimsChallenge(profile.authentication_context, self.tenant)
        context = await self.read("identity/conditionalAccess/authenticationContextClassReferences/"
                                  + profile.authentication_context, user)
        policy = await self.read("identity/conditionalAccess/policies/"
                                 + profile.conditional_access_policy_id, user)
        verify_configuration(profile, user.subject, context, policy)
        user.fresh()
