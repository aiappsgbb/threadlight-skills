"""Guard the cost tiers of the paid E2E workflow.

The E2E workflow is the only thing in this repo that can spend real money,
and it decides what to spend purely through `if:` expressions on individual
steps. That makes the mode gating load-bearing *and* invisible: a new step
appended near the deploy phases inherits nothing, so forgetting one gate
silently turns the $0.20 tier back into the $1 tier.

These tests are the thing that notices. The core rule is fail-safe: any step
after the design-only gate must either be explicitly classified as cheap, or
be excluded from design-only mode. A newly added step matches neither and
fails the suite until someone consciously decides which it is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "threadlight-e2e-foundry.yml"

GATE_STEP = "[contract gate] design→deploy contract check"
READINESS_SAFE_CHECK_STEP = "[Readiness proof] Run post-deploy safe-check"
EVIDENCE_GATE_STEP = "Evaluate lifecycle evidence semantics"
LIFECYCLE_SUMMARY_STEP = "[paid run] Lifecycle summary"

# Steps that appear after the design-only gate and are deliberately allowed to
# run in design-only mode, because they neither provision nor delete Azure
# resources. Adding a name here is a cost decision — make it consciously.
CHEAP_AFTER_GATE = frozenset(
    {
        "[design-only] Run summary",
        "Upload run artifacts",
        "Upload copilot logs",
    }
)


def load_steps() -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["e2e"]["steps"]


def load_inputs() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the bare `on:` key as the boolean True.
    trigger = doc.get("on", doc.get(True))
    return trigger["workflow_dispatch"]["inputs"]


def normalized_evidence_mode(mode: str) -> str:
    if mode in {"live-smoke", "full"}:
        return "live-smoke"
    if mode == "readiness-proof":
        return "readiness-proof"
    return "none"


def step_runs(condition: str | None, mode: str, teardown: str = "true") -> bool:
    """Evaluate a step's `if:` for a given mode.

    Only the tiny expression subset this workflow actually uses is supported:
    `always()`, `success()`, `&&`, and equality against `inputs.mode` /
    `inputs.teardown`. Anything richer should fail loudly rather than be
    silently mis-evaluated, so unsupported syntax raises.
    """
    if condition is None:
        return True
    expr = re.sub(r"\b(?:always|success)\(\)", "True", condition.strip())
    expr = expr.replace("inputs.mode", repr(mode))
    expr = expr.replace(
        "steps.evidence-mode.outputs.mode", repr(normalized_evidence_mode(mode))
    )
    expr = expr.replace("inputs.teardown", repr(teardown))
    expr = expr.replace(" && ", " and ")
    expr = expr.replace(" || ", " or ")
    if not re.fullmatch(r"[\w\s'!=.()-]+", expr):
        raise AssertionError(f"unsupported `if:` syntax for this guard: {condition!r}")
    return bool(eval(expr))  # noqa: S307 - input is this repo's own workflow


def names_for(mode: str) -> list[str]:
    return [
        s.get("name", s.get("uses", "?"))
        for s in load_steps()
        if step_runs(s.get("if"), mode)
    ]


def test_workflow_parses():
    assert load_steps(), "E2E workflow has no steps"


def test_five_lifecycle_modes_are_offered():
    options = load_inputs()["mode"]["options"]
    assert options == [
        "live-smoke",
        "readiness-proof",
        "full",
        "design-only",
        "smoke-only",
    ], (
        "mode options changed; the workflow header and these guards both "
        "assume the approved Task 2 lifecycle tiers and alias ordering"
    )


def test_default_mode_is_live_smoke():
    # Changing the default silently changes what a bare dispatch costs.
    assert load_inputs()["mode"]["default"] == "live-smoke"


def test_mode_description_documents_every_option_and_alias_status():
    description = load_inputs()["mode"]["description"]
    for option in load_inputs()["mode"]["options"]:
        assert option in description, f"mode option {option!r} is undocumented"
    assert "deprecated alias" in description, (
        "the workflow must keep documenting that `full` is only a deprecated "
        "alias to `live-smoke`"
    )


def test_step_condition_evaluator_understands_evidence_mode_output():
    assert step_runs(
        "steps.evidence-mode.outputs.mode == 'readiness-proof'",
        "readiness-proof",
    )
    assert not step_runs(
        "steps.evidence-mode.outputs.mode == 'readiness-proof'",
        "live-smoke",
    )
    assert step_runs("steps.evidence-mode.outputs.mode != 'none'", "full")
    assert not step_runs("steps.evidence-mode.outputs.mode != 'none'", "design-only")


def test_design_only_runs_the_design_and_pattern0_phases():
    names = names_for("design-only")
    for expected in (
        "[Phase 1/4] Drive §4.2 — threadlight-design Fast-PoC",
        "[Phase 1/4 assert] Design artifacts exist + are non-trivial",
        "[Phase 2/4] Drive §4.3 — Pattern 0 setup via agent",
        "[Phase 2/4 assert] Workflow-owned --info + --check",
        GATE_STEP,
    ):
        assert expected in names, f"design-only must run {expected!r}"


@pytest.mark.parametrize(
    "forbidden",
    [
        "[Phase 3/4] Drive §6.2+§6.3 — threadlight-deploy + azd up",
        "Assert agent deployed + responding",
        "Snapshot deployed resources",
        "Teardown — azd down --force --purge",
        "Fallback teardown — async RG delete",
    ],
)
def test_design_only_never_provisions_or_deletes(forbidden):
    """The whole point of the tier is that no resource group is ever created."""
    assert forbidden not in names_for("design-only")


def test_every_step_after_the_gate_is_classified():
    """Fail-safe: a new deploy-phase step must be gated or declared cheap.

    This is the guard that actually earns its keep. Someone adding a Phase 6
    that spends money will copy the `if:` from a neighbour — and if they copy
    a pre-design-only one, this test fails before the bill arrives.
    """
    steps = load_steps()
    names = [s.get("name", s.get("uses", "?")) for s in steps]
    gate_index = names.index(GATE_STEP)

    unclassified = [
        name
        for step, name in zip(steps[gate_index + 1 :], names[gate_index + 1 :])
        if step_runs(step.get("if"), "design-only") and name not in CHEAP_AFTER_GATE
    ]
    assert not unclassified, (
        "these steps run after the design-only gate without being declared "
        f"cheap: {unclassified}. Either add `&& inputs.mode != 'design-only'` "
        "to the step's `if:`, or add its name to CHEAP_AFTER_GATE here if it "
        "genuinely costs nothing."
    )


def test_the_gate_step_actually_invokes_the_contract_checker():
    """A gate that doesn't run the checker is decoration."""
    step = next(s for s in load_steps() if s.get("name") == GATE_STEP)
    body = step["run"]
    assert step.get("if") == "inputs.mode != 'smoke-only'"
    assert "check_pilot_contract.py" in body
    # Fast-PoC is what Phase 1 drives, so the profile must match or the §13
    # silent-defaults callout would go unchecked in the cheap tier.
    assert "--profile fast-poc" in body
    for stage in ("--stage design", "--stage pattern0"):
        assert stage in body, f"gate must check {stage}"
    assert "--stage deploy" not in body, (
        "design-only never deploys, so deployment-posture.md cannot exist yet"
    )
    # The SPEC §14 value-model contract check is opt-in on the checker (for
    # legacy-project compatibility), so the design-only gate must explicitly
    # ask for it or a missing/defaulted value model would go unchecked in
    # the one tier that actually runs the checker against real output.
    assert "--require-value-model" in body, (
        "design-only gate must pass --require-value-model or the §14 "
        "value-model contract goes unenforced in the cheap tier"
    )


def test_require_value_model_is_not_leaked_into_other_checker_uses():
    """The stricter flag is a design-only-gate decision, not a global one.

    This asserts on step semantics (which step's `run` body carries the
    flag, and under which `if:`) rather than a raw text count of
    `check_pilot_contract.py` in the workflow file. A text count would stay
    green if a second, legitimate checker invocation were added elsewhere
    without the flag — that's not leakage. It would also stay green if the
    flag were copied onto a step whose condition is broader than
    design-only, which *is* leakage this test must catch.
    """
    steps = load_steps()
    leaked = [
        s.get("name", s.get("uses", "?"))
        for s in steps
        if "--require-value-model" in s.get("run", "")
    ]
    assert leaked == [GATE_STEP], (
        "--require-value-model must appear in exactly one step's `run` "
        f"body, the design-only gate ({GATE_STEP!r}); found it in: {leaked}"
    )
    gate = next(s for s in steps if s.get("name") == GATE_STEP)
    assert gate.get("if") == "inputs.mode != 'smoke-only'", (
        "the shared contract gate's `if:` changed; --require-value-model must "
        "stay pinned to every non-smoke-only path or one of the paid/design "
        "tiers will silently stop enforcing the design→deploy contract"
    )


def test_the_referenced_contract_checker_exists():
    assert (REPO_ROOT / "scripts" / "ci" / "check_pilot_contract.py").is_file()


def test_contract_gate_runs_for_every_non_smoke_mode():
    for mode in ("design-only", "live-smoke", "readiness-proof", "full"):
        assert GATE_STEP in names_for(mode), f"{mode} must run the contract gate"
    assert GATE_STEP not in names_for("smoke-only")


@pytest.mark.parametrize("mode", ["live-smoke", "readiness-proof", "full"])
def test_paid_modes_run_the_major_lifecycle_steps(mode):
    """Paid modes share the deploy/invoke/report spine; `full` aliases live-smoke."""
    names = names_for(mode)
    for expected in (
        "[Phase 3/4] Drive §6.2+§6.3 — threadlight-deploy + azd up",
        "[Phase 4/4] Drive §6.4 — invoke killer prompts",
        "[Phase 5/5] Run evals + red-team + govern legs against the deployed pilot",
        "Assert agent deployed + responding",
        EVIDENCE_GATE_STEP,
        LIFECYCLE_SUMMARY_STEP,
        "Teardown — azd down --force --purge",
    ):
        assert expected in names, f"{mode} lost {expected!r}"


def test_only_readiness_proof_runs_the_strict_post_deploy_safe_check():
    assert READINESS_SAFE_CHECK_STEP in names_for("readiness-proof")
    for mode in ("live-smoke", "full", "design-only", "smoke-only"):
        assert READINESS_SAFE_CHECK_STEP not in names_for(mode)


def test_full_alias_keeps_live_smoke_semantics():
    assert "[design-only] Run summary" not in names_for("full")
    assert READINESS_SAFE_CHECK_STEP not in names_for("full")


def test_readiness_summary_requires_explicit_passing_evidence():
    body = next(
        s for s in load_steps() if s.get("name") == LIFECYCLE_SUMMARY_STEP
    )["run"]
    assert ".status" in body, (
        "the paid-run summary must inspect the evidence-gate JSON status "
        "before it claims readiness semantics were asserted"
    )
    # For live-smoke the summary must require explicit passing evidence (not
    # just deploy-log or file heuristics). It should reference the EVIDENCE_STATUS
    # variable and check for a passing value.
    assert "EVIDENCE_STATUS" in body, (
        "the paid-run summary must read the evidence status into EVIDENCE_STATUS"
    )
    import re

    assert re.search(r'=\s*"pass"', body), (
        "the paid-run summary must explicitly check for EVIDENCE_STATUS == \"pass\" "
        "before claiming live-smoke success"
    )


def test_smoke_only_stays_free():
    names = names_for("smoke-only")
    assert "Smoke-check Skill-tool discovery" in names
    for costly in (
        "[Phase 1/4] Drive §4.2 — threadlight-design Fast-PoC",
        "[Phase 3/4] Drive §6.2+§6.3 — threadlight-deploy + azd up",
        GATE_STEP,
    ):
        assert costly not in names, f"smoke-only must not run {costly!r}"


def test_teardown_is_reachable_in_full_mode():
    """Guard the guard: teardown carries three `&&` clauses now."""
    steps = load_steps()
    teardown = next(
        s for s in steps if s.get("name") == "Teardown — azd down --force --purge"
    )
    assert step_runs(teardown.get("if"), "full", teardown="true")
    assert not step_runs(teardown.get("if"), "full", teardown="false")


# ── runbook drift ────────────────────────────────────────────────────────
# The operator runbook documented an input (`scenario`) that had been renamed
# to `workload` and a job timeout that had since changed. Nobody noticed,
# because prose is not executable. These two tests make it executable.

RUNBOOK = REPO_ROOT / "docs" / "ci" / "threadlight-e2e.md"


def test_runbook_documents_every_workflow_input():
    prose = RUNBOOK.read_text(encoding="utf-8")
    undocumented = [name for name in load_inputs() if f"**{name}**" not in prose]
    assert not undocumented, (
        f"{RUNBOOK.name} does not document these workflow inputs: {undocumented}"
    )


def test_runbook_does_not_document_inputs_that_no_longer_exist():
    """Catches a renamed input still being advertised to operators."""
    prose = RUNBOOK.read_text(encoding="utf-8")
    real = set(load_inputs())
    advertised = set(re.findall(r"^   - \*\*(\w+)\*\*", prose, flags=re.MULTILINE))
    stale = advertised - real
    assert not stale, (
        f"{RUNBOOK.name} advertises inputs the workflow no longer accepts: "
        f"{sorted(stale)}"
    )


def test_runbook_quotes_the_real_job_timeout():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    timeout = doc["jobs"]["e2e"]["timeout-minutes"]
    assert f"timeout-minutes: {timeout}" in RUNBOOK.read_text(encoding="utf-8"), (
        f"runbook does not quote the current job cap of {timeout} minutes"
    )
