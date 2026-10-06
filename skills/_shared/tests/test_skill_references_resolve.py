"""Every skill a catalog surface tells an agent to use, install or request must exist.

2.19.2 regression: threadlight-deploy listed a `threadlight-workflow` skill that
never existed, so agents told users to "ask the maintainer to publish it". A name
resolves only to one of:

- this repository's skills/<name>/SKILL.md;
- the pinned official lock (skills/_shared/official-skills-lock.json);
- the awesome-gbb catalog recorded in skills/_shared/skill-dependencies.json;
- an explicitly labelled external source in EXTERNAL (with its real location);
- an agent-runtime skill packaged in the same project (`<project>/src/agent/skills/<name>/SKILL.md`).

CHANGELOG.md and docs/superpowers/ (frozen design plans/specs) are historical
records and are not scanned. Files listed in HISTORICAL may name a phantom only on
lines that state it does not exist or has been resolved.
This is a text contract, not proof of runtime skill discovery.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SHARED = ROOT / "skills" / "_shared"
TEXT_SUFFIXES = {".md", ".py", ".js", ".mjs", ".json", ".yml", ".yaml", ".html", ".txt", ".sh", ".toml"}
NAME = r"[a-z0-9]+(?:-[a-z0-9]+)+"
SKIP_FILES = {"CHANGELOG.md", "skills/_shared/tests/test_skill_references_resolve.py"}
SKIP_PREFIXES = ("docs/superpowers/",)
# Words that qualify the word "skill" rather than name one ("the awesome-gbb skill `x`").
QUALIFIERS = {"awesome-gbb", "threadlight-owned"}
# name -> required marker on the same line proving it is labelled as external/retired.
EXTERNAL = {
    "agentic-loop": "github.com/aiappsgbb/agentic-loop",
    "azure-hosted-copilot-sdk": "retired",
}
HISTORICAL = {
    "threadlight-workflow": {
        "docs/ci/router-validation.md",
        "skills/threadlight-router-bench/references/findings/2026-06-30-router-validation-5.4-vs-mini.md",
    },
}
NEGATION = re.compile(r"never existed|does not exist|was never|non-existent|phantom|not shipped|never shipped|resolved", re.I)

PATTERNS = [
    re.compile(rf"`({NAME})`\s+(?:companion\s+|official\s+)?skill\b"),
    re.compile(rf"\b(?:use|invoke|load|install|prefer|call|run)s?\s+(?:the\s+)?({NAME})\s+skill\b", re.I),
    re.compile(rf"skill install\s+[\w./-]+\s+({NAME})"),
    re.compile(rf"\bskill\(\s*[\"']({NAME})[\"']"),
    re.compile(rf"\bthe\s+skill\s+`({NAME})`"),
    re.compile(rf"\b(?:the|a)\s+((?:threadlight|foundry|citadel|azure|ghcp)-[a-z0-9-]*[a-z0-9])\s+skill\b"),
    re.compile(rf"(?:^|(?<=[\s(`'\"\[<]))(?:\.{{1,2}}/)*skills/({NAME})/SKILL\.md"),
]
CELL_NAME = re.compile(rf"^\[?`({NAME})`\]?")
LINK_TARGET = re.compile(r"\]\(([^)\s#]*SKILL\.md)")


def agent_runtime_skills() -> dict[str, set[str]]:
    projects: dict[str, set[str]] = {}
    for path in ROOT.glob("**/src/agent/skills/*/SKILL.md"):
        rel = path.relative_to(ROOT).as_posix()
        projects.setdefault(rel.split("src/agent/skills/")[0], set()).add(path.parent.name)
    return projects


def known_names() -> set[str]:
    names = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    lock = json.loads((SHARED / "official-skills-lock.json").read_text(encoding="utf-8"))
    for plugin in lock["plugins"].values():
        names |= set(plugin["skills"])
    for catalog in lock.get("catalog_skill_names", {}).values():
        names |= set(catalog)
    deps = json.loads((SHARED / "skill-dependencies.json").read_text(encoding="utf-8"))
    names |= set(deps["custom_catalog"]["skills"])
    return names


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().lstrip(">").strip().strip("|").split("|")]


def references(text: str, rel: str = "x.md"):
    """Yield (line_number, name, line) for each skill-like reference."""
    skill_column = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip().lstrip(">").strip()
        if not stripped.startswith("|"):
            skill_column = None
        elif skill_column is None:
            header = [c.strip("* ").lower() for c in cells(line)]
            skill_column = next((i for i, c in enumerate(header) if c.startswith("skill")), None)
        else:
            row = cells(line)
            if skill_column < len(row) and (match := CELL_NAME.match(row[skill_column])):
                yield number, match.group(1), line
        for pattern in PATTERNS:
            for match in pattern.finditer(line):
                yield number, match.group(1), line
        # Relative Markdown links (`../threadlight-x/SKILL.md`) resolved against the file.
        for match in LINK_TARGET.finditer(line):
            target = (ROOT / rel).parent.joinpath(match.group(1)).resolve()
            try:
                parts = target.relative_to(ROOT / "skills").parts
            except ValueError:
                continue
            if len(parts) == 2 and re.fullmatch(NAME, parts[0]):
                yield number, parts[0], line


def unresolved(rel: str, text: str, names: set[str], local: dict[str, set[str]] | None = None) -> list[str]:
    problems = []
    local_names = set().union(*(v for k, v in (local or {}).items() if rel.startswith(k)))
    for number, name, line in references(text, rel):
        if name in names or name in local_names or name.lower() in QUALIFIERS:
            continue
        if name in EXTERNAL and EXTERNAL[name] in line:
            continue
        elif rel in HISTORICAL.get(name, ()) and NEGATION.search(line):
            continue
        problems.append(f"{rel}:{number}: {name}")
    return problems


def tracked_text_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    for rel in out.splitlines():
        path = ROOT / rel
        if rel in SKIP_FILES or rel.startswith(SKIP_PREFIXES) or path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            yield rel, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue


def test_detector_flags_a_phantom_in_every_reference_shape():
    names = {"threadlight-deploy"}
    samples = [
        "| Skill | When |\n|--|--|\n| `threadlight-ghost` | always |",
        "> | Skill or reference | When |\n> |--|--|\n> | `threadlight-ghost` | always |",
        "prefer the `threadlight-ghost` skill when installed",
        "Invoke threadlight-ghost skill with:",
        "gh skill install aiappsgbb/threadlight-skills threadlight-ghost",
        'skill("threadlight-ghost")',
        "publish skills/threadlight-ghost/SKILL.md",
        "the `azure-hosted-copilot-sdk` skill is great",
        "see [x](../skills/threadlight-ghost/SKILL.md)",
        "delegate to the skill `threadlight-ghost`",
        "the threadlight-ghost skill generates it",
        "| Phase | Skill |\n|--|--|\n| 2 | `threadlight-ghost` |",
    ]
    for sample in samples:
        assert unresolved("x.md", sample, names), sample
    assert unresolved("skills/threadlight-deploy/SKILL.md", "[x](../threadlight-ghost/SKILL.md)", names)


def test_detector_accepts_real_and_non_skill_names():
    names = {"threadlight-deploy", "foundry-teams-bot"}
    text = (
        "| Skill | When |\n|--|--|\n| `threadlight-deploy` | always |\n\n"
        "| Field | Value |\n|--|--|\n| `threadlight-governance-manifest` | schema |\n"
        "Invoke the awesome-gbb skill `foundry-teams-bot`; the official `azure-hosted-copilot-sdk` skill was retired\n"
        "| Skill | Purpose |\n|--|--|\n| `intake-validation` | agent-local |"
    )
    assert unresolved("ex/AGENTS.md", text, names, {"ex/": {"intake-validation"}}) == []
    assert unresolved("other/AGENTS.md", "| Skill |\n|--|\n| `intake-validation` |", names, {"ex/": {"intake-validation"}})


def test_every_referenced_skill_resolves_to_a_real_source():
    names, local = known_names(), agent_runtime_skills()
    problems = [p for rel, text in tracked_text_files() for p in unresolved(rel, text, names, local)]
    assert not problems, "Skill references that do not resolve:\n" + "\n".join(problems)


def test_deploy_never_asks_for_an_uninstalled_workflow_skill():
    text = (ROOT / "skills" / "threadlight-deploy" / "SKILL.md").read_text(encoding="utf-8")
    assert "threadlight-workflow" not in text
    gate = text[text.index("**Workflow model gate.**"):]
    gate = gate[: gate.index("Create these files in the project root")]
    assert "workflow_model" in gate
    for phrase in ("when it is installed", "/skills list", "install", "publish", "maintainer"):
        assert phrase not in gate, phrase
