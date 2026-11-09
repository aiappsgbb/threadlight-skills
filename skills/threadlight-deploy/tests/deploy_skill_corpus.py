"""Read the threadlight-deploy core SKILL.md together with its split references.

The core SKILL.md links situational references that were moved out of it
verbatim. Contract tests whose statement moved read the whole corpus, so a
negative assertion keeps covering the moved text.
"""

import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"
SPLIT_MARKER = "moved out of the core"
_REFERENCE_LINK = re.compile(r"\]\((references/[^)#\s/]+\.md)(?:#[^)]*)?\)")


def split_references() -> list[Path]:
    core = SKILL.read_text(encoding="utf-8")
    paths = []
    for rel in dict.fromkeys(_REFERENCE_LINK.findall(core)):
        path = SKILL_DIR / rel
        if SPLIT_MARKER in path.read_text(encoding="utf-8"):
            paths.append(path)
    return paths


def read_reference(name: str) -> str:
    return (SKILL_DIR / "references" / name).read_text(encoding="utf-8")


def read_corpus() -> str:
    parts = [SKILL.read_text(encoding="utf-8")]
    parts += [path.read_text(encoding="utf-8") for path in split_references()]
    return "\n".join(parts)
