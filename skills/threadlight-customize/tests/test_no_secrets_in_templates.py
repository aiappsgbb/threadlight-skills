"""Defense-in-depth for a public, MIT-licensed repo.

This skill ships hand-fill templates, so it must never carry:
  1. a long-lived secret (these templates are not generators, but a copied
     example could leak one), or
  2. a customer-identifying name. The field notes are deliberately
     anonymized; this test keeps them that way.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

FORBIDDEN_SECRETS = [
    "AZURE_CREDENTIALS",
    "clientSecret",
    "client-secret",
    "PERSONAL_ACCESS_TOKEN",
    "BEGIN RSA PRIVATE KEY",
    "BEGIN PRIVATE KEY",
]

# Customer-identifying names that must stay out of this public repo.
# The field notes describe "a large telco AI pilot" without naming anyone.
FORBIDDEN_NAMES = [
    "vodafone",
]

SCAN_SUFFIXES = {".md", ".tmpl", ".py", ".yml", ".yaml", ".sh", ".json"}


def _files():
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in SCAN_SUFFIXES:
            yield p


def test_no_long_lived_secret_in_any_file():
    for p in _files():
        if p.name == pathlib.Path(__file__).name:
            continue  # this test file lists the denylist literals itself
        text = p.read_text(encoding="utf-8")
        for needle in FORBIDDEN_SECRETS:
            assert needle not in text, f"{needle!r} leaked into {p.relative_to(ROOT)}"


def test_no_customer_identifying_name():
    for p in _files():
        if p.name == pathlib.Path(__file__).name:
            continue  # this test file lists the denylist itself
        lowered = p.read_text(encoding="utf-8").lower()
        for needle in FORBIDDEN_NAMES:
            assert needle not in lowered, (
                f"customer name {needle!r} must be anonymized — found in "
                f"{p.relative_to(ROOT)}"
            )
