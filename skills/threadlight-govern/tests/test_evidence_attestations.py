"""Offline issuer fixtures, not document certification or live Azure evidence."""
import asyncio
from dataclasses import replace
import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from test_control_plane import APP, KEY, OTHER, TENANT, WORKLOAD, TestSigner, module


def evidence():
    return module("attestations")


def fixture():
    e = evidence()
    signer = TestSigner()
    public = signer.key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    requirement = e.EvidenceRequirement(
        issuer="https://evidence.example", audience="api://returns-evidence",
        profile="purchase-v1", purpose="record-return",
        keys=[{"kid": KEY, "public_key": public}],
        subjects={"RMA-1": "customer-1"},
        case_field="case_id", revision_field="expected_etag", max_age_seconds=300)
    identity = module("auth").Identity(
        TENANT, WORKLOAD, APP, frozenset({"Governance.Workload"}), frozenset(),
        module("auth").Workload(client_id=APP, agent_id="agent-1", policies=["safe"]))
    arguments = {"case_id": "RMA-1", "expected_etag": "revision-1", "amount": 1500}
    source = e.SourceReference(
        reference="ledger-purchase-1", revision="purchase-3", digest=e.fingerprint({"amount": 1500}))
    result = e.VerificationResult(
        subject="customer-1", case_id="RMA-1", revision="revision-1",
        sources=[source], claims={"purchase_verified": True, "amount": 1500, "defect_declared": True})

    class FixtureAdapter:
        calls = 0
        async def verify(self, authenticated, supplied):
            self.calls += 1
            return result

    adapter = FixtureAdapter()
    provider = e.EvidenceProvider(
        requirement=requirement, action="refund", tenant=TENANT, signer=signer, key_id=KEY,
        adapter=adapter, grants=[e.EvidenceGrant(
            principal=WORKLOAD, client=APP, case_id="RMA-1", subject="customer-1")])
    facts = {"tenant": TENANT, "subject": WORKLOAD, "client": APP, "action": "refund"}
    return e, signer, requirement, identity, arguments, result, adapter, provider, facts


def test_standard_jwt_and_semantic_renewal():
    async def run():
        e, signer, requirement, identity, args, result, adapter, provider, facts = fixture()
        first = await provider.issue(identity, args)
        second = await provider.issue(identity, args)
        assert first["status"] == "verified" and first["attestation"] != second["attestation"]
        a = e.verify_attestation(first["attestation"], requirement, facts, args)
        b = e.verify_attestation(second["attestation"], requirement, facts, args)
        assert a.fingerprint == b.fingerprint
        assert a.safe["claims"]["purchase_verified"] is True
        assert "exp" not in a.safe and "jti" not in a.safe
        decoded = jwt.decode(first["attestation"], signer.key.public_key(), algorithms=["RS256"],
                             issuer=requirement.issuer, audience=requirement.audience)
        assert decoded["sub"] == "customer-1" and decoded["holder"] == WORKLOAD
        assert decoded["arguments_digest"] == e.fingerprint(args)
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"iss": "https://forged.example"}, {"aud": "api://wrong"}, {"tid": OTHER},
    {"sub": "customer-2"}, {"holder": OTHER}, {"holder_client": OTHER},
    {"case_id": "RMA-2"}, {"action": "pay"}, {"purpose": "settle"},
    {"revision": "revision-2"}, {"profile": "other"}, {"exp": 1},
    {"iat": 9999999999}, {"claims": {"purchase_verified": "true"}},
])
def test_wrong_signature_or_binding_rejected(change):
    async def run():
        e, signer, requirement, identity, args, _, _, provider, facts = fixture()
        issued = await provider.issue(identity, args)
        claims = jwt.decode(issued["attestation"], options={"verify_signature": False})
        claims.update(change)
        # Even an otherwise trusted issuer cannot rebind the caller/case or subject.
        token = jwt.encode(claims, signer.key, algorithm="RS256",
                           headers={"kid": KEY, "typ": e.EVIDENCE_TYPE})
        with pytest.raises(e.EvidenceError):
            e.verify_attestation(token, requirement, facts, args)
    asyncio.run(run())


@pytest.mark.parametrize("variant", ["tamper", "other-key", "HS256", "none", "type", "jku", "kid"])
def test_token_confusion_rejected(variant):
    async def run():
        e, signer, requirement, identity, args, _, _, provider, facts = fixture()
        issued = await provider.issue(identity, args)
        claims = jwt.decode(issued["attestation"], options={"verify_signature": False})
        headers = {"kid": KEY, "typ": e.EVIDENCE_TYPE}
        key, algorithm = signer.key, "RS256"
        if variant == "tamper":
            token = issued["attestation"][:-8] + "AAAAAAAA"
        else:
            if variant == "other-key":
                key = TestSigner().key
            elif variant == "HS256":
                key, algorithm = "x" * 32, "HS256"
            elif variant == "none":
                key, algorithm = None, "none"
            elif variant == "type":
                headers["typ"] = "JWT"
            elif variant == "jku":
                headers["jku"] = "https://attacker.example"
            elif variant == "kid":
                headers["kid"] = OTHER
            token = jwt.encode(claims, key, algorithm=algorithm, headers=headers)
        with pytest.raises(e.EvidenceError):
            e.verify_attestation(token, requirement, facts, args)
    asyncio.run(run())


@pytest.mark.parametrize("field,value", [("amount", 1501), ("expected_etag", "revision-2"),
                                        ("case_id", "RMA-2")])
def test_material_arguments_bound(field, value):
    async def run():
        e, _, requirement, identity, args, _, _, provider, facts = fixture()
        issued = await provider.issue(identity, args)
        with pytest.raises(e.EvidenceError):
            e.verify_attestation(issued["attestation"], requirement, facts, {**args, field: value})
    asyncio.run(run())


def test_authorization_precedes_source_access_and_no_arbitrary_claims():
    async def run():
        e, _, _, identity, args, _, adapter, provider, _ = fixture()
        for actor in (replace(identity, tenant=OTHER), replace(identity, subject=OTHER),
                      replace(identity, client=OTHER), replace(identity, workload=None)):
            with pytest.raises(e.EvidenceError, match="evidence_scope_denied"):
                await provider.issue(actor, args)
        with pytest.raises(e.EvidenceError):
            await provider.issue(identity, {**args, "case_id": "RMA-2"})
        assert adapter.calls == 0
        forged = await provider.issue(identity, {**args, "claims": {"defect_verified": True}})
        claims = jwt.decode(forged["attestation"], options={"verify_signature": False})
        assert "defect_verified" not in claims["claims"]
    asyncio.run(run())


def test_insufficient_evidence_and_unavailable_provider_never_sign(caplog):
    async def run():
        e, _, _, identity, args, _, adapter, provider, _ = fixture()
        async def insufficient(*_):
            return None
        adapter.verify = insufficient
        assert await provider.issue(identity, args) == {
            "status": "insufficient_evidence", "reason_code": "corroboration_missing",
            "profile": "purchase-v1"}
        async def unavailable(*_):
            raise OSError("PRIVATE DOCUMENT")
        adapter.verify = unavailable
        with pytest.raises(e.EvidenceError, match="evidence_provider_unavailable"):
            await provider.issue(identity, args)
        assert "PRIVATE DOCUMENT" not in caplog.text
    asyncio.run(run())


def test_loan_fixture_uses_same_issuer_and_validator():
    async def run():
        e, _, requirement, identity, args, _, adapter, provider, facts = fixture()
        # Fixture source, explicitly not a real payroll or document issuer.
        document = {"extracted_income": 50000, "ocr_confidence": 0.99}
        payroll = None
        async def corroborate(*_):
            if payroll is None or document["extracted_income"] != payroll["annual_income"]:
                return None
            return e.VerificationResult(
                subject="customer-1", case_id="RMA-1", revision="revision-1",
                sources=[e.SourceReference(reference="fixture-payroll", revision="v2",
                                           digest=e.fingerprint(payroll))],
                claims={"income_corroborated": True, "annual_income": payroll["annual_income"]})
        adapter.verify = corroborate
        requirement = requirement.model_copy(update={"profile": "loan-income-fixture-v1"})
        provider.requirement = requirement
        assert (await provider.issue(identity, args))["status"] == "insufficient_evidence"
        payroll = {"annual_income": 50000}
        issued = await provider.issue(identity, args)
        verified = e.verify_attestation(issued["attestation"], requirement, facts, args)
        assert verified.safe["claims"] == {"income_corroborated": True, "annual_income": 50000}
        assert "ocr_confidence" not in json.dumps(verified.safe)
    asyncio.run(run())
