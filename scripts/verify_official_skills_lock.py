#!/usr/bin/env python3
"""Verify skills/_shared/official-skills-lock.json against microsoft/azure-skills.

Online check (network): resolves the pinned tag, then recomputes every tree SHA,
file sha256 and description recorded in the lock. CI runs only the offline
consistency tests in skills/_shared/tests/test_official_skills_lock.py; run this
script by hand before changing the pinned tag.

    python3 scripts/verify_official_skills_lock.py                # shallow clone into a temp dir
    python3 scripts/verify_official_skills_lock.py --clone PATH   # reuse a local clone

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
            front = fetcher.read(commit, f"{sdir}/SKILL.md").decode().split("---")[1]
            if yaml.safe_load(front).get("description") != s.get("description"):
                problems.append(f"{plugin}/{skill}: description differs")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clone", type=Path, help="existing local clone of microsoft/azure-skills")
    ap.add_argument("--lock", type=Path, default=LOCK)
    args = ap.parse_args(argv)
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    if args.clone:
        problems = verify(lock, GitFetcher(args.clone))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", lock["tag"], REMOTE, tmp], check=True)
            problems = verify(lock, GitFetcher(Path(tmp)))
    for p in problems:
        print("MISMATCH", p)
    print("OK" if not problems else f"{len(problems)} mismatch(es)", lock["tag"], lock["commit"])
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
