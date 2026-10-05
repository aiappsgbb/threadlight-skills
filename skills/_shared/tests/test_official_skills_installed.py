"""Installed-copy check for the pinned official skills (no dead ends).

A missing, drifted or shadowed official skill must produce a clear message that
names the fix and the Threadlight fallback, never a silent degrade.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from skills._shared import official_skills

ROOT = Path(__file__).resolve().parents[3]
SHARED = ROOT / "skills" / "_shared"
LOCK = json.loads((SHARED / "official-skills-lock.json").read_text())
MANIFEST = json.loads((SHARED / "skill-dependencies.json").read_text())


def locked():
    for plugin, entry in LOCK["plugins"].items():
        for skill, spec in entry["skills"].items():
            yield plugin, skill, spec


def install(tmp_path, source="plugin", skip=(), mutate=None):
    listing = []
    for plugin, skill, spec in locked():
        if skill in skip:
            continue
        sdir = tmp_path / plugin / skill
        for rel, _ in spec["files"].items():
            f = sdir / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"stub")
        listing.append({"name": skill, "source": source, "path": str(sdir), "enabled": True})
    hashes = {}
    for plugin, skill, spec in locked():
        hashes[skill] = {rel: hashlib.sha256(b"stub").hexdigest() for rel in spec["files"]}
    lock = json.loads(json.dumps(LOCK))
    for plugin, entry in lock["plugins"].items():
        for skill, spec in entry["skills"].items():
            spec["files"] = hashes[skill]
    if mutate:
        mutate(tmp_path, listing)
    return listing, lock


def test_every_locked_official_skill_has_a_documented_fallback():
    fallbacks = MANIFEST["official"]["fallbacks"]
    names = {skill for _, skill, _ in locked()}
    assert set(fallbacks) == names
    for skill, fb in fallbacks.items():
        assert fb["note"].strip(), skill
        for rel in fb["threadlight"]:
            assert (ROOT / rel).exists(), f"{skill}: fallback {rel} missing"


def test_all_pinned_copies_report_ok(tmp_path):
    listing, lock = install(tmp_path)
    report = official_skills.check(listing, lock=lock, manifest=MANIFEST)
    assert report["status"] == "ok"
    assert {r["status"] for r in report["skills"]} == {"ok"}


def test_missing_skill_names_install_command_and_fallback(tmp_path):
    listing, lock = install(tmp_path, skip={"microsoft-foundry"})
    report = official_skills.check(listing, lock=lock, manifest=MANIFEST)
    assert report["status"] == "degraded"
    row = next(r for r in report["skills"] if r["skill"] == "microsoft-foundry")
    assert row["status"] == "missing"
    text = official_skills.render(report)
    assert "copilot plugin install azure@azure-skills" in text
    assert "skills/threadlight-deploy/references/hosted-agent/maf" in text
    assert "v1.2.77" in text


def test_drifted_or_shadowing_copy_is_reported_with_its_path(tmp_path):
    def drift(base, listing):
        row = next(r for r in listing if r["name"] == "microsoft-foundry")
        row["source"] = "inherited"
        (Path(row["path"]) / "SKILL.md").write_bytes(b"older release")

    listing, lock = install(tmp_path, mutate=drift)
    report = official_skills.check(listing, lock=lock, manifest=MANIFEST)
    row = next(r for r in report["skills"] if r["skill"] == "microsoft-foundry")
    assert row["status"] == "drift"
    assert row["source"] == "inherited"
    assert "SKILL.md" in row["mismatched"]
    text = official_skills.render(report)
    assert row["path"] in text and "parity" in text


def test_matching_copy_from_another_source_is_flagged_not_failed(tmp_path):
    listing, lock = install(tmp_path, source="inherited")
    report = official_skills.check(listing, lock=lock, manifest=MANIFEST)
    assert report["status"] == "ok"
    assert {r["status"] for r in report["skills"]} == {"ok-not-plugin"}


def test_disabled_skill_counts_as_missing(tmp_path):
    def disable(base, listing):
        next(r for r in listing if r["name"] == "azure-deploy")["enabled"] = False

    listing, lock = install(tmp_path, mutate=disable)
    report = official_skills.check(listing, lock=lock, manifest=MANIFEST)
    assert next(r for r in report["skills"] if r["skill"] == "azure-deploy")["status"] == "missing"


def test_cli_reads_a_skill_list_file_and_exits_nonzero_when_degraded(tmp_path):
    listing, _ = install(tmp_path, skip={"azure-prepare"})
    f = tmp_path / "skills.json"
    f.write_text(json.dumps(listing))
    proc = subprocess.run(
        [sys.executable, str(SHARED / "official_skills.py"), "--skill-list", str(f), "--json"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["status"] == "degraded"
    assert any(r["skill"] == "azure-prepare" and r["status"] == "missing" for r in out["skills"])


def test_auto_preflight_runs_the_check_and_records_it():
    text = (ROOT / "skills/threadlight-auto/SKILL.md").read_text()
    assert "_shared/official_skills.py" in text
    assert "official_skills" in text and "preflight-passed.json" in text
