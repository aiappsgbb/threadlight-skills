"""Denylist guard: the pre-sales estimate contribution is COST-ESTIMATION ONLY.

This skill was generalized from a private customer pilot. NOTHING customer-,
CX-, or journey-specific may leak into the public MIT repo. This test scans
every committed file under the skill (scripts, references, fixtures, SKILL.md,
tests) for a denylist of customer / project / journey terms and for obvious
secret literals. It is the automated enforcement of the "zero customer / CX
references" hard constraint. Identifying names are matched by SHA-256 so the
guard does not republish them.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent

# Customer / CX / journey terms that must NEVER appear. Lower-cased substring
# match. Kept deliberately broad — a generic pilot needs none of these.
FORBIDDEN_TERMS = [
    "customer-journey advisor",
    "journey-advisor",
    "vulnerable customer",
    "breathing space",
    "promise to pay",
    "annual price-rise",
    "collections state",
    "acc-00",          # synthetic account-id pattern from the private repo
]

# Customer, programme and person identifiers, as SHA-256 of a normalized token.
FORBIDDEN_TOKEN_SHA256 = {
    "c09dee99967309b12c066143a662efd5fc01f4b628d337037dcdf3931c0661bc",
    "67d236621f94510d80a1b862cb6f472b2e6588c4b6fb2d1d75ef3ab284014247",
    "81dd6be53aa1f8503067861a9c29ecc0d1b86d554f62e427094ddef352508372",
    "77055aea8924ace76bb1ec5f6c677c255891dc57e033fda66a18dd2d2116870a",
}

# Internal knowledge-base article ids from the private pilot.
KB_ARTICLE = re.compile(r"\bkm\d{7}\b")

# Secret-shaped literals.
SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                      # AWS key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),    # PEM
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),          # Slack token
    re.compile(r"gh[pousr]_[0-9A-Za-z]{36,}"),            # GitHub token
]

TEXT_SUFFIXES = {
    ".md", ".py", ".json", ".jsonc", ".yaml", ".yml", ".html", ".txt", ".sh",
}


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
    for p in SKILL_ROOT.rglob("*"):
        if not p.is_file():
            continue
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield p


def test_no_customer_or_cx_terms_leak():
    # This very test file lists the forbidden terms; exclude it from its own scan.
    self_name = Path(__file__).name
    offenders: list[str] = []
    for p in _files():
        if p.name == self_name:
            continue
        low = p.read_text(encoding="utf-8", errors="ignore").lower()
        for term in FORBIDDEN_TERMS:
            if term in low:
                offenders.append(f"{p.relative_to(SKILL_ROOT)} :: {term!r}")
        if _token_hashes(low) & FORBIDDEN_TOKEN_SHA256:
            offenders.append(f"{p.relative_to(SKILL_ROOT)} :: customer identifier")
        if KB_ARTICLE.search(low):
            offenders.append(f"{p.relative_to(SKILL_ROOT)} :: knowledge-base article id")
    assert not offenders, "Customer/CX terms leaked:\n" + "\n".join(offenders)


def test_no_secret_literals():
    offenders: list[str] = []
    for p in _files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                offenders.append(f"{p.relative_to(SKILL_ROOT)} :: {pat.pattern}")
    assert not offenders, "Secret-shaped literal found:\n" + "\n".join(offenders)
