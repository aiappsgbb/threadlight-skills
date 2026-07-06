"""Pin v0.6.1 version across script + SKILL.md frontmatter."""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


def test_version_is_061():
    assert mod.VERSION == "0.6.1", f"expected 0.6.1, got {mod.VERSION!r}"


def test_version_matches_skill_md():
    skill_md = (ROOT / "SKILL.md").read_text()
    assert 'version: "0.6.1"' in skill_md, "SKILL.md frontmatter must declare version: \"0.6.1\""


if __name__ == "__main__":
    test_version_is_061()
    test_version_matches_skill_md()
    print("OK")
