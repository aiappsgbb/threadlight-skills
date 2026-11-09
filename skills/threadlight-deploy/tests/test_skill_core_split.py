"""The deploy SKILL.md stays a lean core that points to situational references."""

import re
from pathlib import Path

from deploy_skill_corpus import split_references

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"
CORE_LINE_CEILING = 900
REFERENCE_LINK = re.compile(r"\]\((references/[^)#\s]+\.md)(?:#[^)]*)?\)")

SPLIT_REFERENCES = (
    "selected-governance.md",
    "generate-only-mode.md",
    "poly-repo-guard.md",
    "mcp-in-foundry.md",
    "runtime-variants.md",
    "model-access.md",
    "deployment-posture.md",
    "phase-2-artifacts.md",
    "validation-checklist.md",
    "postdeploy-gate.md",
    "architecture-reference.md",
    "showroom-static-site.md",
    "azd-extension-sdk.md",
    "teams-bot.md",
    "azd-project.md",
    "module-composer.md",
    "demo-data-seed.md",
    "prep-guide-walkthrough.md",
    "citadel-handoff.md",
    "gotchas.md",
)


def _core() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_core_skill_stays_under_line_ceiling():
    lines = _core().rstrip("\n").split("\n")
    assert len(lines) <= CORE_LINE_CEILING, (
        f"threadlight-deploy/SKILL.md has {len(lines)} lines; move situational "
        f"material to references/*.md (ceiling {CORE_LINE_CEILING})"
    )


def test_every_referenced_markdown_reference_exists():
    targets = sorted(set(REFERENCE_LINK.findall(_core())))
    assert targets, "core SKILL.md links no references/*.md files"
    missing = [t for t in targets if not (SKILL_DIR / t).is_file()]
    assert not missing, f"SKILL.md links missing references: {missing}"


def test_split_references_are_linked_from_core_with_a_read_when_pointer():
    linked = set(REFERENCE_LINK.findall(_core()))
    for name in SPLIT_REFERENCES:
        path = f"references/{name}"
        assert (SKILL_DIR / path).is_file(), f"{path} does not exist"
        assert path in linked, f"core SKILL.md does not point to {path}"
        text = (SKILL_DIR / path).read_text(encoding="utf-8")
        assert "](../SKILL.md)" in text and "Read it " in text, (
            f"{path} must link back to SKILL.md and say when to read it"
        )


def test_corpus_helper_reads_exactly_the_split_references():
    assert sorted(p.name for p in split_references()) == sorted(SPLIT_REFERENCES)
