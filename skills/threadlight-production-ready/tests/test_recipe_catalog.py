"""Recipe catalog enforcement gate (Phase B Task B2).

Three checks:
1. Every non-experimental must-fix finding has a recipe file.
2. Every recipe file has all required markdown sections.
3. load_recipe_catalog accepts every recipe (i.e. valid YAML front-matter
   and a `kind` from APPLY_PLAN_KINDS).

This file is RED on creation and turns green when Phase B completes
(after the last bucket in B15 lands).
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_ready.py"
RDIR = ROOT / "references" / "remediation-recipes"

_spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod  # Python 3.14 dataclass+importlib workaround
_spec.loader.exec_module(mod)

REQUIRED_SECTIONS = (
    "## Target file",
    "## Edit type",
    "## Edit recipe",
    "## Verification",
)


def _must_fix_ids():
    out = []
    for fid, meta in mod.FINDING_CATALOG.items():
        if meta.get("severity") == "must-fix" and not meta.get("experimental"):
            out.append(fid)
    return sorted(out)


def test_every_must_fix_has_recipe():
    missing = [fid for fid in _must_fix_ids() if not (RDIR / f"{fid}.md").exists()]
    assert not missing, (
        f"{len(missing)} must-fix finding(s) without recipe: {missing}\n"
        "→ author the recipe under references/remediation-recipes/{ID}.md"
    )


def test_every_recipe_has_required_sections():
    bad = []
    for path in sorted(RDIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        for sec in REQUIRED_SECTIONS:
            if sec not in text:
                bad.append(f"{path.name}: missing {sec!r}")
    assert not bad, "\n".join(bad)


def test_loader_accepts_every_recipe():
    mod.load_recipe_catalog(RDIR)


if __name__ == "__main__":
    test_every_must_fix_has_recipe()
    test_every_recipe_has_required_sections()
    test_loader_accepts_every_recipe()
    print("OK")
