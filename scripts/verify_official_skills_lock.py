#!/usr/bin/env python3
"""Verify skills/_shared/official-skills-lock.json against microsoft/azure-skills.

Online check (network): resolves the pinned tag, then recomputes every tree SHA,
file sha256 and description recorded in the lock. CI runs only the offline
consistency tests in skills/_shared/tests/test_official_skills_lock.py; run this
script by hand before changing the pinned tag.

    python3 scripts/verify_official_skills_lock.py                # shallow clone into a temp dir
    python3 scripts/verify_official_skills_lock.py --clone PATH   # reuse a local clone
    python3 scripts/verify_official_skills_lock.py --clone PATH --write
        # regenerate the lock at skill-dependencies.json official.tag from the fetched
        # content (never hand-edit hashes), then verify the written file

Exit 0 when the lock matches, 1 with a list of mismatches otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "skills" / "_shared" / "official-skills-lock.json"
MANIFEST = ROOT / "skills" / "_shared" / "skill-dependencies.json"
PLUGINS = ".github/plugins"
REMOTE = "https://github.com/microsoft/azure-skills.git"


class GitFetcher:
    """Reads objects from a local git clone. Any object with the same methods can be injected."""

    def __init__(self, clone: Path):
        self.clone = Path(clone)

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.clone), *args], check=True, capture_output=True).stdout.decode()

    def resolve(self, tag: str) -> str:
        return self._git("rev-parse", f"{tag}^{{commit}}").strip()

    def tree(self, commit: str, path: str) -> str:
        return self._git("rev-parse", f"{commit}:{path}").strip()

    def read(self, commit: str, path: str) -> bytes:
        return subprocess.run(["git", "-C", str(self.clone), "show", f"{commit}:{path}"], check=True, capture_output=True).stdout

    def ls(self, commit: str, path: str) -> list[str]:
        return [line.rsplit("/", 1)[-1] for line in self._git("ls-tree", "--name-only", f"{commit}:{path}").splitlines()
                if self._git("cat-file", "-t", f"{commit}:{path}/{line.rsplit('/', 1)[-1]}").strip() == "tree"]


def _description(raw: bytes) -> str:
    return yaml.safe_load(raw.decode().split("---")[1]).get("description")


def build_lock(manifest: dict, template: dict, fetcher) -> dict:
    """Regenerate the lock at manifest official.tag from fetched objects only.

    The locked skill set and extra files come from the manifest's official and
    official_partial references; every tree, sha256, description, version and
    catalog name is read from the fetched release.
    """
    tag = manifest["official"]["tag"]
    commit = fetcher.resolve(tag)
    roots, catalog = {}, {}
    for directory in sorted(fetcher.ls(commit, PLUGINS)):
        root = f"{PLUGINS}/{directory}"
        meta = json.loads(fetcher.read(commit, f"{root}/.claude-plugin/plugin.json"))
        roots[meta["name"]] = (root, meta["version"])
        catalog[directory] = sorted(fetcher.ls(commit, f"{root}/skills"))
    wanted: dict[str, dict[str, set[str]]] = {}
    for entry in manifest["skills"].values():
        for target in entry.get("official", []) + entry.get("official_partial", []):
            files = wanted.setdefault(target["plugin"], {}).setdefault(target["skill"], {"SKILL.md"})
            files.update(target.get("paths", []))
    plugins = {}
    for plugin in sorted(wanted):
        root, version = roots[plugin.split("@", 1)[0]]
        skills = {}
        for skill in sorted(wanted[plugin]):
            sdir = f"{root}/skills/{skill}"
            skills[skill] = {
                "tree": fetcher.tree(commit, sdir),
                "files": {rel: hashlib.sha256(fetcher.read(commit, f"{sdir}/{rel}")).hexdigest()
                          for rel in sorted(wanted[plugin][skill])},
                "description": _description(fetcher.read(commit, f"{sdir}/SKILL.md")),
            }
        plugins[plugin] = {"root": root, "tree": fetcher.tree(commit, root), "version": version, "skills": skills}
    return {**{k: template[k] for k in ("schema", "repository", "marketplace")},
            "tag": tag, "commit": commit, "note": template["note"],
            "plugins": plugins, "catalog_skill_names": catalog}


def render_lock(lock: dict) -> str:
    return json.dumps(lock, indent=2) + "\n"


def verify(lock: dict, fetcher) -> list[str]:
    problems: list[str] = []
    commit = lock["commit"]
    resolved = fetcher.resolve(lock["tag"])
    if resolved != commit:
        return [f"tag {lock['tag']} resolves to {resolved}, lock says {commit}"]
    for plugin, entry in sorted(lock["plugins"].items()):
        root = entry["root"]
        if fetcher.tree(commit, root) != entry["tree"]:
            problems.append(f"{plugin}: plugin tree differs")
        for skill, s in sorted(entry["skills"].items()):
            sdir = f"{root}/skills/{skill}"
            if fetcher.tree(commit, sdir) != s["tree"]:
                problems.append(f"{plugin}/{skill}: skill tree differs")
            for rel, digest in sorted(s["files"].items()):
                actual = hashlib.sha256(fetcher.read(commit, f"{sdir}/{rel}")).hexdigest()
                if actual != digest:
                    problems.append(f"{plugin}/{skill}/{rel}: sha256 {actual} != {digest}")
            if _description(fetcher.read(commit, f"{sdir}/SKILL.md")) != s.get("description"):
                problems.append(f"{plugin}/{skill}: description differs")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clone", type=Path, help="existing local clone of microsoft/azure-skills")
    ap.add_argument("--lock", type=Path, default=LOCK)
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--write", action="store_true",
                    help="regenerate the lock at the manifest official.tag before verifying")
    args = ap.parse_args(argv)
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    tag = manifest["official"]["tag"] if args.write else lock["tag"]

    def run(fetcher):
        nonlocal lock
        if args.write:
            lock = build_lock(manifest, lock, fetcher)
            args.lock.write_text(render_lock(lock), encoding="utf-8")
        return verify(lock, fetcher)

    if args.clone:
        problems = run(GitFetcher(args.clone))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", tag, REMOTE, tmp], check=True)
            problems = run(GitFetcher(Path(tmp)))
    for p in problems:
        print("MISMATCH", p)
    print("OK" if not problems else f"{len(problems)} mismatch(es)", lock["tag"], lock["commit"])
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
