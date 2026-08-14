"""Pin the version across script + SKILL.md frontmatter.

The pin is deliberate: a version bump must be a conscious edit here too, so a
behaviour change cannot ship under an unchanged version. Both assertions read
the same `EXPECTED` constant, so a bump is a one-line change and the two can
never silently disagree.
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

EXPECTED = "0.10.0"

_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


def test_script_version_is_pinned():
    assert mod.VERSION == EXPECTED, f"expected {EXPECTED}, got {mod.VERSION!r}"


def test_version_matches_skill_md():
    skill_md = (ROOT / "SKILL.md").read_text()
    assert f'version: "{EXPECTED}"' in skill_md, (
        f'SKILL.md frontmatter must declare version: "{EXPECTED}"'
    )


if __name__ == "__main__":
    test_script_version_is_pinned()
    test_version_matches_skill_md()
    print("OK")
