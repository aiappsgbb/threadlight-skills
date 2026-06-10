"""Phase F2: every `kind: sibling-skill` recipe must appear in the map.

This is the load-bearing gate that prevents recipe drift from the map. If
the test fails, either add the new finding to references/sibling-skills-map.md
or change the recipe's `kind` away from `sibling-skill`.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RDIR = ROOT / "references" / "remediation-recipes"
MAP = ROOT / "references" / "sibling-skills-map.md"


def _sibling_skill_recipes():
    out = []
    for path in RDIR.glob("*.md"):
        if path.name.startswith("_"):
            continue
        text = path.read_text()
        m = re.search(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not m:
            continue
        front = m.group(1)
        if re.search(r"^kind:\s*sibling-skill\s*$", front, re.MULTILINE):
            out.append(path.stem)
    return sorted(out)


def test_every_sibling_recipe_is_in_map():
    assert MAP.exists(), "sibling-skills-map.md not found"
    text = MAP.read_text()
    missing = [fid for fid in _sibling_skill_recipes() if fid not in text]
    assert not missing, f"sibling-skill recipes not listed in map: {missing}"


def test_map_lists_at_least_one_sibling_recipe():
    """Sanity: if v0.4.0 ships with zero sibling-skill recipes, something
    regressed — Phase B explicitly committed NET-501 as sibling-skill."""
    assert _sibling_skill_recipes(), "no sibling-skill recipes found; expected NET-501 at minimum"


if __name__ == "__main__":
    test_every_sibling_recipe_is_in_map()
    test_map_lists_at_least_one_sibling_recipe()
    print("OK")
