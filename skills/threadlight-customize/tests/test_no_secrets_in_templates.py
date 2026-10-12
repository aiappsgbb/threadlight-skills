"""Defense-in-depth for a public, MIT-licensed repo.

This skill ships hand-fill templates, so it must never carry:
  1. a long-lived secret (these templates are not generators, but a copied
     example could leak one), or
  2. a customer-identifying name. The field notes are deliberately
     anonymized; this test keeps them that way.
"""
import hashlib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

FORBIDDEN_SECRETS = [
    "AZURE_CREDENTIALS",
    "clientSecret",
    "client-secret",
    "PERSONAL_ACCESS_TOKEN",
    "BEGIN RSA PRIVATE KEY",
    "BEGIN PRIVATE KEY",
]

# Customer-identifying names that must stay out of this public repo, as
# SHA-256 of a normalized token so this test does not republish them.
# The field notes describe "a large telco AI pilot" without naming anyone.
FORBIDDEN_NAME_SHA256 = {
    "c09dee99967309b12c066143a662efd5fc01f4b628d337037dcdf3931c0661bc",
    "67d236621f94510d80a1b862cb6f472b2e6588c4b6fb2d1d75ef3ab284014247",
    "81dd6be53aa1f8503067861a9c29ecc0d1b86d554f62e427094ddef352508372",
}

SCAN_SUFFIXES = {".md", ".tmpl", ".py", ".yml", ".yaml", ".sh", ".json"}


def _token_hashes(text: str) -> set[str]:
    """SHA-256 of every lower-cased token and of its ``.``/``-``/``_`` parts."""
    hashes: set[str] = set()
    for raw in re.findall(r"[a-z0-9][a-z0-9._-]*", text.lower()):
        token = raw.strip("._-")
        for value in [token, *re.split(r"[._-]+", token)]:
            if value:
                hashes.add(hashlib.sha256(value.encode()).hexdigest())
    return hashes


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
        hits = _token_hashes(p.read_text(encoding="utf-8")) & FORBIDDEN_NAME_SHA256
        assert not hits, (
            f"a customer name must be anonymized — found in {p.relative_to(ROOT)}"
        )
