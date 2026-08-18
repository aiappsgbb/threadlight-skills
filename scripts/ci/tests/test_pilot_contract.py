"""Tests for the design -> deploy pilot contract checker.

Two jobs, in order of importance:

1. **Prove the checker can fail.** A guard that cannot fail is worse than no
   guard, because it produces a green check that lies. Every rule id the
   checker can emit has a test that injects the corresponding violation into
   an otherwise-valid pilot and asserts that exact rule fires.

2. **Run the real contract against the shipped reference pilot** on every PR.
   `examples/returns-triage-governed` is what a reader is told to copy. If the
   contract drifts away from it, that is a documentation bug we want to catch
   for free, not six weeks later in a $1 workflow run.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "check_pilot_contract.py"
EXAMPLE = REPO_ROOT / "examples" / "returns-triage-governed"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "threadlight-e2e-foundry.yml"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_pilot_contract", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_pilot_contract"] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()

# Markers required inside SPEC section 14, kept as one source of truth so
# every test that removes one to prove it is enforced uses the exact same
# list the checker itself validates against.
VALUE_MODEL_MARKERS = (
    "value_model:",
    "maturity_policy:",
    "success_event:",
    "baseline:",
    "accounting:",
)

# The single canonical, blank (no-default) section 14 body. Every test that
# rewrites SPEC.md and needs a *valid* section 14 appends this exact text —
# never a hand-copied variant — so a marker rename only has to happen here.
VALUE_MODEL_BLOCK = (
    "## 14. Value Model\n\n"
    "```yaml\n"
    "value_model:\n"
    "  cost:\n"
    "    maturity_policy:\n"
    "      min_complete_days:\n"
    "      min_successful_interactions:\n"
    "      min_cost_settlement_age_hours:\n"
    "      max_window_end_age_days:\n"
    "      min_projection_attribution_coverage_pct:\n"
    "    success_event:\n"
    "      name:\n"
    "      trace_attribute:\n"
    "      success_values: []\n"
    "    baseline:\n"
    "      target_cost_per_successful_interaction_usd:\n"
    "      max_forecast_variance_pct:\n"
    "      max_token_volume_variance_pct:\n"
    "    accounting:\n"
    "      actual_cost_basis:\n"
    "      actual_billing_price_basis:\n"
    "      forecast_price_basis:\n"
    "      allow_basis_mismatch_for_verdict:\n"
    "      scope_policy:\n"
    "```\n"
)


def rules(failures) -> set[str]:
    return {rule for rule, _ in failures.items}


@pytest.fixture
def pilot(tmp_path: Path) -> Path:
    """A minimal but fully valid pilot, built from scratch.

    Deliberately not a copy of the shipped example: these tests must keep
    working when the example changes, and a hand-built fixture makes each
    injected violation obvious.
    """
    root = tmp_path / "pilot"
    (root / "specs" / "sample-data").mkdir(parents=True)
    (root / "tests").mkdir()

    (root / "specs" / "SPEC.md").write_text(
        "# SPEC\n\n"
        "## 1. Process Overview\nStuff.\n\n"
        "## 12. Production Readiness\nStuff.\n\n"
        "## 13. Assumptions & Open Questions\n"
        "Ran in Fast-PoC mode: audience mode, customer context, brand and\n"
        "production posture were not collected; neutral demo defaults applied.\n\n"
        f"{VALUE_MODEL_BLOCK}",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Agents\nContent.\n", encoding="utf-8")
    (root / "tests" / "killer-prompts.md").write_text(
        "# Prompts\n1. Do a thing.\n", encoding="utf-8"
    )
    (root / "specs" / "sample-data" / "orders.json").write_text(
        json.dumps([{"id": "A-1", "total": 42.5}], indent=2), encoding="utf-8"
    )
    (root / "specs" / "deployment-posture.md").write_text(
        "# posture\ndeployment_target: demo-sandbox\nsource: interactive\n", encoding="utf-8"
    )
    (root / ".env.local").write_text(
        "LLM_BACKEND=aoai\n"
        "AZURE_OPENAI_ENDPOINT=https://example.openai.azure.com/\n"
        "AZURE_OPENAI_DEPLOYMENT=gpt-4o\n",
        encoding="utf-8",
    )
    return root


ALL_STAGES = ["design", "pattern0", "deploy"]


def check(
    pilot: Path,
    *,
    profile: str = "fast-poc",
    target: str | None = None,
    require_value_model: bool = False,
):
    return mod.run_checks(
        pilot, ALL_STAGES, profile, target, require_value_model=require_value_model
    )


# -- the happy path ----------------------------------------------------------

def test_valid_pilot_passes_every_stage(pilot: Path) -> None:
    assert not check(pilot), rules(check(pilot))


def test_shipped_example_satisfies_the_contract() -> None:
    """The reference pilot a reader is told to copy must actually be valid.

    `.env.local` is gitignored (it holds a live endpoint), so the pattern0
    stage is deliberately not checked here.
    """
    failures = mod.run_checks(
        EXAMPLE, ["design", "deploy"], "governed", "customer-pilot",
        require_value_model=True,
    )
    assert not failures, rules(failures)


# -- every rule must be reachable --------------------------------------------

def test_missing_pilot_dir_is_reported(tmp_path: Path) -> None:
    failures = check(tmp_path / "nope")
    assert rules(failures) == {"pilot.missing"}


@pytest.mark.parametrize(
    "relpath,stage",
    [
        ("specs/SPEC.md", "design"),
        ("AGENTS.md", "design"),
        ("tests/killer-prompts.md", "design"),
        (".env.local", "pattern0"),
        ("specs/deployment-posture.md", "deploy"),
    ],
)
def test_each_required_file_is_enforced(pilot: Path, relpath: str, stage: str) -> None:
    (pilot / relpath).unlink()
    assert f"{stage}.file.missing" in rules(check(pilot))


def test_empty_required_file_counts_as_missing(pilot: Path) -> None:
    (pilot / "AGENTS.md").write_text("", encoding="utf-8")
    assert "design.file.missing" in rules(check(pilot))


def test_sample_data_dir_must_exist(pilot: Path) -> None:
    shutil.rmtree(pilot / "specs" / "sample-data")
    assert "design.sample-data.missing" in rules(check(pilot))


def test_malformed_sample_json_is_rejected(pilot: Path) -> None:
    """The bash original only checked file size, so broken JSON sailed through."""
    (pilot / "specs" / "sample-data" / "orders.json").write_text(
        '{"id": "A-1", "total": 42.5,,}', encoding="utf-8"
    )
    assert "design.sample-data.invalid-json" in rules(check(pilot))


def test_trivial_sample_json_is_rejected(pilot: Path) -> None:
    (pilot / "specs" / "sample-data" / "orders.json").write_text("[]", encoding="utf-8")
    assert "design.sample-data.empty" in rules(check(pilot))


def test_missing_spec_section_13_is_rejected(pilot: Path) -> None:
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    head = text.split("## 13.")[0]
    # Section 14 is present and well-formed here; section 13 is genuinely
    # absent. The generic extractor must still return None for section 13,
    # not silently backfill it from the sibling section 14 body.
    spec.write_text(f"{head}{VALUE_MODEL_BLOCK}", encoding="utf-8")
    assert "design.spec.no-section-13" in rules(check(pilot))


@pytest.mark.parametrize(
    "section13",
    [
        "Nothing notable here.",
        "Ran in Fast-PoC mode.",
        "Context was not collected.",
    ],
)
def test_fast_poc_callout_needs_all_markers(pilot: Path, section13: str) -> None:
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    head = text.split("## 13.")[0]
    spec.write_text(
        f"{head}## 13. Assumptions & Open Questions\n{section13}\n\n{VALUE_MODEL_BLOCK}",
        encoding="utf-8",
    )
    assert "design.spec.fast-poc-callout" in rules(check(pilot))


def test_governed_profile_does_not_demand_the_fast_poc_callout(pilot: Path) -> None:
    """A governed pilot collected that context for real; the callout would be a lie."""
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    head = text.split("## 13.")[0]
    spec.write_text(
        f"{head}## 13. Assumptions & Open Questions\nNone outstanding.\n\n{VALUE_MODEL_BLOCK}",
        encoding="utf-8",
    )
    assert not check(pilot, profile="governed")


def test_section_13_extraction_stops_at_section_15(pilot: Path) -> None:
    """A Fast-PoC-looking phrase in a later section must not satisfy the
    section 13 callout. Section 14 is now the real, mandatory Value Model
    section, so this boundary probe uses section 15 instead."""
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    head = text.split("## 13.")[0]
    spec.write_text(
        f"{head}"
        "## 13. Assumptions & Open Questions\nNone.\n\n"
        f"{VALUE_MODEL_BLOCK}\n"
        "## 15. Appendix\nFast-PoC: context was not collected, neutral defaults applied.\n",
        encoding="utf-8",
    )
    assert "design.spec.fast-poc-callout" in rules(check(pilot))


def test_section_13_extraction_keeps_lettered_subsections(pilot: Path) -> None:
    """`## 13b.` belongs to section 13 and must stay inside the captured region."""
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    head = text.split("## 13.")[0]
    spec.write_text(
        f"{head}"
        "## 13. Assumptions & Open Questions\nSee below.\n\n"
        "## 13b. Silent defaults\n"
        "Fast-PoC: context was not collected, neutral defaults applied.\n\n"
        f"{VALUE_MODEL_BLOCK}",
        encoding="utf-8",
    )
    assert not check(pilot)


# -- section 14 (Value Model): opt-in enforcement ----------------------------

def test_missing_spec_section_14_is_rejected_only_when_required(pilot: Path) -> None:
    # The `pilot` fixture already carries exactly one `VALUE_MODEL_BLOCK`. Do
    # NOT append another copy here: with two `## 14.` headings present, the
    # corrected extractor stops only at a heading whose leading integer is
    # strictly greater than 14, so a second `## 14.` would not terminate the
    # first one's extraction and this test would start truncating the wrong
    # thing. `spec_text.count(...) == 1` guards that invariant before every
    # mutation in this file.
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    spec.write_text(text.split("## 14.")[0], encoding="utf-8")
    assert "design.spec.no-section-14" in rules(check(pilot, require_value_model=True))


def test_legacy_pilot_without_section_14_still_passes_by_default(pilot: Path) -> None:
    """Enforcement is opt-in: a pilot authored before this design must keep
    passing unchanged unless the caller explicitly asks for the new contract.
    """
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    spec.write_text(text.split("## 14.")[0], encoding="utf-8")
    assert "design.spec.no-section-14" not in rules(check(pilot))


@pytest.mark.parametrize("marker", VALUE_MODEL_MARKERS)
def test_section_14_requires_value_model_shape(pilot: Path, marker: str) -> None:
    # Same reasoning as above: appending a second `VALUE_MODEL_BLOCK` here
    # would leave an intact, unmodified copy of `marker` sitting right after
    # the mutated one, and the extractor would fold both into one section 14
    # body — silently backfilling the marker this test just removed and
    # defeating the assertion below without ever failing loudly.
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    spec.write_text(text.replace(marker, f"# removed {marker}", 1), encoding="utf-8")
    assert "design.spec.value-model-shape" in rules(check(pilot, require_value_model=True))


@pytest.mark.parametrize("marker", VALUE_MODEL_MARKERS)
def test_present_but_malformed_section_14_fails_without_the_flag(
    pilot: Path, marker: str
) -> None:
    """Opt-in applies to *absence* only. A pilot that ships a half-written
    section 14 is asserting the new contract and is validated by default."""
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    spec.write_text(text.replace(marker, f"# removed {marker}", 1), encoding="utf-8")
    assert "design.spec.value-model-shape" in rules(check(pilot, require_value_model=False))


def test_section_14_does_not_require_numeric_defaults(pilot: Path) -> None:
    spec = pilot / "specs" / "SPEC.md"
    assert spec.read_text(encoding="utf-8").count("## 14. Value Model") == 1
    failures = check(pilot, require_value_model=True)
    assert "design.spec.value-model-shape" not in rules(failures)


def test_extract_section_ignores_an_out_of_order_lower_numbered_heading() -> None:
    """A stray `## 12.` inside section 13's body must not truncate it — only
    a heading numbered strictly greater than 13 is a boundary."""
    text = (
        "## 13. Assumptions\n"
        "Body line one.\n\n"
        "## 12. Stray heading from a bad merge\n"
        "Still section 13's body.\n\n"
        "## 14. Next section\n"
        "Not part of section 13.\n"
    )
    section13 = mod.extract_section(text, 13)
    assert section13 is not None
    assert "## 12. Stray heading" in section13
    assert "Still section 13's body." in section13
    assert "Not part of section 13." not in section13


def test_extract_section_stops_at_the_next_strictly_greater_heading() -> None:
    text = (
        "## 13. Assumptions\n"
        "Body line one.\n\n"
        "## 14. Next section\n"
        "Not part of section 13.\n"
    )
    section13 = mod.extract_section(text, 13)
    assert section13 is not None
    assert "Body line one." in section13
    assert "Not part of section 13." not in section13


@pytest.mark.parametrize(
    "token", ["<your-aoai-resource>", "<your-deployment-name>", "<placeholder>"]
)
def test_unresolved_env_placeholders_are_rejected(pilot: Path, token: str) -> None:
    env = pilot / ".env.local"
    env.write_text(env.read_text(encoding="utf-8") + f"SOMETHING={token}\n", encoding="utf-8")
    assert "pattern0.env.placeholder" in rules(check(pilot))


def test_env_requires_exact_backend_line(pilot: Path) -> None:
    (pilot / ".env.local").write_text(
        "LLM_BACKEND=openai\nAZURE_OPENAI_ENDPOINT=https://x.openai.azure.com/\n"
        "AZURE_OPENAI_DEPLOYMENT=gpt-4o\n",
        encoding="utf-8",
    )
    assert "pattern0.env.backend" in rules(check(pilot))


def test_env_endpoint_must_be_a_url(pilot: Path) -> None:
    (pilot / ".env.local").write_text(
        "LLM_BACKEND=aoai\nAZURE_OPENAI_ENDPOINT=\nAZURE_OPENAI_DEPLOYMENT=gpt-4o\n",
        encoding="utf-8",
    )
    assert "pattern0.env.endpoint" in rules(check(pilot))


def test_env_deployment_must_be_non_empty(pilot: Path) -> None:
    (pilot / ".env.local").write_text(
        "LLM_BACKEND=aoai\nAZURE_OPENAI_ENDPOINT=https://x.openai.azure.com/\n"
        "AZURE_OPENAI_DEPLOYMENT=\n",
        encoding="utf-8",
    )
    assert "pattern0.env.deployment" in rules(check(pilot))


def test_posture_without_a_target_is_rejected(pilot: Path) -> None:
    (pilot / "specs" / "deployment-posture.md").write_text(
        "# posture\nsource: interactive\n", encoding="utf-8"
    )
    assert "deploy.posture.no-target" in rules(check(pilot))


def test_unknown_posture_target_is_rejected(pilot: Path) -> None:
    (pilot / "specs" / "deployment-posture.md").write_text(
        "deployment_target: yolo-prod\n", encoding="utf-8"
    )
    assert "deploy.posture.unknown-target" in rules(check(pilot))


def test_posture_target_mismatch_is_rejected(pilot: Path) -> None:
    assert "deploy.posture.wrong-target" in rules(check(pilot, target="production-bound"))


def test_posture_target_match_passes(pilot: Path) -> None:
    assert not check(pilot, target="demo-sandbox")


# -- keep the checker honest about what the workflow expects -----------------

def test_every_contract_file_is_still_named_by_the_e2e_workflow() -> None:
    """Guard against the checker and the E2E asserts drifting apart.

    The workflow's inline bash is still the thing that runs against a freshly
    generated pilot. If someone changes what it demands without teaching this
    checker, the free PR-time gate silently stops matching the paid one.
    """
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    missing = [
        rel
        for files in mod.REQUIRED_FILES.values()
        for rel in files
        if rel not in workflow_text
    ]
    assert not missing, (
        f"these contract artifacts are no longer referenced in {WORKFLOW.name}: {missing}"
    )


def test_valid_deployment_targets_match_the_posture_vocabulary() -> None:
    assert set(mod.VALID_DEPLOYMENT_TARGETS) == {
        "demo-sandbox",
        "customer-pilot",
        "production-bound",
    }
