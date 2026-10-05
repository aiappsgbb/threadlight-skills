"""Offline contract for skills/_shared/official-skills-lock.json.

The lock pins microsoft/azure-skills by tag, commit, tree SHA and per-file sha256.
CI checks only that the lock is consistent with skill-dependencies.json and that
Threadlight skill names and descriptions do not collide with, or silently shadow,
official or awesome-gbb skills. scripts/verify_official_skills_lock.py does the
online recomputation. The routing probe is static description evidence, not
model routing evidence.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
SHARED = ROOT / "skills" / "_shared"
LOCK = SHARED / "official-skills-lock.json"
MANIFEST = SHARED / "skill-dependencies.json"
PROBE = Path(__file__).parent / "fixtures" / "routing-probe.json"
EXCLUSION = re.compile(r"(DO NOT USE (?:FOR|WHEN)|DO NOT use this skill|NOT:|Not for)", re.I)
INCLUSION = re.compile(r"(USE FOR:|WHEN:)")


def load(path):
    assert path.exists(), f"{path.relative_to(ROOT)} is missing"
    return json.loads(path.read_text(encoding="utf-8"))


def threadlight_skills():
    out = {}
    for skill_md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        front = yaml.safe_load(skill_md.read_text(encoding="utf-8").split("---")[1])
        out[skill_md.parent.name] = front
    return out


def split_claims(description):
    """Return (claim_text, exclusion_text): exclusion segments run from an exclusion
    marker to the next USE FOR:/WHEN: marker or the end."""
    claim, excl, pos, excluding = [], [], 0, False
    excl_spans = [m.span() for m in EXCLUSION.finditer(description)]
    incl = [m.start() for m in INCLUSION.finditer(description)
            if not any(a <= m.start() < b for a, b in excl_spans)]
    markers = sorted([(a, "x") for a, _ in excl_spans] + [(s, "i") for s in incl])
    for start, kind in markers:
        (excl if excluding else claim).append(description[pos:start])
        pos, excluding = start, kind == "x"
    (excl if excluding else claim).append(description[pos:])
    return " ".join(claim), " ".join(excl)


def claims(description, trigger):
    return trigger.lower() in split_claims(description)[0].lower()


def all_descriptions():
    lock = load(LOCK)
    descs = {name: front["description"] for name, front in threadlight_skills().items()}
    for plugin in lock["plugins"].values():
        for skill, entry in plugin["skills"].items():
            descs[skill] = entry["description"]
    return descs


def test_lock_matches_manifest_tag_and_commit():
    lock, manifest = load(LOCK), load(MANIFEST)
    assert lock["schema"] == "threadlight-official-skills-lock/v1"
    assert lock["repository"] == "microsoft/azure-skills"
    assert (lock["tag"], lock["commit"]) == (manifest["official"]["tag"], manifest["official"]["commit"])
    for plugin in lock["plugins"].values():
        assert re.fullmatch(r"[0-9a-f]{40}", plugin["tree"]) and plugin["version"]
        for skill, entry in plugin["skills"].items():
            assert re.fullmatch(r"[0-9a-f]{40}", entry["tree"]), skill
            assert "SKILL.md" in entry["files"] and entry["description"], skill
            assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in entry["files"].values()), skill


def test_lock_covers_every_official_reference_in_the_manifest():
    lock, manifest = load(LOCK), load(MANIFEST)
    missing = []
    for name, entry in manifest["skills"].items():
        for target in entry.get("official", []) + entry.get("official_partial", []):
            locked = lock["plugins"].get(target["plugin"], {}).get("skills", {}).get(target["skill"])
            if not locked:
                missing.append(f"{name}: {target['plugin']}/{target['skill']}")
                continue
            for rel in target.get("paths", []):
                if rel not in locked["files"]:
                    missing.append(f"{name}: {target['skill']}/{rel}")
    assert not missing, missing


def test_lock_lists_the_whole_official_catalog():
    names = {n for skills in load(LOCK)["catalog_skill_names"].values() for n in skills}
    for plugin in load(LOCK)["plugins"].values():
        assert set(plugin["skills"]) <= names
    assert {"microsoft-foundry", "azure-prepare", "azure-deploy", "azure-validate", "foundry-iq"} <= names


def test_threadlight_skill_names_do_not_collide_or_shadow():
    official = {n for skills in load(LOCK)["catalog_skill_names"].values() for n in skills}
    custom = set(load(MANIFEST)["custom_catalog"]["skills"])
    for directory, front in threadlight_skills().items():
        assert front["name"] == directory, f"{directory}: frontmatter name {front['name']!r} differs"
        assert directory.startswith("threadlight-"), f"{directory}: Threadlight skills use the threadlight- prefix"
        assert directory not in official, f"{directory} shadows an official azure-skills skill"
        assert directory not in custom, f"{directory} shadows an awesome-gbb skill"


def test_descriptions_route_to_official_skills_not_replaced_custom_ones():
    manifest = load(MANIFEST)
    official = {n for skills in load(LOCK)["catalog_skill_names"].values() for n in skills}
    replaced = {n for n, e in manifest["skills"].items() if e["label"] == "official-default" and n not in official}
    offenders = {}
    for name, front in threadlight_skills().items():
        hits = sorted(n for n in replaced if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", front["description"]))
        if hits:
            offenders[name] = hits
    assert not offenders, offenders


def test_split_claims_drops_exclusion_tails():
    claim, excl = split_claims("Does X. USE FOR: alpha, beta. DO NOT USE FOR: gamma (use other). WHEN: delta.")
    assert "alpha" in claim and "delta" in claim and "gamma" not in claim and "gamma" in excl


def test_static_routing_probe():
    probe, descs = load(PROBE), all_descriptions()
    assert probe["evidence_kind"] == "static"
    tl = set(threadlight_skills())
    failures = []
    for p in probe["probes"]:
        owner = p["owner"]
        assert owner in descs, f"probe owner {owner} not found"
        scores = {name: sum(claims(d, t) for t in p["triggers"]) for name, d in descs.items()}
        best = scores[owner]
        rivals = sorted(n for n, s in scores.items() if n != owner and s >= best)
        if best == 0 or rivals:
            failures.append(f"{p['prompt']!r}: {owner}={best}, rivals {rivals}")
        if owner not in tl:
            for name in sorted(tl):
                if scores[name] and owner not in split_claims(descs[name])[1]:
                    failures.append(f"{p['prompt']!r}: {name} claims it without routing to {owner} in DO NOT USE FOR")
    assert not failures, failures


def test_verify_script_detects_drift_with_an_injected_fetcher():
    spec = importlib.util.spec_from_file_location("verify_lock", ROOT / "scripts" / "verify_official_skills_lock.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    body = b"---\ndescription: d\n---\n"
    lock = {"tag": "v1", "commit": "c" * 40, "plugins": {"p": {"root": "r", "tree": "t0", "skills": {
        "s": {"tree": "t1", "description": "d", "files": {"SKILL.md": hashlib.sha256(body).hexdigest()}}}}}}

    class Fake:
        def __init__(self, data, commit="c" * 40):
            self.data, self.commit = data, commit

        def resolve(self, tag):
            return self.commit

        def tree(self, commit, path):
            return {"r": "t0", "r/skills/s": "t1"}[path]

        def read(self, commit, path):
            return self.data

    assert mod.verify(lock, Fake(body)) == []
    assert any("sha256" in p for p in mod.verify(lock, Fake(body + b"x")))
    assert mod.verify(lock, Fake(body, commit="d" * 40))[0].startswith("tag v1 resolves")


def test_manifest_records_upstream_todos_as_local_drafts_only():
    manifest = load(MANIFEST)
    todos = {n: e["upstream_todo"] for n, e in manifest["skills"].items() if "upstream_todo" in e}
    assert {"foundry-hosted-agents", "foundry-mcp-aca", "citadel-hub-deploy", "foundry-teams-bot"} <= set(todos)
    for name, todo in todos.items():
        assert todo["draft"] and todo["target"], name
        assert "not filed" in todo["status"], name
