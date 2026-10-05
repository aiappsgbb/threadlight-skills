"""Offline contract for skills/_shared/skill-dependencies.json.

Official Microsoft skills (azure@azure-skills) are the default. Capabilities with
no official equivalent are ported into threadlight-skills as threadlight-owned
skills or references; other awesome-gbb skills stay labelled residual with
recorded evidence.
These checks prove the manifest's shape and labels, not skill runtime behaviour.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "skills" / "_shared" / "skill-dependencies.json"
LABELS = {"official-default", "threadlight-owned", "residual", "out-of-product-scope"}
OFFICIAL_PLUGINS = {"azure@azure-skills", "azure-cost@azure-skills", "foundry-iq-skills@azure-skills"}
RESIDUAL_LABEL = "GBB pattern, not a Microsoft product skill"
AWESOME_GBB_SKILL_LINK = re.compile(r"awesome-gbb/(?:tree|blob)/[^/\s)\]]+/skills/([a-z0-9]+(?:-[a-z0-9]+)*)")


def uncovered_awesome_gbb_links(text, covered):
    return {m.group(1) for m in AWESOME_GBB_SKILL_LINK.finditer(text)} - set(covered)


def load():
    assert MANIFEST.exists(), "skills/_shared/skill-dependencies.json is missing"
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def tracked_text_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    for rel in out.splitlines():
        path = ROOT / rel
        if path == MANIFEST or path.suffix not in {".md", ".py", ".json", ".yml", ".yaml", ".js", ".html", ".txt"}:
            continue
        try:
            yield rel, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue


def test_policy_and_official_source_are_declared():
    data = load()
    assert data["schema"] == "threadlight-skill-dependencies/v1"
    official = data["official"]
    assert official["marketplace"] == "microsoft/azure-skills"
    assert official["default_plugin"] == "azure@azure-skills"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", official["tag"])
    assert re.fullmatch(r"[0-9a-f]{40}", official["commit"])
    assert "default" in data["policy"].lower()


def test_manifest_covers_the_whole_checked_custom_catalog():
    data = load()
    catalog = data["custom_catalog"]
    assert catalog["repository"] == "aiappsgbb/awesome-gbb"
    assert re.fullmatch(r"[0-9a-f]{40}", catalog["commit_checked"])
    names = catalog["skills"]
    assert len(names) == len(set(names)) >= 40
    assert sorted(data["skills"]) == sorted(names)


def test_every_entry_has_a_valid_label_and_evidence():
    data = load()
    tag = data["official"]["tag"]
    for name, entry in data["skills"].items():
        label = entry.get("label")
        assert label in LABELS, f"{name}: unknown label {label!r}"
        if label == "official-default":
            targets = entry.get("official")
            assert targets, f"{name}: official-default needs an official target"
            for target in targets:
                assert target["plugin"] in OFFICIAL_PLUGINS, f"{name}: {target}"
                assert target["skill"], f"{name}: official target needs a skill"
            assert entry.get("coverage") in {"full", "partial"}, name
            if entry["coverage"] == "partial":
                assert entry.get("threadlight_owned_gap"), f"{name}: partial coverage needs the owned gap"
        elif label in {"residual", "threadlight-owned"}:
            exc = entry.get("exception") or {}
            assert exc.get("reason"), f"{name}: residual needs exception.reason"
            assert exc.get("official_tag_checked") == tag, f"{name}: exception must cite {tag}"
            assert exc.get("observation"), f"{name}: residual needs a test or observation"
            if label == "residual":
                assert entry.get("display_label") == RESIDUAL_LABEL, name
            else:
                assert (ROOT / entry["threadlight_target"]).is_dir(), f"{name}: missing port target"
                assert entry.get("provenance", "").startswith("awesome-gbb@"), name
        else:
            assert entry.get("reason"), f"{name}: out-of-product-scope needs a reason"


def test_expected_gaps_are_threadlight_owned_or_residual():
    data = load()
    for name in ("citadel-hub-deploy", "citadel-spoke-onboarding", "foundry-agt", "azure-tenant-isolation",
                 "foundry-mcp-aca", "ghcp-hosted-agents"):
        assert data["skills"][name]["label"] == "threadlight-owned", name
    assert data["skills"]["foundry-teams-bot"]["label"] == "residual"


def test_heavily_used_platform_skills_default_to_official():
    data = load()
    for name in ("foundry-hosted-agents", "foundry-evals", "foundry-observability", "azd-patterns"):
        assert data["skills"][name]["label"] == "official-default", name


def test_uncovered_awesome_gbb_links_detects_an_uncatalogued_skill():
    data = load()
    text = (
        "see https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-evals and "
        "https://github.com/aiappsgbb/awesome-gbb/blob/abc123/skills/" + "foundry-new-thing/SKILL.md"
    )
    assert uncovered_awesome_gbb_links(text, data["skills"]) == {"foundry-new-thing"}


def test_every_linked_awesome_gbb_skill_is_in_the_manifest():
    data = load()
    missing = {}
    for path, text in tracked_text_files():
        for name in uncovered_awesome_gbb_links(text, data["skills"]):
            missing.setdefault(name, []).append(path)
    assert not missing, missing


def test_plugin_and_readme_state_the_official_default():
    plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert "azure@azure-skills" in plugin["description"]
    assert "use awesome-gbb" not in plugin["description"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "skills/_shared/skill-dependencies.json" in readme
    assert "azure@azure-skills" in readme
