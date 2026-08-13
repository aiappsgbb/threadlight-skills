"""Run-durability checks landing inside pillar 8 `hitl-audit`.

Implements Q2 (idempotency is declared, never verified) and Q3 (a HITL gate
can outlive any session) from
`docs/superpowers/specs/2026-08-13-run-durability-deep-dive-design.md`.

Two new findings:

* ``HITL-006`` — the skill contracts that make up the super-agent declare a
  *substantive* idempotency statement, not a box-ticking one.
* ``HITL-007`` — SPEC section 8 names the trigger that resumes a gated unit of
  work and the state that gets rehydrated.

Both are declared-and-attested checks, never runtime probes: the deep dive's
open question 2 says outright that a regex for ``if-none-match`` proves very
little, so neither check is allowed to claim it verified behaviour.
"""
import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
EXAMPLE = REPO / "examples" / "returns-triage-governed"

_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py")
pr = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = pr
_spec.loader.exec_module(pr)


SEC8 = """## 8. Human Interaction Points

### Supervisor escalation review
- **Trigger**: BR-003 fires.
- **Actor**: Returns supervisor
- **Channel**: Teams adaptive card
- **Timeout/SLA**: manual review < 24h.
"""

CONTRACT = """---
name: {name}
description: does a thing. USE FOR the thing.
---

# {name}

## Operational contract
- **Inputs**: `rma_id`
- **Outputs**: a decision
- **Deps**: tool `returns_apply_decision`
- **Idempotency**: {idem}
- **Failure behavior**: escalate
"""


def _repo(spec: str = "# spec\n", **files: str) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp())
    (root / "specs").mkdir(parents=True, exist_ok=True)
    (root / "specs" / "SPEC.md").write_text(spec, encoding="utf-8")
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _skills(**named_idempotency: str) -> dict[str, str]:
    return {
        f"src/agent/skills/{name}/SKILL.md": CONTRACT.format(name=name, idem=idem)
        for name, idem in named_idempotency.items()
    }


def _by_id(findings):
    return {f.id: f for f in findings}


def _hitl(root: pathlib.Path):
    ctx = pr.RepoContext.from_repo(root, {})
    return _by_id(pr._check_hitl_static(ctx))


# --- catalog wiring -------------------------------------------------------

def test_both_findings_are_registered_under_pillar_8():
    for fid in ("HITL-006", "HITL-007"):
        assert fid in pr.FINDING_CATALOG, f"{fid} missing from FINDING_CATALOG"
        assert pr.FINDING_CATALOG[fid]["pillar"] == "hitl-audit"
        assert pr.FINDING_CATALOG[fid]["tier"] == 0, "static check, tier 0"


def test_severities_match_the_deep_dive_decision():
    """HITL-006 gates; HITL-007 deliberately does not.

    Downgrading HITL-007 to should-fix is a conscious deviation from the deep
    dive: it is a brand-new *declaration* requirement, and a must-fix would
    retroactively flip the hard gate for every pilot that already declares
    section 8 gates, purely on a documentation gap.
    """
    assert pr.FINDING_CATALOG["HITL-006"]["severity"] == "must-fix"
    assert pr.FINDING_CATALOG["HITL-007"]["severity"] == "should-fix"


# --- HITL-006: idempotency is substantive ---------------------------------

def test_no_section_8_makes_both_not_applicable():
    by = _hitl(_repo())
    assert by["HITL-006"].status == "not-applicable"
    assert by["HITL-007"].status == "not-applicable"


def test_section_8_without_skill_contracts_is_not_verified_never_must_fix():
    """A pilot with no `src/agent/skills/` is not judged, so legacy pilots
    cannot be retroactively broken by this check."""
    by = _hitl(_repo(spec=SEC8))
    assert by["HITL-006"].status == "not-verified"


def test_keyed_no_op_statement_passes():
    root = _repo(spec=SEC8, **_skills(
        disposition="writing the same decision for the same `rma_id` is a no-op."))
    assert _hitl(root)["HITL-006"].status == "pass"


def test_read_only_statement_passes():
    root = _repo(spec=SEC8, **_skills(intake="read-only; safe to re-run."))
    assert _hitl(root)["HITL-006"].status == "pass"


def test_pure_function_statement_passes():
    root = _repo(spec=SEC8, **_skills(policy="pure function of inputs."))
    assert _hitl(root)["HITL-006"].status == "pass"


def test_conditional_write_statement_passes():
    root = _repo(spec=SEC8, **_skills(
        apply="uses an if-not-exists write keyed on the request id."))
    assert _hitl(root)["HITL-006"].status == "pass"


def test_vacuous_statement_is_should_fix():
    root = _repo(spec=SEC8, **_skills(apply="Yes."))
    f = _hitl(root)["HITL-006"]
    assert f.status == "should-fix"
    assert "apply" in f.detail


def test_bare_word_idempotent_is_vacuous():
    """`Idempotency: idempotent` restates the label and attests nothing."""
    root = _repo(spec=SEC8, **_skills(apply="idempotent"))
    assert _hitl(root)["HITL-006"].status == "should-fix"


def test_missing_idempotency_line_is_must_fix():
    root = _repo(spec=SEC8, **{
        "src/agent/skills/apply/SKILL.md":
            "---\nname: apply\n---\n\n## Operational contract\n"
            "- **Inputs**: `rma_id`\n- **Failure behavior**: escalate\n"})
    f = _hitl(root)["HITL-006"]
    assert f.status == "must-fix"
    assert "apply" in f.detail


def test_missing_line_outranks_vacuous_line():
    root = _repo(spec=SEC8, **{
        "src/agent/skills/good/SKILL.md": CONTRACT.format(
            name="good", idem="read-only; safe to re-run."),
        "src/agent/skills/vague/SKILL.md": CONTRACT.format(
            name="vague", idem="Yes."),
        "src/agent/skills/silent/SKILL.md":
            "---\nname: silent\n---\n\n## Operational contract\n- **Inputs**: x\n"})
    assert _hitl(root)["HITL-006"].status == "must-fix"


# --- HITL-007: the resume path is declared --------------------------------

def test_resume_trigger_and_rehydrated_state_passes():
    spec = SEC8 + (
        "- **Resume trigger**: approval webhook posts to the ACA receiver.\n"
        "- **Rehydrated state**: the pending case is reloaded from Cosmos by "
        "`correlation_id`.\n")
    assert _hitl(_repo(spec=spec))["HITL-007"].status == "pass"


def test_section_8_with_neither_is_should_fix():
    spec = ("## 8. Human Interaction Points\n\n"
            "### Review\n- **Actor**: supervisor\n- **Options**: approve, deny\n")
    f = _hitl(_repo(spec=spec))["HITL-007"]
    assert f.status == "should-fix"
    assert "resume trigger" in f.detail.lower()


def test_trigger_without_rehydrated_state_is_should_fix():
    spec = SEC8 + "- **Resume trigger**: approval webhook.\n"
    f = _hitl(_repo(spec=spec))["HITL-007"]
    assert f.status == "should-fix"
    assert "state" in f.detail.lower()


def test_section_9_text_does_not_satisfy_section_8():
    """The check must read section 8 only, or any pilot mentioning Cosmos
    anywhere would pass by accident."""
    spec = (SEC8 + "\n## 9. Evaluation\n\nResumes from Cosmos via a queue "
            "trigger and rehydrates state.\n")
    assert _hitl(_repo(spec=spec))["HITL-007"].status == "should-fix"


# --- regression guards ----------------------------------------------------

def test_existing_hitl_findings_keep_their_statuses():
    """Adding two findings must not disturb HITL-001..005."""
    before = _hitl(_repo())
    for fid in ("HITL-001", "HITL-002", "HITL-003", "HITL-004", "HITL-005"):
        assert fid in before, f"{fid} disappeared"
    assert before["HITL-001"].status == "should-fix"
    assert before["HITL-002"].status == "not-applicable"
    assert before["HITL-003"].status == "must-fix"


def test_shipped_example_contracts_pass_hitl_006():
    """Pins the reference pilot: its four operational contracts are the shape
    the check is calibrated against."""
    contracts = sorted(EXAMPLE.glob("src/agent/skills/*/SKILL.md"))
    assert len(contracts) == 4, "example shape changed; recalibrate this test"
    files = {
        f"src/agent/skills/{p.parent.name}/SKILL.md": p.read_text(encoding="utf-8")
        for p in contracts
    }
    spec = (EXAMPLE / "specs" / "SPEC.md").read_text(encoding="utf-8")
    assert _hitl(_repo(spec=spec, **files))["HITL-006"].status == "pass"


def test_evaluating_does_not_write_to_the_target():
    root = _repo(spec=SEC8, **_skills(apply="read-only; safe to re-run."))
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    _hitl(root)
    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert before == after


if __name__ == "__main__":
    import traceback
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                failures += 1
                print(f"[FAIL] {name}")
                traceback.print_exc()
    print("OK" if not failures else f"{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
