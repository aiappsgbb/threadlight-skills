"""Structure: every reference this skill promises actually ships, and every
relative link in SKILL.md resolves to a file on disk.

An instructions skill is only as good as its templates existing. This guards
against a SKILL.md that points at a runbook nobody wrote.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

EXPECTED = [
    "SKILL.md",
    "references/customer-profile.md.tmpl",
    "references/customization-map.md",
    "references/fork-runbook.md",
    "references/non-coverage.md",
    "references/field-notes-telco-pilot.md",
    "references/private-env-test/codespaces.md",
    "references/private-env-test/azure-ml-vscode.md",
    "references/private-env-test/private-vnet-checklist.md",
]


def test_expected_files_exist():
    missing = [p for p in EXPECTED if not (ROOT / p).exists()]
    assert not missing, f"missing shipped files: {missing}"


def test_skill_md_relative_links_resolve():
    skill_path = ROOT / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    # markdown links of the form (references/....) — strip any #anchor
    links = re.findall(r"\]\((references/[^)]+)\)", text)
    assert links, "SKILL.md should link its references"
    broken = []
    for link in links:
        target = link.split("#", 1)[0]
        # directory links (trailing slash) resolve to the dir itself
        if not (ROOT / target).exists():
            broken.append(link)
    assert not broken, f"SKILL.md links that do not resolve: {broken}"
