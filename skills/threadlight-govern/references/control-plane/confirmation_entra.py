"""Narrow live CA-configuration verifier, not proof of a new per-transaction factor."""
import base64
import asyncio
from datetime import datetime, timedelta, timezone
import uuid

from .confirmation import ConfirmationUnavailable, digest
from .models import Timestamp, canonical, parse, strict_json


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
        self.epochs = {}
        self.lock = asyncio.Lock()

    def slot(self, profile, user):
        return (profile.conditional_access_policy_id, profile.authentication_context,
                user.issuer, user.subject, user.client)

    async def read(self, path, user):
        token = await self.credential.get_token("https://graph.microsoft.com/.default")
        if user is not None:
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
        if user is not None:
            user.fresh()
        return value

    async def observe(self, profile):
        slots = [self.slot(profile, user) for user in profile.users]
        async with self.lock:
            try:
                context = await self.read("identity/conditionalAccess/authenticationContextClassReferences/"
                                          + profile.authentication_context, None)
                policy = await self.read("identity/conditionalAccess/policies/"
                                         + profile.conditional_access_policy_id, None)
                now = datetime.now(timezone.utc)
                # A reverted policy must still expose a changed Graph generation.
                # Missing modification history cannot establish protection at issuance.
                try:
                    modified = parse(Timestamp, canonical(policy.get("modifiedDateTime")))
                except ValueError:
                    raise ConfirmationUnavailable("confirmation_ca_history_unavailable") from None
                if modified > now:
                    raise ConfirmationUnavailable("confirmation_ca_history_unavailable")
                for user in profile.users:
                    if user.issuer != self.issuer:
                        raise ConfirmationUnavailable("confirmation_ca_issuer_mismatch")
                    verify_configuration(profile, user.subject, context, policy)
                fingerprint = digest({"profile": profile.model_dump(mode="json"),
                                      "context": context, "policy": policy})
                for slot in slots:
                    prior = self.epochs.get(slot)
                    if prior is None or prior["fingerprint"] != fingerprint:
                        # Integer JWT iat must be strictly after the first successful
                        # observation, not merely after an administrator's edit.
                        prior = {"fingerprint": fingerprint, "generation": uuid.uuid4().hex,
                                 "not_before": int(now.timestamp()) + 1}
                    self.epochs[slot] = {**prior, "profile_digest": digest(profile),
                                        "valid_until": now + timedelta(seconds=30)}
            except BaseException:
                for slot in slots:
                    self.epochs.pop(slot, None)
                raise

    async def health(self, profile):
        await self.observe(profile)

    def fresh(self, profile, user, generation):
        user.fresh()
        epoch = self.epochs.get(self.slot(profile, user))
        now = datetime.now(timezone.utc)
        if (epoch is None or epoch["valid_until"] <= now
                or epoch["profile_digest"] != digest(profile)
                or user.issued_at < epoch["not_before"]
                or user.issued_at > now.timestamp()
                or generation is None or epoch["generation"] != generation):
            raise ClaimsChallenge(profile.authentication_context, self.tenant)
        return epoch["valid_until"]

    async def verify(self, profile, user):
        user.fresh()
        if user.issuer != self.issuer:
            raise ConfirmationUnavailable("confirmation_ca_issuer_mismatch")
        # Establish protection before challenging even when acrs is absent. The
        # ensuing token can satisfy this stable epoch instead of chasing new ones.
        await self.observe(profile)
        if profile.authentication_context not in user.auth_contexts:
            raise ClaimsChallenge(profile.authentication_context, self.tenant)
        generation = self.epochs[self.slot(profile, user)]["generation"]
        self.fresh(profile, user, generation)
        return generation
