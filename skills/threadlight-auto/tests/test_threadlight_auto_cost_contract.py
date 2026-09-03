#!/usr/bin/env python3
"""Version + trust-boundary contract for `threadlight-auto` (Task 14).

Two things are frozen here, both of which a future edit could silently break:

  1. **The published version.** `threadlight-auto` gained a governed-actions
     lifecycle *recommendation* (never an execution stage), so its SKILL
     metadata must read `1.3.0` and the existing cost-actuals lockstep test
     must agree with it — a version pinned in two places that disagree is a
     published-surface bug, not a formatting detail.

  2. **The trust boundary the bump paid for.** Auto recommends; it never
     executes. `threadlight-governed-actions` is not a stage, not a stage
     probe, not a leg contract, and not one of the four advisory live legs,
     and no auto-authored prose may hand an agent a scaffold, policy-apply,
     canary, rollout, merge, or deploy command. The only wording allowed to
     name the skill is the exact approved lifecycle recommendation.

pytest-style (bare ``test_`` functions + ``assert``); no extra deps.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AUTO_SKILL = REPO / "skills" / "threadlight-auto" / "SKILL.md"
ORCHESTRATOR = REPO / "skills" / "threadlight-auto" / "references" / "orchestrator.py"
COST_ACTUALS_TEST = Path(__file__).resolve().parent / "test_cost_actuals_guidance.py"

AUTO_VERSION = "1.3.0"

EXPECTED_HANDOFF = {
    "execution": "manual-explicit",
    "design": (
        "run threadlight-governed-actions --phase design "
        "after threadlight-design"
    ),
    "pre_deploy": (
        "run threadlight-governed-actions --phase pre-deploy before deploy"
    ),
    "post_deploy": (
        "run threadlight-governed-actions --phase post-deploy "
        "against staging only"
    ),
    "manifest": "tests/governed-actions-manifest.json",
}

# Command-shaped tokens auto must never author — in code or in prose. Bare
# words (`scaffold`, `canary`, `merge`, `deploy`) are deliberately absent:
# they appear legitimately in the approved lifecycle wording and in the
# documented list of things auto explicitly never does.
FORBIDDEN_COMMAND_TOKENS = (
    "--scaffold",
    "--apply",
    "--canary",
    "apply-plan",
    "policy apply",
    "azd up",
    "azd deploy",
    "az deployment",
    "gh pr merge",
    "git merge",
    "git push",
)


def _load_orchestrator():
    spec = importlib.util.spec_from_file_location(
        "threadlight_auto_orchestrator_cost_contract", str(ORCHESTRATOR)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["threadlight_auto_orchestrator_cost_contract"] = mod
    spec.loader.exec_module(mod)
    return mod


orch = _load_orchestrator()


def _governed_actions_section() -> str:
    """Body of the SKILL.md section that owns the governed-actions handoff."""
    text = AUTO_SKILL.read_text(encoding="utf-8")
    headings = list(re.finditer(r"^(#{2,4})\s+(.*)$", text, re.M))
    hits = [
        text[m.end() : (headings[i + 1].start() if i + 1 < len(headings) else len(text))]
        for i, m in enumerate(headings)
        if "governed" in m.group(2).casefold()
    ]
    assert hits, "SKILL.md has no governed-actions section"
    assert len(hits) == 1, f"{len(hits)} governed-actions sections; expected 1"
    return hits[0]


# ---------------------------------------------------------------------------
# 1. version lockstep
# ---------------------------------------------------------------------------


def test_auto_version_is_bumped_for_the_governed_actions_recommendation() -> None:
    assert f'version: "{AUTO_VERSION}"' in AUTO_SKILL.read_text(encoding="utf-8")


def test_auto_version_matches_the_cost_actuals_lockstep_pin() -> None:
    """One published version, pinned in one place per test file — and equal."""
    pinned = re.search(
        r'^AUTO_VERSION = "([^"]+)"$', COST_ACTUALS_TEST.read_text(encoding="utf-8"), re.M
    )
    assert pinned, "test_cost_actuals_guidance.py no longer pins AUTO_VERSION"
    assert pinned.group(1) == AUTO_VERSION


# ---------------------------------------------------------------------------
# 2. the trust boundary the bump paid for
# ---------------------------------------------------------------------------


def test_governed_actions_handoff_is_exactly_the_approved_wording() -> None:
    assert orch.GOVERNED_ACTIONS_HANDOFF == EXPECTED_HANDOFF


def test_governed_actions_is_never_scheduled_by_auto() -> None:
    assert "governed_actions" not in orch.STAGES
    assert "governed_actions" not in orch.STAGE_PROBES
    assert all(
        leg.get("skill") != "threadlight-governed-actions"
        for leg in orch.LEG_CONTRACTS.values()
    )
    assert "threadlight-governed-actions" not in orch.MANUAL_HANDOFFS


def test_orchestrator_authors_no_privileged_command() -> None:
    """Scoped to the governed-actions block: auto's own deploy stage really
    does run `azd up`, and that pre-existing authority is not what this guard
    is about."""
    source = ORCHESTRATOR.read_text(encoding="utf-8")
    start = source.index("# Governed-actions lifecycle")
    end = source.index("# Freshness probes", start)
    block = source[start:end]
    for approved in EXPECTED_HANDOFF.values():
        block = block.replace(approved, "")
    low = block.casefold()
    for token in FORBIDDEN_COMMAND_TOKENS:
        assert token not in low, token


def test_skill_documents_manual_ownership_of_governed_actions() -> None:
    body = _governed_actions_section().casefold()
    for phrase in (
        "manual",
        "recommend",
        "never",
        "tests/governed-actions-manifest.json",
        EXPECTED_HANDOFF["design"].casefold(),
        EXPECTED_HANDOFF["pre_deploy"].casefold(),
        EXPECTED_HANDOFF["post_deploy"].casefold(),
    ):
        assert phrase in body, phrase


def test_skill_never_hands_the_agent_a_privileged_governed_actions_command() -> None:
    body = _governed_actions_section()
    for approved in EXPECTED_HANDOFF.values():
        body = body.replace(approved, "")
    low = body.casefold()
    for token in FORBIDDEN_COMMAND_TOKENS:
        assert token not in low, token


if __name__ == "__main__":  # pragma: no cover - convenience runner
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
