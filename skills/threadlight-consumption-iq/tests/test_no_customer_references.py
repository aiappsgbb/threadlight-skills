"""Denylist guard: the pre-sales estimate contribution is COST-ESTIMATION ONLY.

This skill was generalized from a private customer pilot. NOTHING customer-,
CX-, or journey-specific may leak into the public MIT repo. This test scans
every committed file under the skill (scripts, references, fixtures, SKILL.md,
tests) for a denylist of customer / project / journey terms and for obvious
secret literals. It is the automated enforcement of the "zero VF3 / CX
references" hard constraint.
"""
from __future__ import annotations

import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent

# Customer / CX / journey terms that must NEVER appear. Lower-cased substring
# match. Kept deliberately broad — a generic pilot needs none of these.
FORBIDDEN_TERMS = [
    "vodafone",
    "vodafonethree",
    "vf3",
    "carl.johnson-gash",
    "customer-journey advisor",
    "journey-advisor",
    "vulnerable customer",
    "breathing space",
    "promise to pay",
    "annual price-rise",
    "collections state",
    "km1006704",
    "km1067250",
    "acc-00",          # synthetic account-id pattern from the private repo
]

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
    # This very test file names the forbidden terms; exclude it from its own scan.
    self_name = Path(__file__).name
    offenders: list[str] = []
    for p in _files():
        if p.name == self_name:
            continue
        low = p.read_text(encoding="utf-8", errors="ignore").lower()
        for term in FORBIDDEN_TERMS:
            if term in low:
                offenders.append(f"{p.relative_to(SKILL_ROOT)} :: {term!r}")
    assert not offenders, "Customer/CX terms leaked:\n" + "\n".join(offenders)


def test_no_secret_literals():
    offenders: list[str] = []
    for p in _files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                offenders.append(f"{p.relative_to(SKILL_ROOT)} :: {pat.pattern}")
    assert not offenders, "Secret-shaped literal found:\n" + "\n".join(offenders)
