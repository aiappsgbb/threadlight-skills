"""Public-safety guard for the public repository and its GitHub Pages site.

Identifying values (real deployment targets, customer and personal names,
internal policy names) are matched by SHA-256 of normalized tokens so this
guard never republishes what it protects.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"

FORBIDDEN_TOKEN_SHA256 = {
    # Real CI deployment target: subscription, resource group, account.
    "7826f5a241f74b44581cdc2c8d2231ea171f18bd116411a7b3a3ebdd0445eacb",
    "d381b5215cfc56da3465f19b05319165a35d36006ef450f36ad95a917c43483f",
    "10d60aaa4666fe6b66f0485d69f0c83530ba39cab3fdff6c607b9bb43a1f9e78",
    # Internal organization policy-set name.
    "fa4cfe0f600c1d621e9d5c0795d02c732bfbeb60bb1f02048c00fa72262a0b49",
    # Customer-identifying names and a person.
    "c09dee99967309b12c066143a662efd5fc01f4b628d337037dcdf3931c0661bc",
    "67d236621f94510d80a1b862cb6f472b2e6588c4b6fb2d1d75ef3ab284014247",
    "81dd6be53aa1f8503067861a9c29ecc0d1b86d554f62e427094ddef352508372",
    "77055aea8924ace76bb1ec5f6c677c255891dc57e033fda66a18dd2d2116870a",
    # Personal aliases.
    "0674a3040cc01c5d6bc47f63af1df72b37a68af626a0866d35b4d9c47597ed77",
    "0f447dc02c09d52bc7c21f49147b37ed1914e136b8493da37ab2f9db07d225a7",
    "f1d654b62242e415e8e2bc42714013e0823eb1772d8112e7bcdf7dbda364e0c5",
}

# Public brand references deliberately kept (brand palette, fictional-name
# guidance). Only the customer-brand token is tolerated there.
BRAND_ALLOWLIST = {
    "skills/threadlight-design/references/brand-palettes.md",
    "skills/threadlight-design/references/data-realism/README.md",
    "skills/threadlight-design/references/data-realism/telco.md",
}
BRAND_TOKEN = "c09dee99967309b12c066143a662efd5fc01f4b628d337037dcdf3931c0661bc"

FORBIDDEN_PATTERNS = {
    "personal home path": re.compile(r"/Users/(?!me/|<)[A-Za-z0-9._-]+/"),
    "Copilot session-state path": re.compile(r"\.copilot/session-state/[0-9a-f]{8}-"),
    "internal knowledge-base article id": re.compile(r"\bkm\d{7}\b", re.IGNORECASE),
}

TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]*")
TEXT_SUFFIXES = {
    ".md", ".html", ".js", ".mjs", ".css", ".json", ".py", ".sh", ".ps1", ".yml",
    ".yaml", ".toml", ".txt", ".bicep", ".bicepparam", ".tmpl", ".env", ".rego",
    ".jsonl", ".tsv", ".csv", ".ini", ".cfg", ".svg", ".log", "",
}
SELF = Path(__file__).resolve()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _token_hashes(text: str) -> set[str]:
    hashes: set[str] = set()
    for raw in TOKEN.findall(text.lower()):
        token = raw.strip("._-")
        if not token:
            continue
        hashes.add(_sha(token))
        for part in re.split(r"[._-]+", token):
            if part:
                hashes.add(_sha(part))
    return hashes


def _tracked_text_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode()
    for rel in filter(None, out.split("\0")):
        path = ROOT / rel
        if path.resolve() == SELF or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if not path.is_file():
            continue
        try:
            yield rel, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def test_repository_has_no_identifying_tokens():
    offenders = []
    for rel, text in _tracked_text_files():
        hits = _token_hashes(text) & FORBIDDEN_TOKEN_SHA256
        if rel in BRAND_ALLOWLIST:
            hits.discard(BRAND_TOKEN)
        if hits:
            offenders.append(f"{rel}: {len(hits)} identifying token(s)")
    assert not offenders, "\n".join(offenders)


def test_repository_has_no_internal_paths_or_ids():
    offenders = []
    for rel, text in _tracked_text_files():
        for label, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(text):
                offenders.append(f"{rel}: {label}")
    assert not offenders, "\n".join(offenders)


def test_public_validation_records_describe_policy_exemptions_neutrally():
    for rel in (
        "docs/governed-returns-validation.md",
        "docs/production-readiness-pages-spec.md",
        "skills/threadlight-deploy/references/governance/returns-mcp-demo.md",
    ):
        text = (ROOT / rel).read_text()
        assert "SecurityControl" not in text, rel


def test_router_helpers_take_their_target_from_the_environment():
    for rel in ("scripts/ci/router-subset.sh", "scripts/ci/foundry-strong-arm.sh"):
        text = (ROOT / rel).read_text()
        assert re.search(r'RG="\$\{ROUTER_RESOURCE_GROUP:\?', text), rel
        assert re.search(r'ACC="\$\{ROUTER_ACCOUNT:\?', text), rel
    assert re.search(
        r'SUB="\$\{ROUTER_SUBSCRIPTION_ID:\?',
        (ROOT / "scripts/ci/router-subset.sh").read_text(),
    )


INTERNAL_NOTES = (
    "superpowers/",
    "solution-review-2026-09-15.md",
    "production-readiness-pages-spec.md",
)


def test_pages_build_excludes_internal_notes():
    config = yaml.safe_load((DOCS / "_config.yml").read_text())
    excluded = set(config.get("exclude", []))
    for note in INTERNAL_NOTES:
        assert note in excluded, note
    for path in DOCS.glob("*.md"):
        if path.name.startswith("solution-review-") or path.name.endswith("-pages-spec.md"):
            assert path.name in excluded, path.name
    # Jekyll defaults must survive the custom exclude list.
    for default in ("node_modules", "vendor/bundle/", "Gemfile", "Gemfile.lock"):
        assert default in excluded, default


def test_public_pages_do_not_link_to_excluded_notes():
    stems = [n.removesuffix(".md") for n in INTERNAL_NOTES if n.endswith(".md")]
    for page in DOCS.glob("*.html"):
        text = page.read_text()
        for stem in stems:
            assert not re.search(rf'href="(?:\./)?{re.escape(stem)}(?:\.html|\.md)?["#]', text), (
                page.name,
                stem,
            )
        assert 'href="./superpowers/' not in text and 'href="superpowers/' not in text, page.name
