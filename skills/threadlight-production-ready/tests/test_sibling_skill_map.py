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


def test_recipes_for_planned_siblings_must_be_manual():
    """Recipes for unbuilt upstream sibling skills MUST be kind: manual (issue #31).
    
    The sibling-skills-map.md marks planned skills inline as `*(planned — awesome-gbb#NNN)*`.
    Any recipe in such a row must declare `kind: manual` in its YAML front-matter.
    Otherwise the apply-plan dispatcher would call a non-existent sibling skill.
    """
    import re
    import pathlib
    
    text = MAP.read_text(encoding="utf-8")
    planned_recipes = []
    for line in text.splitlines():
        if "(planned" not in line:
            continue
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells[0] is empty (leading "|"), cells[1] is the finding-id cell
        if len(cells) < 2:
            continue
        first = cells[1]
        if not first or first in ("Finding ID", ":---", "---"):
            continue
        planned_recipes.append(first)
    
    assert planned_recipes, "expected at least one '(planned' row in sibling-skills-map.md"
    
    offenders = []
    for rid in planned_recipes:
        rpath = RDIR / f"{rid}.md"
        if not rpath.exists():
            # Recipe file missing — separate problem, not what this test gates.
            continue
        rtext = rpath.read_text(encoding="utf-8")
        m = re.search(r"^kind:\s*([a-z-]+)\s*$", rtext, re.MULTILINE)
        if not m:
            offenders.append(f"{rid}: no kind: in front-matter")
            continue
        if m.group(1) != "manual":
            offenders.append(
                f"{rid}: kind: {m.group(1)} but sibling skill is unbuilt — must be 'manual'."
            )
    
    assert offenders == [], (
        "Planned-sibling rule violated. See #31.\n  " + "\n  ".join(offenders)
    )


if __name__ == "__main__":
    test_every_sibling_recipe_is_in_map()
    test_map_lists_at_least_one_sibling_recipe()
    test_recipes_for_planned_siblings_must_be_manual()
    print("OK")
