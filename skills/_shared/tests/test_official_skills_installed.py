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
    assert "v1.2.79" in text


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


@pytest.mark.parametrize("failure", ["missing-binary", "nonzero", "bad-json"])
def test_unavailable_copilot_cli_reports_unchecked_not_degraded(tmp_path, failure):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if failure != "missing-binary":
        fake = bindir / "copilot"
        body = "echo 'unknown command' >&2; exit 3" if failure == "nonzero" else "echo 'not json'"
        fake.write_text(f"#!/bin/sh\n{body}\n")
        fake.chmod(0o755)
    env = {"PATH": str(bindir), "HOME": str(tmp_path)}
    for args in ([], ["--json"]):
        proc = subprocess.run(
            [sys.executable, str(SHARED / "official_skills.py"), *args],
            capture_output=True, text=True, cwd=ROOT, env=env,
        )
        assert proc.returncode == 2, proc.stderr
        assert "Traceback" not in proc.stderr
        if args:
            assert json.loads(proc.stdout)["status"] == "unchecked"
        else:
            assert proc.stdout.startswith("UNCHECKED:")


def test_auto_preflight_runs_the_check_and_records_it():
    text = (ROOT / "skills/threadlight-auto/SKILL.md").read_text()
    assert "_shared/official_skills.py" in text
    assert "official_skills" in text and "preflight-passed.json" in text
    assert "exits 2" in text and '"unchecked"' in text


def _fake_copilot(tmp_path, listing):
    """A fake `copilot` that records argv and prints `listing` only when invoked
    as `copilot [--plugin-dir D]... skill list --json`."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    argv_log = tmp_path / "argv.txt"
    out = tmp_path / "listing.json"
    out.write_text(json.dumps(listing))
    fake = bindir / "copilot"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$@\" > '{argv_log}'\n"
        "last3=$(echo \"$@\" | awk '{print $(NF-2), $(NF-1), $NF}')\n"
        "[ \"$last3\" = 'skill list --json' ] || { echo 'unexpected argument' >&2; exit 2; }\n"
        f"cat '{out}'\n"
    )
    fake.chmod(0o755)
    return bindir, argv_log


def test_plugin_dirs_are_passed_to_the_copilot_listing_before_the_subcommand(tmp_path):
    # Copilot CLI only lists --plugin-dir skills when the flag precedes the
    # `skill list` subcommand; omitting it produced false MISSING/DRIFT in live2.
    listing, _ = install(tmp_path)
    bindir, argv_log = _fake_copilot(tmp_path, listing)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, str(SHARED / "official_skills.py"), "--json",
         "--plugin-dir", "/p/azure-skills", "--plugin-dir", "/p/azure-cost"],
        capture_output=True, text=True, cwd=ROOT, env=env,
    )
    assert proc.returncode in (0, 1), proc.stderr
    assert argv_log.read_text().split() == [
        "--plugin-dir", "/p/azure-skills", "--plugin-dir", "/p/azure-cost", "skill", "list", "--json"]


def test_plugin_dirs_env_var_is_honoured(tmp_path):
    import os
    listing, _ = install(tmp_path)
    bindir, argv_log = _fake_copilot(tmp_path, listing)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path),
           "THREADLIGHT_PLUGIN_DIRS": os.pathsep.join(["/p/a", "/p/b"])}
    proc = subprocess.run(
        [sys.executable, str(SHARED / "official_skills.py"), "--json"],
        capture_output=True, text=True, cwd=ROOT, env=env,
    )
    assert proc.returncode in (0, 1), proc.stderr
    assert argv_log.read_text().split() == ["--plugin-dir", "/p/a", "--plugin-dir", "/p/b", "skill", "list", "--json"]


def test_auto_preflight_forwards_plugin_dirs():
    text = (ROOT / "skills/threadlight-auto/SKILL.md").read_text()
    assert "THREADLIGHT_PLUGIN_DIRS" in text and "--plugin-dir" in text


def test_namespaced_skill_names_from_colliding_plugins_resolve(tmp_path):
    # When two plugins ship the same skill name, `copilot skill list` reports
    # `<plugin>:<skill>`; the pinned plugin's copy must still resolve.
    def namespace(_, listing):
        for row in listing:
            if row["name"] == "foundry-iq":
                row["name"] = "foundry-iq-skills:foundry-iq"
                listing.append({"name": "awesome-gbb:foundry-iq", "source": "plugin",
                                "path": "/elsewhere/foundry-iq", "enabled": True})
                break
    listing, lock = install(tmp_path, mutate=namespace)
    report = official_skills.check(listing, lock=lock, manifest=MANIFEST)
    row = next(r for r in report["skills"] if r["skill"] == "foundry-iq")
    assert row["status"] == "ok", row


def _with_threadlight(listing, tmp_path, first):
    tl = [{"name": f"threadlight-{n}", "source": "plugin", "enabled": True,
           "path": str(tmp_path / "tl" / n)} for n in ("auto", "deploy", "design")]
    return tl + listing if first else listing + tl


def test_threadlight_installed_after_official_is_reported_as_order_risk(tmp_path):
    # Copilot CLI 1.0.91 describes plugin skills in install order up to a
    # prompt budget; official-first left every threadlight-* skill name-only
    # and routed "deploya il pilota" to azure-deploy (Phase 2 baseline 88%).
    listing, lock = install(tmp_path)
    report = official_skills.check(_with_threadlight(listing, tmp_path, first=False), lock=lock, manifest=MANIFEST)
    assert report["routing_order"] == "official-first"
    assert report["status"] == "degraded"
    text = official_skills.render(report)
    assert "ORDER" in text
    assert "copilot plugin uninstall azure@azure-skills" in text
    assert text.index("copilot plugin install threadlight-skills@threadlight-skills") < text.index(
        "copilot plugin install azure@azure-skills")


def test_threadlight_installed_first_is_ok(tmp_path):
    listing, lock = install(tmp_path)
    report = official_skills.check(_with_threadlight(listing, tmp_path, first=True), lock=lock, manifest=MANIFEST)
    assert report["routing_order"] == "threadlight-first"
    assert report["status"] == "ok"
    assert "ORDER" not in official_skills.render(report)


def test_order_is_unknown_without_threadlight_plugin_skills(tmp_path):
    listing, lock = install(tmp_path)
    report = official_skills.check(listing, lock=lock, manifest=MANIFEST)
    assert report["routing_order"] == "unknown"
    assert report["status"] == "ok"


def test_readme_companion_install_order_puts_threadlight_first():
    text = (ROOT / "README.md").read_text()
    block = text[text.index("### Companion skills: official first"):]
    block = block[:block.index("## Live experience")]
    assert block.index("copilot plugin install threadlight-skills@threadlight-skills") < block.index(
        "copilot plugin install azure@azure-skills")
    assert "copilot plugin uninstall azure@azure-skills" in block


def _crowding_plugin(tmp_path, count, chars=900):
    return [{"name": f"other-{i}", "description": "x" * chars, "source": "plugin", "enabled": True,
             "path": str(tmp_path / "installed-plugins" / "other-mkt" / "other-plugin" / "skills" / f"other-{i}")}
            for i in range(count)]


def test_other_plugins_installed_before_threadlight_are_reported_even_without_official(tmp_path):
    # Real machine (env B): personal skills plus workiq/m365/awesome-gbb plugins
    # used the whole description budget, so every threadlight-* skill was
    # name-only although no official plugin was installed (code review on #155).
    official, lock = install(tmp_path)
    listing = _crowding_plugin(tmp_path, 20) + _with_threadlight(official, tmp_path, first=True)
    report = official_skills.check(listing, lock=lock, manifest=MANIFEST)
    assert report["routing_order"] == "threadlight-first"
    assert "threadlight-auto" in report["routing_name_only"]
    assert report["status"] == "degraded"
    text = official_skills.render(report)
    assert "ORDER" in text and "other-plugin" in text


def test_small_skill_sets_before_threadlight_keep_routing_ok(tmp_path):
    listing, lock = install(tmp_path)
    listing = _crowding_plugin(tmp_path, 3) + _with_threadlight(listing, tmp_path, first=True)
    report = official_skills.check(listing, lock=lock, manifest=MANIFEST)
    assert report["routing_name_only"] == []
    assert report["status"] == "ok"


def test_official_named_skill_from_another_marketplace_is_not_official_first(tmp_path):
    # awesome-gbb ships its own foundry-iq; it must not be reported as the
    # official azure-skills plugin being installed first.
    listing, lock = install(tmp_path)
    gbb = [{"name": "foundry-iq", "source": "plugin", "enabled": True,
            "path": str(tmp_path / "installed-plugins" / "_direct" / "aiappsgbb--awesome-gbb" / "skills" / "foundry-iq")}]
    report = official_skills.check(gbb + _with_threadlight(listing, tmp_path, first=True), lock=lock, manifest=MANIFEST)
    assert report["routing_order"] == "threadlight-first"


def test_direct_installed_plugins_are_labelled_by_name_and_github_source(tmp_path):
    # GitHub-direct installs live under installed-plugins/_direct/<owner>--<repo>;
    # "<dir>@_direct" is not a valid install spec (code review on #155).
    root = tmp_path / "installed-plugins" / "_direct" / "aiappsgbb--awesome-gbb"
    (root / "skills").mkdir(parents=True)
    (root / "plugin.json").write_text('{"name": "awesome-gbb"}')
    official, lock = install(tmp_path)
    crowd = [{"name": f"gbb-{i}", "description": "x" * 900, "source": "plugin", "enabled": True,
              "path": str(root / "skills" / f"gbb-{i}")} for i in range(20)]
    report = official_skills.check(crowd + _with_threadlight(official, tmp_path, first=True), lock=lock, manifest=MANIFEST)
    assert report["routing_ahead"] == ["awesome-gbb (aiappsgbb/awesome-gbb)"]
    text = official_skills.render(report)
    assert "@_direct" not in text
    assert "copilot plugin install aiappsgbb/awesome-gbb" in text
