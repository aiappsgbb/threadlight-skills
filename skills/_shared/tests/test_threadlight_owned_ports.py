"""Offline contract for the Threadlight-owned ports of awesome-gbb skills.

Threadlight ships as one package: its runtime companions are the official
azure@azure-skills plugin and threadlight-skills itself. Capabilities with no
official equivalent were ported from aiappsgbb/awesome-gbb (MIT) into
Threadlight-owned skills or references. These checks prove the ports exist,
carry provenance and are what live surfaces route to. They do not prove the
ported runtime behaviour.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
SKILLS = ROOT / "skills"
MANIFEST = SKILLS / "_shared" / "skill-dependencies.json"
LOCK = SKILLS / "_shared" / "official-skills-lock.json"
SOURCE_SHA = "7f1de882d5386e5a27852c91d3a89523eef218d0"

PORTED_SKILLS = {
    "citadel-hub-deploy": "threadlight-citadel-hub",
    "citadel-spoke-onboarding": "threadlight-citadel-spoke",
    "azure-tenant-isolation": "threadlight-tenant-isolation",
    "foundry-mcp-aca": "threadlight-mcp-aca",
}
PORTED_REFERENCES = {
    "azd-patterns": "threadlight-deploy/references/azd-modules",
    "foundry-hosted-agents": "threadlight-deploy/references/hosted-agent/maf",
    "ghcp-hosted-agents": "threadlight-deploy/references/hosted-agent/ghcp",
    "foundry-agt": "threadlight-govern/references/agt-inprocess",
}
PORT_ROOTS = [*PORTED_SKILLS.values(), *PORTED_REFERENCES.values()]

# Historical or generated surfaces that may keep the awesome-gbb names.
HISTORY = re.compile(
    r"^(CHANGELOG\.md"
    r"|docs/superpowers/"
    r"|examples/[^/]+/archive/"
    r"|skills/_shared/(skill-dependencies|official-skills-lock)\.json"
    r"|skills/_shared/tests/"
    r"|skills/[^/]+/(references/)?.*PROVENANCE\.md"
    # Approved workbook, byte-frozen by tests/blueprint (sha1 blob 1d6ed17f); changing
    # its pinned companions needs an explicit re-approval of that freeze.
    r"|docs/first-governed-workflow\.md$|tests/blueprint/first-governed-workflow\.test\.js$"
    r")"
)
# `foundry-agt` is also a legacy Python distribution name; lines that name it next to
# the agent-governance-toolkit package are dependency detection, not skill routing.
PACKAGE_LINE = re.compile(r"foundry-agt.*agent-governance-toolkit|agent-governance-toolkit.*foundry-agt")
TEXT_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".js", ".html", ".txt", ".bicep", ".sh", ".tmpl"}


def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def official_names():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    return {n for names in lock["catalog_skill_names"].values() for n in names}


def front(skill_dir):
    return yaml.safe_load((SKILLS / skill_dir / "SKILL.md").read_text(encoding="utf-8").split("---")[1])


def routed_away_names():
    """awesome-gbb names a live surface must no longer route to: the ported ones
    and the official-default ones that are not themselves official skill names."""
    data, official = manifest(), official_names()
    replaced = {n for n, e in data["skills"].items() if e["label"] == "official-default" and n not in official}
    return replaced | set(PORTED_SKILLS) | set(PORTED_REFERENCES)


def live_files():
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    for rel in out.splitlines():
        path = ROOT / rel
        if HISTORY.match(rel) or path.suffix not in TEXT_SUFFIXES or not path.is_file():
            continue
        yield rel, path.read_text(encoding="utf-8", errors="replace")


def test_ported_skills_are_threadlight_owned_and_routable():
    for source, directory in PORTED_SKILLS.items():
        meta = front(directory)
        assert meta["name"] == directory
        assert meta["description"].startswith("Threadlight-owned"), directory
        assert len(meta["description"]) <= 1024, directory
        assert "PROVENANCE.md" in meta["metadata"]["provenance"], directory


def test_every_port_records_mit_provenance_at_the_source_commit():
    for root in PORT_ROOTS:
        prov = SKILLS / root / "PROVENANCE.md"
        assert prov.exists(), f"{root}: PROVENANCE.md missing"
        text = prov.read_text(encoding="utf-8")
        assert SOURCE_SHA in text, root
        assert "MIT License" in text and "AI Global Black Belts" in text, root
        assert "Permission is hereby granted, free of charge" in text, root


def test_ported_reference_indexes_carry_a_threadlight_owned_banner():
    for root in PORTED_REFERENCES.values():
        readme = (SKILLS / root / "README.md").read_text(encoding="utf-8")
        assert "Threadlight-owned" in readme[:1500], root
        assert not readme.startswith("---"), f"{root}: a reference must not carry skill frontmatter"


def test_manifest_labels_ports_threadlight_owned_with_existing_targets():
    data = manifest()
    assert "threadlight-owned" in data["policy"]
    for source, target in {**PORTED_SKILLS, **PORTED_REFERENCES}.items():
        entry = data["skills"][source]
        if source in ("azd-patterns", "foundry-hosted-agents"):
            assert entry["label"] == "official-default", source
        else:
            assert entry["label"] == "threadlight-owned", source
        assert entry["threadlight_target"] == f"skills/{target}", source
        assert (ROOT / entry["threadlight_target"]).is_dir(), source
        assert entry["provenance"].startswith("awesome-gbb@7f1de882"), source


def test_relative_links_inside_ports_resolve():
    link = re.compile(r"\]\((?!https?:|mailto:|#)([^)\s#]+)(?:#[^)\s]*)?\)")
    broken = []
    for root in PORT_ROOTS:
        for md in (SKILLS / root).rglob("*.md"):
            for m in link.finditer(md.read_text(encoding="utf-8")):
                if not (md.parent / m.group(1)).exists():
                    broken.append(f"{md.relative_to(ROOT)}: {m.group(0)}")
    assert not broken, broken


def test_live_surfaces_route_to_official_or_threadlight_owned_skills():
    names = routed_away_names()
    pattern = re.compile(r"(?<![\w/.-])(" + "|".join(map(re.escape, sorted(names, key=len, reverse=True))) + r")(?![\w-])")
    offenders = {}
    for rel, text in live_files():
        text = "\n".join(line for line in text.splitlines() if not PACKAGE_LINE.search(line))
        hits = sorted({m.group(1) for m in pattern.finditer(text)})
        if hits:
            offenders[rel] = hits
    assert not offenders, f"{len(offenders)} live files still route to awesome-gbb names: {offenders}"


def test_live_surfaces_do_not_tell_users_to_install_awesome_gbb():
    install = re.compile(r"install (?:it |them )?from [`']?aiappsgbb/awesome-gbb|plugin install [\w-]+@awesome-gbb", re.I)
    offenders = [rel for rel, text in live_files() if install.search(text) and not rel.startswith(".github/workflows/")]
    assert not offenders, offenders


PERSONAL_ALIAS = re.compile(r"\b(ricchi|fruocco|frocco)\b", re.IGNORECASE)


def test_ports_carry_no_personal_aliases_or_targets():
    hits = []
    for root in PORT_ROOTS:
        for path in sorted((SKILLS / root).rglob("*")):
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if PERSONAL_ALIAS.search(line):
                        hits.append(f"{path.relative_to(ROOT)}:{number}")
    assert not hits, "Ported content must not name personal aliases:\n" + "\n".join(hits)
