"""Version is well-formed and declared in SKILL.md.

threadlight-customize is an instructions/runbooks skill — there is no
generator module to match against (by design). This test simply pins that
SKILL.md declares a well-formed semver metadata.version.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_skill_md_declares_semver_version():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r'version:\s*"([^"]+)"', skill)
    assert m, "SKILL.md must declare metadata.version"
    assert re.match(r"^\d+\.\d+\.\d+$", m.group(1)), (
        f"version {m.group(1)!r} is not semver"
    )
