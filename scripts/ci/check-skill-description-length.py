#!/usr/bin/env python3
"""Fail-fast gate: assert every skills/<name>/SKILL.md has a frontmatter
`description` within the agentskills.io 1024-character limit.

Why this exists
---------------
The Copilot CLI skill loader silently DROPS any skill whose parsed YAML
`description` exceeds 1024 characters — the skill never appears in the
Skill-tool registry, so the agent falls back to reading SKILL.md by hand
and the failure looks like "skill not found". This regressed on 7 skills
at once (cicd, consumption-iq, customize, deploy, evals, govern,
production-ready) because the only guard for it
(scripts/ci/skill-discovery-smoke.sh) runs solely on the manual-dispatch
e2e workflow, never on regular PRs.

This check is the cheap, deterministic, network-free per-PR counterpart:
~50ms, no Copilot CLI, no Azure. The smoke test stays as the heavier
end-to-end confirmation.

Usage:
    python scripts/ci/check-skill-description-length.py
Exit code 0 if all descriptions are <= the limit, 1 otherwise.
"""
from __future__ import annotations

import glob
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "::error::PyYAML is required (pip install pyyaml) to run "
        "check-skill-description-length.py\n"
    )
    sys.exit(2)

# The loader's hard cap on the parsed `description` scalar. Skills at or
# under this load; anything larger is silently dropped from the registry.
MAX_DESCRIPTION_CHARS = 1024


def repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def parsed_description(skill_md_path: str) -> str:
    text = open(skill_md_path, encoding="utf-8").read()
    if not text.startswith("---"):
        raise ValueError(f"{skill_md_path}: missing YAML frontmatter")
    # Frontmatter is the block between the first two '---' fences.
    frontmatter = text.split("---", 2)[1]
    data = yaml.safe_load(frontmatter) or {}
    return data.get("description", "") or ""


def main() -> int:
    root = repo_root()
    # Only top-level skills are registered by the loader; nested fixture
    # SKILL.md files under references/ are not, so they are excluded.
    skill_files = sorted(glob.glob(os.path.join(root, "skills", "*", "SKILL.md")))
    if not skill_files:
        sys.stderr.write("::error::No skills/*/SKILL.md files found\n")
        return 1

    failures: list[tuple[str, int]] = []
    for path in skill_files:
        name = os.path.basename(os.path.dirname(path))
        length = len(parsed_description(path))
        status = "OK" if length <= MAX_DESCRIPTION_CHARS else "OVER"
        print(f"  [{status:>4}] {length:>5}/{MAX_DESCRIPTION_CHARS}  {name}")
        if length > MAX_DESCRIPTION_CHARS:
            failures.append((name, length))

    print(f"Checked {len(skill_files)} skill(s); limit {MAX_DESCRIPTION_CHARS} chars.")
    if failures:
        sys.stderr.write(
            "::error::Skill description(s) exceed the "
            f"{MAX_DESCRIPTION_CHARS}-char load limit and will be SILENTLY "
            "DROPPED from the Copilot CLI registry:\n"
        )
        for name, length in failures:
            sys.stderr.write(
                f"::error::  {name}: {length} chars "
                f"({length - MAX_DESCRIPTION_CHARS} over)\n"
            )
        return 1

    print("✓ All skill descriptions are within the load limit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
