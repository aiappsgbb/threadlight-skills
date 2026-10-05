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
import os
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
    "foundry-observability": "threadlight-deploy/references/observability",
}
PORT_ROOTS = [*PORTED_SKILLS.values(), *PORTED_REFERENCES.values()]

# Historical or generated surfaces that may keep the awesome-gbb names.
HISTORY = re.compile(
    r"^(CHANGELOG\.md"
    r"|docs/superpowers/"
    # Dated execution record (2026-09-11): it describes the skills that were used then.
    r"|docs/governed-returns-validation\.md$"
    r"|examples/[^/]+/archive/"
    r"|skills/_shared/(skill-dependencies|official-skills-lock)\.json"
    r"|skills/_shared/tests/"
    r"|skills/[^/]+/(references/)?.*PROVENANCE\.md"
    # Approved workbook, byte-frozen by tests/blueprint (sha1 blob 1d6ed17f); changing
    # its pinned companions needs an explicit re-approval of that freeze.
    r"|docs/first-governed-workflow\.md$|tests/blueprint/first-governed-workflow\.test\.js$"
    # Pinned explanatory snapshot, byte-frozen by tests/blueprint (sha1 blob 909409fe).
    r"|docs/agent-governance-deep-dive\.md$"
    r")"
)
# `foundry-agt` is also a legacy Python distribution name; lines that name it next to
# the agent-governance-toolkit package are dependency detection, not skill routing.
# The upstream CI action `foundry-agt/verify@<sha>` and pip specifiers are likewise identifiers.
PACKAGE_LINE = re.compile(
    r"foundry-agt.*agent-governance-toolkit|agent-governance-toolkit.*foundry-agt"
    r"|foundry-agt/verify@|foundry-agt\s*(==|>=|~=)"
)
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
        if source in ("azd-patterns", "foundry-hosted-agents", "foundry-observability"):
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


PIN_TAG = json.loads(LOCK.read_text(encoding="utf-8"))["tag"]
OFFICIAL_PATHS = ROOT / "skills" / "_shared" / "tests" / "fixtures" / "official-paths.json"


def test_agt_detector_still_matches_the_upstream_ci_action():
    import importlib.util
    path = SKILLS / "threadlight-govern/references/agt-inprocess/references/python/capability_detector.py"
    spec = importlib.util.spec_from_file_location("tl_capability_detector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._CI_ACTION_RE.search("uses: foundry-agt/verify@" + "a" * 40)


def test_live_surfaces_do_not_link_reference_skill_files_that_do_not_exist():
    offenders = []
    for root in ("threadlight-deploy/references/azd-modules", "threadlight-deploy/references/hosted-agent/maf",
                 "threadlight-deploy/references/hosted-agent/ghcp", "threadlight-deploy/references/observability",
                 "threadlight-govern/references/agt-inprocess"):
        for rel, text in live_files():
            if f"{root}/SKILL.md" in text:
                offenders.append(f"{rel}: {root}/SKILL.md")
    assert not offenders, offenders


# Content the awesome-gbb originals owned and the pinned official skills do not ship.
NOT_IN_OFFICIAL = re.compile(
    r"O-01[12]|otel_init|init_telemetry|connect_foundry_appinsights|cp1252|Layer 2|layers 2 \+ 3|3-Layer"
    r"|Warmup retry|Resume-after-cooldown|Continuous Evaluation Loop|Enriched dataset shape|apim-dns-zone-link"
    r"|drop-in module|templates/main\.bicep|account-host module|Basic \*\*project\*\* host module"
    r"|scheduled[- ]query"
)


def test_live_surfaces_do_not_attribute_absent_content_to_official_skills():
    offenders = []
    for rel, text in live_files():
        for number, line in enumerate(text.splitlines(), 1):
            if "microsoft-foundry" in line and NOT_IN_OFFICIAL.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()[:160]}")
    assert not offenders, "\n".join(offenders)


OFFICIAL_URL = re.compile(r"https://github\.com/microsoft/azure-skills/(?:tree|blob)/([^/\s)`'\"]+)/([^\s)`'\"#>]*)")


def test_official_skill_links_are_pinned_and_exist_at_the_pin():
    fixture = json.loads(OFFICIAL_PATHS.read_text(encoding="utf-8"))
    assert fixture["tag"] == PIN_TAG
    known = set(fixture["paths"])
    offenders = []
    for rel, text in live_files():
        if rel.endswith("upstream-pin.md"):
            continue  # freshness trackers intentionally watch the moving branch
        for ref, path in OFFICIAL_URL.findall(text):
            path = path.rstrip(".,;:")
            if ref != PIN_TAG or path not in known:
                offenders.append(f"{rel}: {ref}/{path}")
    assert not offenders, "\n".join(sorted(set(offenders)))


def _descriptions(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield value
            else:
                yield from _descriptions(value)
    elif isinstance(node, list):
        for value in node:
            yield from _descriptions(value)


def test_package_manifests_do_not_route_away_from_shipped_threadlight_skills():
    shipped = {p.name for p in SKILLS.iterdir() if p.name.startswith("threadlight-") and (p / "SKILL.md").exists()}
    offenders = []
    for rel in ("plugin.json", ".github/plugin/marketplace.json"):
        for desc in _descriptions(json.loads((ROOT / rel).read_text(encoding="utf-8"))):
            _, _, excluded = desc.partition("DO NOT USE FOR")
            offenders += [f"{rel}: {name}" for name in sorted(shipped)
                          if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", excluded)]
    assert not offenders, offenders


def test_source_of_truth_pointers_inside_ports_resolve_to_an_existing_section():
    """No dead ends: `../../X.md § Heading` pointers in ported code must name a file
    that exists and a heading that file actually has."""
    pointer = re.compile(r"((?:\.\./)+[\w./-]+\.md)\s*§\s*([^`\n]+?)(?:`|\.\s*$|\s*$)")
    broken = []
    for root in PORTED_REFERENCES.values():
        for path in (SKILLS / root).rglob("*"):
            if path.suffix not in TEXT_SUFFIXES or not path.is_file() or "__pycache__" in path.parts:
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                for m in pointer.finditer(line):
                    target = (path.parent / m.group(1)).resolve()
                    if not target.is_file():
                        broken.append(f"{path.relative_to(ROOT)}: missing {m.group(1)}")
                        continue
                    heading = m.group(2).strip().rstrip(".")
                    headings = [h.lstrip("#").strip().replace("`", "") for h in target.read_text(encoding="utf-8").splitlines()
                                if h.startswith("#")]
                    if not any(h.startswith(heading[:40]) or heading[:40] in h for h in headings):
                        broken.append(f"{path.relative_to(ROOT)}: no heading '{heading}' in {m.group(1)}")
    assert not broken, broken


PORT_TEXT_NAMES = {"Dockerfile"}
PORT_EXTRA_SUFFIXES = {".toml", ".bicepparam", ".env"}


def _port_text_files(root):
    for path in (SKILLS / root).rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.name == "PROVENANCE.md":
            continue
        if path.suffix in TEXT_SUFFIXES | PORT_EXTRA_SUFFIXES or path.name in PORT_TEXT_NAMES:
            yield path


def test_bare_skill_md_pointers_inside_reference_ports_name_an_existing_file():
    """Ported references have a README.md, not a SKILL.md. A bare `SKILL.md §` pointer
    (one not qualified by a path or a skill name) inside them is a dead end."""
    bare = re.compile(r"(?<![\w./`-])(?<!`\s)SKILL\.md`?\s*§")
    broken = []
    for root in PORTED_REFERENCES.values():
        assert not (SKILLS / root / "SKILL.md").exists(), root
        for path in _port_text_files(root):
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if bare.search(line):
                    broken.append(f"{path.relative_to(ROOT)}:{n}")
    assert not broken, broken


def test_relative_pointers_inside_all_ports_resolve():
    """Same as the pointer test above, but across ported skills too and every text
    file type the ports ship (Dockerfile, toml, bicepparam, env)."""
    pointer = re.compile(r"((?:\.\./)+[\w./-]+\.md)")
    broken = []
    for root in PORT_ROOTS:
        for path in _port_text_files(root):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                for m in pointer.finditer(line):
                    if not (path.parent / m.group(1)).resolve().is_file():
                        broken.append(f"{path.relative_to(ROOT)}: missing {m.group(1)}")
    assert not broken, broken


def _github_slug(heading):
    heading = heading.strip().replace("`", "").lower()
    return re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")


def _headings(path):
    return [line.lstrip("#").strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("#")]


def test_live_markdown_anchor_links_resolve():
    link = re.compile(r"\]\(([^)\s#:]+\.md)#([^)\s]+)\)")
    broken = []
    for rel, text in live_files():
        if not rel.endswith(".md"):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in link.finditer(line):
                target = ((ROOT / rel).parent / m.group(1)).resolve()
                if not target.is_file():
                    broken.append(f"{rel}:{n} missing {m.group(1)}")
                elif m.group(2) not in {_github_slug(h) for h in _headings(target)}:
                    broken.append(f"{rel}:{n} no anchor #{m.group(2)} in {m.group(1)}")
    assert not broken, broken


def test_live_prose_pointers_to_ported_references_name_an_existing_section():
    """`threadlight-deploy/references/x` [README.md] § "Heading" in live text must resolve."""
    roots = "|".join(re.escape(r) for r in PORTED_REFERENCES.values())
    pointer = re.compile(rf"`?((?:{roots})(?:/[\w./-]+\.md)?)`?\s*(?:(SKILL|README)\.md\s*)?§\s*"
                         rf"(?:[\"“]([^\"”\n]+)[\"”]|([^\n]+))")
    broken = []
    for rel, text in live_files():
        for n, line in enumerate(text.splitlines(), 1):
            for m in pointer.finditer(line):
                if m.group(2) == "SKILL":
                    broken.append(f"{rel}:{n} {m.group(1)} has no SKILL.md")
                    continue
                target = SKILLS / m.group(1)
                target = target if target.suffix == ".md" else target / "README.md"
                headings = [h.replace("`", "").lower() for h in _headings(target)]
                if m.group(3):  # quoted: the heading must start with it
                    quoted = m.group(3).replace("`", "").lower().strip()
                    ok = any(h.startswith(quoted) for h in headings)
                else:  # unquoted (or a quote wrapped over two lines): prose must open with a heading's start
                    prose = m.group(4).replace("`", "").lower().strip().lstrip("\"“")
                    ok = any(len(os.path.commonprefix([prose, h])) >= min(len(h), len(prose), 7) for h in headings)
                if not ok:
                    broken.append(f"{rel}:{n} no heading for '{(m.group(3) or m.group(4))[:60]}' in {target.relative_to(SKILLS)}")
    assert not broken, broken
