"""Version is well-formed and matches the SKILL.md front-matter."""
import importlib.util
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "generate_pipeline", ROOT / "scripts" / "generate_pipeline.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["generate_pipeline"] = mod
_spec.loader.exec_module(mod)


def test_version_is_semver():
    assert re.match(r"^\d+\.\d+\.\d+$", mod.VERSION)


def test_version_matches_skill_md():
    skill = (ROOT / "SKILL.md").read_text()
    m = re.search(r'version:\s*"([^"]+)"', skill)
    assert m, "SKILL.md must declare metadata.version"
    assert m.group(1) == mod.VERSION
