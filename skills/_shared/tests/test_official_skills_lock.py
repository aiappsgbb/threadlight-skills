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
import types
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


def test_routing_exclusions_keep_every_description_loadable():
    """Longer DO NOT USE FOR lists must not push a description past the 1024-char
    loader limit, or the Copilot CLI silently drops the whole skill."""
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci/check-skill-description-length.py")],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def _verify_module():
    spec = importlib.util.spec_from_file_location("verify_lock", ROOT / "scripts" / "verify_official_skills_lock.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lock_pins_the_current_marketplace_release():
    lock = load(LOCK)
    assert (lock["tag"], lock["commit"]) == ("v1.2.79", "344bd5b3e041445237de09007ef62f4b3c51805d")
    versions = {name: plugin["version"] for name, plugin in lock["plugins"].items()}
    assert versions == {"azure@azure-skills": "1.2.79", "azure-cost@azure-skills": "1.0.5",
                        "foundry-iq-skills@azure-skills": "0.1.10"}


def test_build_lock_regenerates_from_fetched_content_not_hand_edits():
    """The generator derives every hash from fetched bytes and the locked set from the manifest."""
    mod = _verify_module()
    skill_md = b"---\nname: s\ndescription: official d\n---\nbody\n"
    extra = b"guide"
    base = ".github/plugins"
    files = {
        f"{base}/R/.claude-plugin/plugin.json": b'{"name": "p", "version": "9.9.9"}',
        f"{base}/R/skills/s/SKILL.md": skill_md,
        f"{base}/R/skills/s/ref/guide.md": extra,
        f"{base}/Q/.claude-plugin/plugin.json": b'{"name": "q", "version": "1.0.0"}',
    }
    listing = {base: ["R", "Q"], f"{base}/R/skills": ["s", "t"], f"{base}/Q/skills": ["u"]}

    class Fake:
        def resolve(self, tag):
            return "c" * 40

        def tree(self, commit, path):
            return hashlib.sha1(path.encode()).hexdigest()

        def read(self, commit, path):
            return files[path]

        def ls(self, commit, path):
            return listing[path]

    manifest = {"official": {"tag": "v9"}, "skills": {"x": {
        "official": [{"plugin": "p@m", "skill": "s"}],
        "official_partial": [{"plugin": "p@m", "skill": "s", "paths": ["ref/guide.md"]}]}}}
    template = {"schema": "threadlight-official-skills-lock/v1", "repository": "o/r", "marketplace": "m", "note": "n"}
    fetcher = Fake()
    lock = mod.build_lock(manifest, template, fetcher)
    assert (lock["tag"], lock["commit"]) == ("v9", "c" * 40)
    entry = lock["plugins"]["p@m"]
    assert entry["root"] == ".github/plugins/R" and entry["version"] == "9.9.9"
    s = entry["skills"]["s"]
    assert s["files"] == {"SKILL.md": hashlib.sha256(skill_md).hexdigest(),
                          "ref/guide.md": hashlib.sha256(extra).hexdigest()}
    assert s["description"] == "official d"
    assert lock["catalog_skill_names"] == {"Q": ["u"], "R": ["s", "t"]}
    assert mod.verify(lock, fetcher) == []


def test_committed_lock_is_exactly_what_the_generator_emits_from_its_own_data(monkeypatch):
    """Offline round trip: a fetcher serving only the committed lock's recorded
    objects, driven by build_lock from the manifest, must reproduce the file
    byte-for-byte. The plugin/skill/file set therefore follows the manifest and
    the layout follows the generator, not a hand edit."""
    mod = _verify_module()
    lock = load(LOCK)
    manifest = load(MANIFEST)
    hashes, descriptions, trees, metas = {}, {}, {}, {}
    for plugin, entry in lock["plugins"].items():
        root = entry["root"]
        trees[root] = entry["tree"]
        metas[root] = {"name": plugin.split("@", 1)[0], "version": entry["version"]}
        for skill, s in entry["skills"].items():
            sdir = f"{root}/skills/{skill}"
            trees[sdir] = s["tree"]
            descriptions[f"{sdir}/SKILL.md"] = s["description"]
            for rel, digest in s["files"].items():
                hashes[f"{sdir}/{rel}"] = digest

    class Digest:
        def __init__(self, data):
            self.path = data.decode()

        def hexdigest(self):
            return hashes[self.path]

    class Fake:
        def resolve(self, tag):
            assert tag == lock["tag"]
            return lock["commit"]

        def tree(self, commit, path):
            return trees[path]

        def read(self, commit, path):
            if path.endswith("/.claude-plugin/plugin.json"):
                root = path[: -len("/.claude-plugin/plugin.json")]
                return json.dumps(metas.get(root, {"name": root, "version": "0"})).encode()
            return path.encode()

        def ls(self, commit, path):
            if path == mod.PLUGINS:
                return list(lock["catalog_skill_names"])
            return lock["catalog_skill_names"][path.rsplit("/", 2)[-2]]

    monkeypatch.setattr(mod, "hashlib", types.SimpleNamespace(sha256=Digest))
    monkeypatch.setattr(mod, "_description", lambda raw: descriptions[raw.decode()])
    rebuilt = mod.build_lock(manifest, lock, Fake())
    assert LOCK.read_text(encoding="utf-8") == mod.render_lock(rebuilt)
