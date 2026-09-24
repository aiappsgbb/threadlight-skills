"""Requester authentication and signed confirmation results are independent adapters."""
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

from test_user_confirmation_generation import (
    confirmation_inputs, confirmation_authority, package, stage,
)

pytestmark = pytest.mark.governance_runtime


def customer_verifier():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "issuer": "https://identity.example", "audience": "customer-confirmation",
        "client_id": "customer-web", "scope": "confirm", "key_id": "customer-key",
        "public_key": key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
    }


def test_customer_result_adapter_still_permits_its_entra_requester(tmp_path):
    data = confirmation_inputs(tmp_path)
    generator = package(data)
    stage(generator, data)
    project, document, _, deployment, _ = data
    authority = confirmation_authority(data)
    profile = authority["profiles"]["email"]
    profile.update(kind="customer-signed", result_verifier=customer_verifier())
    deployment["confirmation"] = authority
    generator.bind(project, document, configuration=deployment)
    control = json.loads((project / ".threadlight/governance-deployment.json").read_text())[
        "bindings"]["control_config"]
    assert control["confirmation_subjects"] == [profile["users"][0]["subject"]]


def test_basic_email_can_use_a_generic_customer_identity_without_entra_cast(tmp_path):
    data = confirmation_inputs(tmp_path)
    generator = package(data)
    stage(generator, data)
    project, document, _, deployment, _ = data
    authority = confirmation_authority(data)
    profile = authority["profiles"]["email"]
    identity = customer_verifier()
    profile["customer_identity"] = identity
    profile["users"][0].update(
        issuer=identity["issuer"], subject="customer-42", client=identity["client_id"])
    deployment["confirmation"] = authority
    generator.bind(project, document, configuration=deployment)
    control = json.loads((project / ".threadlight/governance-deployment.json").read_text())[
        "bindings"]["control_config"]
    assert control["confirmation_subjects"] == []
    assert control["confirmation"]["profiles"]["email"]["users"][0]["subject"] == "customer-42"
