"""Quality-rubric scorer for threadlight-router-validation.

Scores a built PoC's artifacts (SPEC.md etc.) against a per-workload rubric
of "hard-points". Match strategies, all case-insensitive over concatenated
artifact text:
  - contains: substring present
  - regex:    re.search matches
  - all_of:   every substring present
  - any_of:   at least one substring present
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Artifact files we concatenate for matching, in priority order.
_ARTIFACT_GLOBS = ("specs/SPEC.md", "AGENTS.md", "tests/killer-prompts.md",
                   "specs/*.md")


def _gather_text(target: Path) -> str:
    """Return lowercased concatenated text of relevant artifacts.

    `target` may be a single file or a PoC directory.
    """
    if target.is_file():
        return target.read_text(encoding="utf-8", errors="ignore").lower()
    parts: list[str] = []
    seen: set[Path] = set()
    for pattern in _ARTIFACT_GLOBS:
        for p in sorted(target.glob(pattern)):
            if p.is_file() and p not in seen:
                seen.add(p)
                parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts).lower()


def _check_passes(text: str, check: dict[str, Any]) -> bool:
    if "contains" in check:
        return check["contains"].lower() in text
    if "regex" in check:
        return re.search(check["regex"], text, re.IGNORECASE) is not None
    if "all_of" in check:
        return all(s.lower() in text for s in check["all_of"])
    if "any_of" in check:
        return any(s.lower() in text for s in check["any_of"])
    return False


def score_rubric(target: Path, rubric: dict[str, Any]) -> dict[str, Any]:
    """Score `target` artifacts against `rubric`. Returns score 0..1 + checks."""
    text = _gather_text(Path(target))
    checks_out = []
    total_w = 0.0
    earned_w = 0.0
    for check in rubric.get("checks", []):
        w = float(check.get("weight", 1))
        passed = _check_passes(text, check)
        total_w += w
        if passed:
            earned_w += w
        checks_out.append({"id": check["id"], "passed": passed, "weight": w})
    score = round(earned_w / total_w, 4) if total_w else 0.0
    return {"score": score, "checks": checks_out}


def load_rubric(path: Path) -> dict[str, Any]:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
