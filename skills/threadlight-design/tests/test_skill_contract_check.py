"""Tests for skill_contract_check.py — the generated-skill contract linter.

Every test builds a synthetic pilot repo in a tmp_path. Nothing under
``references/`` or ``examples/`` is written to, so a full run leaves the
checkout clean.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

TEST_DIR = Path(__file__).resolve().parent
SCRIPT = TEST_DIR.parent / "scripts" / "skill_contract_check.py"
REPO_ROOT = TEST_DIR.parents[2]
CHECK_PILOT_CONTRACT_SCRIPT = REPO_ROOT / "scripts" / "ci" / "check_pilot_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("skill_contract_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["skill_contract_check"] = module
    spec.loader.exec_module(module)
    return module


scc = _load_module()


def _load_check_pilot_contract():
    """Load the production `scripts/ci/check_pilot_contract.py` module.

    `VALUE_MODEL_MARKERS` and `extract_section` must come from this one
    source — a hand-copied duplicate here could silently drift from what the
    real design->deploy checker validates against, and the template tests
    below would then be pinning the wrong contract.
    """
    spec = importlib.util.spec_from_file_location(
        "check_pilot_contract", CHECK_PILOT_CONTRACT_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_pilot_contract"] = module
    spec.loader.exec_module(module)
    return module


cpc = _load_check_pilot_contract()


# --------------------------------------------------------------------------
# synthetic pilot builder
# --------------------------------------------------------------------------

SKILL_BODY = """
# {title}

> Implements {brs}.

## Operational contract
- **Inputs**: the case object.
- **Outputs**: a verdict.
- **Deps**: tools {deps}.
- **Idempotency**: pure function of inputs.
- **Failure behavior**: escalate on ambiguity.

## Procedure
1. Do the thing.
"""

SPEC_TMPL = """# SPEC

## 3. Business rules

{rules}
"""

AGENTS_TMPL = """# AGENTS.md

## Available skills

| Skill | Purpose | Implements |
|-------|---------|------------|
{skill_rows}

## Foundry tools required

| Tool | R/W | Backed by | Used by |
|------|-----|-----------|---------|
{tool_rows}
"""


def write_skill(root: Path, directory: str, *, name=None, description=None,
                brs="BR-001", deps="`alpha_read`", body=None):
    name = directory if name is None else name
    if description is None:
        description = (
            f"Handle the {directory} step of the process. "
            f"USE FOR the {directory} stage. "
            f"DO NOT USE FOR anything else (other-skill)."
        )
    target = root / "src" / "agent" / "skills" / directory
    target.mkdir(parents=True, exist_ok=True)
    text = "---\n"
    if name is not None:
        text += f"name: {name}\n"
    text += f"description: {description}\n---\n"
    text += body if body is not None else SKILL_BODY.format(
        title=directory, brs=brs, deps=deps)
    (target / "SKILL.md").write_text(text, encoding="utf-8")


def build_pilot(root: Path, *, brs=("BR-001", "BR-002"),
                tools=(("alpha_read", "intake-stage"), ("beta_write", "decision-stage"))):
    """A minimal, fully conformant two-skill pilot."""
    write_skill(
        root, "intake-stage",
        description=(
            "Correlate the inbound case with its source records. "
            "USE FOR intake of a new case, matching identifiers, completeness checks. "
            "DO NOT USE FOR the terminal outcome (decision-stage)."
        ),
        brs="BR-001", deps="`alpha_read`")
    write_skill(
        root, "decision-stage",
        description=(
            "Emit the terminal outcome and persist the audit record. "
            "USE FOR producing and writing the final decision. "
            "DO NOT USE FOR gathering source records (intake-stage)."
        ),
        brs="BR-002", deps="`beta_write`")

    (root / "specs").mkdir(parents=True, exist_ok=True)
    rules = "\n\n".join(f"### {br}: rule {br}\n\n- Some rule text." for br in brs)
    (root / "specs" / "SPEC.md").write_text(
        SPEC_TMPL.format(rules=rules), encoding="utf-8")

    skill_rows = "\n".join(
        f"| `{n}` | purpose | {b} |" for n, b in
        (("intake-stage", "BR-001"), ("decision-stage", "BR-002")))
    tool_rows = "\n".join(
        f"| `{t}` | R | mock | {u} |" for t, u in tools)
    (root / "AGENTS.md").write_text(
        AGENTS_TMPL.format(skill_rows=skill_rows, tool_rows=tool_rows),
        encoding="utf-8")
    return root


def status_of(root: Path, key: str) -> str:
    return scc.evaluate(str(root))[key]["status"]


def caps_of(root: Path) -> dict:
    return scc.evaluate(str(root))


# --------------------------------------------------------------------------
# frontmatter parsing
# --------------------------------------------------------------------------

def test_frontmatter_single_line():
    data = scc.parse_frontmatter("---\nname: alpha\ndescription: A short one.\n---\nbody")
    assert data["name"] == "alpha"
    assert data["description"] == "A short one."


def test_frontmatter_folded_block():
    text = "---\nname: alpha\ndescription: >\n  first line\n  second line\n---\nbody"
    data = scc.parse_frontmatter(text)
    assert data["description"] == "first line second line"


def test_frontmatter_literal_block():
    text = "---\nname: alpha\ndescription: |\n  first line\n  second line\n---\nbody"
    data = scc.parse_frontmatter(text)
    assert data["description"] == "first line\nsecond line"


def test_frontmatter_strips_quotes():
    data = scc.parse_frontmatter('---\nname: "alpha"\ndescription: \'quoted\'\n---\n')
    assert data["name"] == "alpha"
    assert data["description"] == "quoted"


def test_frontmatter_absent_returns_empty():
    assert scc.parse_frontmatter("# no frontmatter here\n") == {}


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------

def test_clean_pilot_is_sound(tmp_path):
    build_pilot(tmp_path)
    man = scc.manifest(str(tmp_path), scc.evaluate(str(tmp_path)))
    assert man["must_fix"] == [], man["capabilities"]
    assert man["should_fix"] == [], man["capabilities"]
    assert man["verdict"] == "sound"
    assert man["metrics"]["skills"] == 2


def test_manifest_shape(tmp_path):
    build_pilot(tmp_path)
    man = scc.manifest(str(tmp_path), scc.evaluate(str(tmp_path)))
    assert man["schema"] == scc.MANIFEST_SCHEMA
    assert man["tool_version"] == scc.VERSION
    assert set(man["capabilities"]) == set(scc.CAPABILITY_ORDER)
    for key, cap in man["capabilities"].items():
        assert cap["check_id"] == scc.CAPABILITY_IDS[key]
        assert cap["status"] in scc.STATUSES


# --------------------------------------------------------------------------
# SKC-001 .. SKC-012
# --------------------------------------------------------------------------

def test_missing_skills_dir_is_must_fix(tmp_path):
    (tmp_path / "specs").mkdir()
    caps = caps_of(tmp_path)
    assert caps["skills_present"]["status"] == "must-fix"
    # downstream checks must degrade, not crash
    assert caps["name_matches_directory"]["status"] == "not-verified"


def test_unparseable_frontmatter_is_must_fix(tmp_path):
    build_pilot(tmp_path)
    (tmp_path / "src/agent/skills/intake-stage/SKILL.md").write_text(
        "# no frontmatter\n", encoding="utf-8")
    assert status_of(tmp_path, "frontmatter_parseable") == "must-fix"


def test_name_directory_mismatch(tmp_path):
    build_pilot(tmp_path)
    write_skill(tmp_path, "intake-stage", name="intake-validation",
                description=(
                    "Correlate records. USE FOR intake. "
                    "DO NOT USE FOR outcomes (decision-stage)."))
    cap = caps_of(tmp_path)["name_matches_directory"]
    assert cap["status"] == "must-fix"
    assert "intake-validation" in cap["evidence"]


def test_description_over_limit_is_must_fix(tmp_path):
    build_pilot(tmp_path)
    long_desc = (
        "Correlate records. USE FOR intake. "
        "DO NOT USE FOR outcomes (decision-stage). " + "x" * 1100)
    write_skill(tmp_path, "intake-stage", description=long_desc)
    cap = caps_of(tmp_path)["description_within_limit"]
    assert cap["status"] == "must-fix"
    assert str(scc.MAX_DESCRIPTION_CHARS) in cap["evidence"]


def test_description_at_limit_passes(tmp_path):
    build_pilot(tmp_path)
    head = "USE FOR intake. DO NOT USE FOR outcomes (decision-stage). "
    desc = head + "x" * (scc.MAX_DESCRIPTION_CHARS - len(head))
    write_skill(tmp_path, "intake-stage", description=desc)
    assert status_of(tmp_path, "description_within_limit") == "pass"


def test_missing_use_for_is_must_fix(tmp_path):
    build_pilot(tmp_path)
    write_skill(tmp_path, "intake-stage",
                description="Correlate the inbound case with its source records.")
    cap = caps_of(tmp_path)["routing_contract_present"]
    assert cap["status"] == "must-fix"
    assert "intake-stage" in cap["evidence"]


def test_missing_do_not_use_for_is_must_fix(tmp_path):
    build_pilot(tmp_path)
    write_skill(tmp_path, "intake-stage",
                description="Correlate records. USE FOR intake of a new case.")
    assert status_of(tmp_path, "routing_contract_present") == "must-fix"


def test_do_not_use_for_is_not_mistaken_for_use_for(tmp_path):
    """`DO NOT USE FOR` contains the literal `USE FOR` — the parser must not
    treat a description that only has the negative clause as complete."""
    desc = "Correlate records. DO NOT USE FOR outcomes (decision-stage)."
    assert scc.use_for_clause(desc) == ""
    assert scc.do_not_use_for_clause(desc) != ""


def test_dangling_handoff_target_is_must_fix(tmp_path):
    build_pilot(tmp_path)
    write_skill(tmp_path, "intake-stage",
                description=(
                    "Correlate records. USE FOR intake. "
                    "DO NOT USE FOR outcomes (decision-stayge)."))
    cap = caps_of(tmp_path)["handoff_targets_resolve"]
    assert cap["status"] == "must-fix"
    assert "decision-stayge" in cap["evidence"]


def test_handoff_check_ignores_non_parenthesised_compounds(tmp_path):
    """`high-value` outside parentheses is prose, not a handoff pointer."""
    build_pilot(tmp_path)
    write_skill(tmp_path, "intake-stage",
                description=(
                    "Correlate records. USE FOR intake. "
                    "DO NOT USE FOR high-value real-time gating (decision-stage)."))
    assert status_of(tmp_path, "handoff_targets_resolve") == "pass"


def test_handoff_check_not_verified_when_convention_unused(tmp_path):
    """No parenthesised token resolves anywhere: we cannot tell whether the
    pilot uses the convention, so do not fail it."""
    build_pilot(tmp_path)
    for d in ("intake-stage", "decision-stage"):
        write_skill(tmp_path, d,
                    description=f"Do {d}. USE FOR {d}. DO NOT USE FOR other things.")
    assert status_of(tmp_path, "handoff_targets_resolve") == "not-verified"


def test_routing_overlap_is_should_fix(tmp_path):
    build_pilot(tmp_path)
    shared = "USE FOR triaging an inbound refund return case for a retail customer."
    write_skill(tmp_path, "intake-stage",
                description=f"Alpha. {shared} DO NOT USE FOR x (decision-stage).")
    write_skill(tmp_path, "decision-stage",
                description=f"Beta. {shared} DO NOT USE FOR y (intake-stage).")
    cap = caps_of(tmp_path)["routing_overlap_clear"]
    assert cap["status"] == "should-fix"
    assert "intake-stage" in cap["evidence"] and "decision-stage" in cap["evidence"]


def test_distinct_routing_passes(tmp_path):
    build_pilot(tmp_path)
    assert status_of(tmp_path, "routing_overlap_clear") == "pass"


def test_incomplete_operational_contract_is_should_fix(tmp_path):
    build_pilot(tmp_path)
    body = "# intake\n\n> Implements BR-001.\n\n## Operational contract\n- **Inputs**: x.\n- **Outputs**: y.\n"
    write_skill(tmp_path, "intake-stage", body=body,
                description=("Correlate records. USE FOR intake. "
                             "DO NOT USE FOR outcomes (decision-stage)."))
    cap = caps_of(tmp_path)["operational_contract_complete"]
    assert cap["status"] == "should-fix"
    assert "idempotency" in cap["evidence"].lower()


def test_british_spelling_of_failure_behaviour_accepted(tmp_path):
    build_pilot(tmp_path)
    body = SKILL_BODY.format(title="intake-stage", brs="BR-001", deps="`alpha_read`")
    body = body.replace("**Failure behavior**", "**Failure behaviour**")
    write_skill(tmp_path, "intake-stage", body=body,
                description=("Correlate records. USE FOR intake. "
                             "DO NOT USE FOR outcomes (decision-stage)."))
    assert status_of(tmp_path, "operational_contract_complete") == "pass"


def test_undeclared_tool_dep_is_must_fix(tmp_path):
    build_pilot(tmp_path)
    write_skill(tmp_path, "intake-stage", deps="`ghost_tool`",
                description=("Correlate records. USE FOR intake. "
                             "DO NOT USE FOR outcomes (decision-stage)."))
    cap = caps_of(tmp_path)["tool_deps_declared"]
    assert cap["status"] == "must-fix"
    assert "ghost_tool" in cap["evidence"]


def test_tool_deps_not_verified_without_agents_md(tmp_path):
    build_pilot(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    assert status_of(tmp_path, "tool_deps_declared") == "not-verified"


def test_uncovered_br_is_should_fix(tmp_path):
    build_pilot(tmp_path, brs=("BR-001", "BR-002", "BR-003"))
    cap = caps_of(tmp_path)["br_coverage_complete"]
    assert cap["status"] == "should-fix"
    assert "BR-003" in cap["evidence"]


def test_dangling_br_reference_is_must_fix(tmp_path):
    build_pilot(tmp_path)
    write_skill(tmp_path, "intake-stage", brs="BR-009",
                description=("Correlate records. USE FOR intake. "
                             "DO NOT USE FOR outcomes (decision-stage)."))
    cap = caps_of(tmp_path)["br_references_resolve"]
    assert cap["status"] == "must-fix"
    assert "BR-009" in cap["evidence"]


def test_br_checks_not_verified_without_spec(tmp_path):
    build_pilot(tmp_path)
    (tmp_path / "specs" / "SPEC.md").unlink()
    caps = caps_of(tmp_path)
    assert caps["br_coverage_complete"]["status"] == "not-verified"
    assert caps["br_references_resolve"]["status"] == "not-verified"


def test_orphan_skill_is_should_fix(tmp_path):
    build_pilot(tmp_path)
    write_skill(tmp_path, "orphan-stage", brs="BR-001",
                description=("Do orphan work. USE FOR an unrelated tangent. "
                             "DO NOT USE FOR intake (intake-stage)."))
    cap = caps_of(tmp_path)["skills_registered"]
    assert cap["status"] == "should-fix"
    assert "orphan-stage" in cap["evidence"]


def test_agents_table_listing_a_missing_skill_is_must_fix(tmp_path):
    build_pilot(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    text = text.replace("| `decision-stage` |", "| `decision-stagge` |")
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8")
    cap = caps_of(tmp_path)["skills_registered"]
    assert cap["status"] == "must-fix"
    assert "decision-stagge" in cap["evidence"]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_exit_zero_on_clean_pilot(tmp_path, capsys):
    build_pilot(tmp_path)
    assert scc.main(["--target", str(tmp_path)]) == 0
    assert "SOUND" in capsys.readouterr().out


def test_cli_gate_exits_two_on_must_fix(tmp_path, capsys):
    build_pilot(tmp_path)
    write_skill(tmp_path, "intake-stage", name="wrong-name",
                description=("Correlate records. USE FOR intake. "
                             "DO NOT USE FOR outcomes (decision-stage)."))
    assert scc.main(["--target", str(tmp_path), "--gate"]) == 2


def test_cli_gate_ignores_should_fix(tmp_path):
    build_pilot(tmp_path, brs=("BR-001", "BR-002", "BR-003"))
    assert scc.main(["--target", str(tmp_path), "--gate"]) == 0


def test_cli_json_is_parseable(tmp_path, capsys):
    build_pilot(tmp_path)
    scc.main(["--target", str(tmp_path), "--json"])
    man = json.loads(capsys.readouterr().out)
    assert man["schema"] == scc.MANIFEST_SCHEMA


def test_cli_emit_writes_artifacts(tmp_path):
    build_pilot(tmp_path)
    scc.main(["--target", str(tmp_path), "--emit"])
    man_path = tmp_path / "specs" / "skill-contract-manifest.json"
    report_path = tmp_path / "docs" / "skill-contract-report.md"
    assert man_path.is_file() and report_path.is_file()
    assert json.loads(man_path.read_text(encoding="utf-8"))["verdict"] == "sound"
    assert "Skill contract" in report_path.read_text(encoding="utf-8")


def test_cli_survives_a_nonexistent_target(tmp_path, capsys):
    rc = scc.main(["--target", str(tmp_path / "nope")])
    assert rc == 0
    assert "UNSOUND" in capsys.readouterr().out


# --------------------------------------------------------------------------
# repo invariants
# --------------------------------------------------------------------------

def test_version_matches_skill_md_metadata():
    text = (TEST_DIR.parent / "SKILL.md").read_text(encoding="utf-8")
    declared = scc.parse_frontmatter(text)
    assert declared.get("metadata.version") == scc.VERSION, (
        "SKILL.md metadata.version must equal skill_contract_check.VERSION")


def test_shipped_example_pilot_is_sound():
    """The committed example is the reference implementation of the contract."""
    example = REPO_ROOT / "examples" / "returns-triage-governed"
    if not example.is_dir():
        pytest.skip("example pilot not present")
    man = scc.manifest(str(example), scc.evaluate(str(example)))
    assert man["must_fix"] == [], json.dumps(man["capabilities"], indent=2)


def test_evaluate_does_not_write_to_the_target(tmp_path):
    build_pilot(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    scc.evaluate(str(tmp_path))
    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


def test_speckit_template_declares_section_14_value_model():
    """The canonical SpecKit template must ship the § 14 value-model contract.

    This pins the template shape that `scripts/ci/check_pilot_contract.py`'s
    `VALUE_MODEL_MARKERS` validates against, and the schema documented in
    `references/value-model-schema.md`. The template is a *blank* contract —
    every numeric/decision leaf must stay a bare YAML key (a comment, not a
    value) so no pilot inherits an invented number. `VALUE_MODEL_MARKERS` and
    the section boundary are loaded from the production checker itself
    (`cpc`), not re-typed here, and every assertion below is scoped to the
    *extracted* § 14 body — never the whole template file, where a stray
    match elsewhere (e.g. in § 12's prose) could pass by accident.
    """
    template = (
        TEST_DIR.parent / "references" / "speckit-template.md"
    ).read_text(encoding="utf-8")

    # Exactly one § 14 heading, and no § 15 — § 14 must stay the template's
    # last section. `extract_section`'s boundary is "the next heading whose
    # leading integer is strictly greater than 14"; a second `## 14.` or a
    # stray `## 15.` would silently change what "all of § 14" means.
    assert template.count("## 14. Value Model") == 1
    assert not re.search(r"^##[ \t]+15\.", template, re.MULTILINE)

    section = cpc.extract_section(template, 14)
    assert section is not None, "check_pilot_contract.extract_section must find § 14"

    # Structural markers the design->deploy checker keys off, loaded from the
    # production module rather than a hand-copied duplicate.
    for marker in cpc.VALUE_MODEL_MARKERS:
        assert re.search(rf"^[ \t]*{re.escape(marker)}", section, re.MULTILINE), (
            f"section 14 must declare `{marker}` as a live (non-comment) key"
        )

    required_fields = (
        "min_complete_days",
        "min_successful_interactions",
        "min_cost_settlement_age_hours",
        "max_window_end_age_days",
        "min_projection_attribution_coverage_pct",
        "name",
        "trace_attribute",
        "success_values",
        "target_cost_per_successful_interaction_usd",
        "max_forecast_variance_pct",
        "max_token_volume_variance_pct",
        "actual_cost_basis",
        "actual_billing_price_basis",
        "forecast_price_basis",
        "allow_basis_mismatch_for_verdict",
        "scope_policy",
    )
    for field in required_fields:
        assert field in section, f"section 14 must declare field `{field}`"

    # The template is a *blank* shape: every numeric/decision leaf is a bare
    # `key:` (optionally followed only by a trailing comment), never a
    # populated value like `7` or `0.95` copied from the reference example.
    # This covers every leaf EXCEPT `actual_cost_basis`, the sole fixed
    # literal (checked separately below).
    blank_fields = (
        "min_complete_days",
        "min_successful_interactions",
        "min_cost_settlement_age_hours",
        "max_window_end_age_days",
        "min_projection_attribution_coverage_pct",
        "name",
        "trace_attribute",
        "success_values",
        "target_cost_per_successful_interaction_usd",
        "max_forecast_variance_pct",
        "max_token_volume_variance_pct",
        "actual_billing_price_basis",
        "forecast_price_basis",
        "allow_basis_mismatch_for_verdict",
        "scope_policy",
    )
    for line in section.splitlines():
        stripped = line.strip()
        for field in blank_fields:
            if not stripped.startswith(f"{field}:"):
                continue
            remainder = stripped[len(f"{field}:"):].split("#", 1)[0].strip()
            # `success_values` is a list leaf; its blank form is `[]`, not a
            # bare key, but it must never carry actual entries.
            allowed = {"[]"} if field == "success_values" else {""}
            assert remainder in allowed, (
                f"template field `{field}` must stay blank (comment only, "
                f"no invented value), found: {stripped!r}"
            )

    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("actual_cost_basis:"):
            remainder = stripped[len("actual_cost_basis:"):].split("#", 1)[0].strip()
            assert remainder == "usage-pretax", (
                "`actual_cost_basis` must be the fixed literal `usage-pretax`, "
                f"found: {stripped!r}"
            )
            break
    else:
        pytest.fail("section 14 must declare `actual_cost_basis:`")


def test_speckit_template_variance_thresholds_are_fractional_with_no_default():
    """`max_forecast_variance_pct` / `max_token_volume_variance_pct` are
    fractional floats in [0, 1] (`0.20` == 20%) — never a bare percentage
    integer, and never pre-filled with a default value.
    """
    template = (
        TEST_DIR.parent / "references" / "speckit-template.md"
    ).read_text(encoding="utf-8")
    section = cpc.extract_section(template, 14)
    assert section is not None

    for field in ("max_forecast_variance_pct", "max_token_volume_variance_pct"):
        match = re.search(rf"^[ \t]*{field}:[^\n]*$", section, re.MULTILINE)
        assert match, f"section 14 must declare `{field}`"
        line = match.group(0)
        value_part, _, comment = line.partition("#")
        assert value_part.split(":", 1)[1].strip() == "", (
            f"`{field}` must not ship a default value, found: {line!r}"
        )
        assert "fractional" in comment.lower(), (
            f"`{field}` comment must state the value is fractional, found: {line!r}"
        )
        assert "[0, 1]" in comment, (
            f"`{field}` comment must state the [0, 1] bound, found: {line!r}"
        )


def test_speckit_template_target_cpi_is_explicit_usd_definition():
    """`target_cost_per_successful_interaction_usd` must be documented as a
    USD amount per successful interaction, strictly greater than 0."""
    template = (
        TEST_DIR.parent / "references" / "speckit-template.md"
    ).read_text(encoding="utf-8")
    section = cpc.extract_section(template, 14)
    assert section is not None

    match = re.search(
        r"^[ \t]*target_cost_per_successful_interaction_usd:[^\n]*"
        r"(?:\n[ \t]+#[^\n]*)*",
        section,
        re.MULTILINE,
    )
    assert match, "section 14 must declare `target_cost_per_successful_interaction_usd`"
    comment = match.group(0).lower()
    assert "usd" in comment
    assert "successful interaction" in comment
    assert "> 0" in comment


def test_fast_poc_callout_verbatim_and_value_model_row_present():
    """The checker's `FAST_POC_MARKERS` key off an exact § 13 callout string
    — it must stay untouched — but an actionable § 14 captured-context row
    must immediately follow it so the audit trail also covers the value
    model, not just § 1 / § 11f.
    """
    template = (
        TEST_DIR.parent / "references" / "speckit-template.md"
    ).read_text(encoding="utf-8")
    # Mirror how this text renders in a real generated SPEC: the template's
    # leading `> ` blockquote markers are authoring annotations, not part of
    # the emitted content, and the callout wraps across several such lines.
    stripped_lines = [
        line[2:] if line.startswith("> ") else line
        for line in template.splitlines()
    ]
    flat = " ".join(" ".join(stripped_lines).split())

    verbatim_callout = (
        "_Fast-PoC mode: audience mode, customer context, brand, and "
        "production posture were not collected; using neutral demo "
        "defaults. Override later in SPEC § 1 / § 11f / § 13._"
    )
    assert verbatim_callout in flat, "the mandatory Fast-PoC § 13 callout must stay verbatim"

    row = "| § 14.value_model | not collected | open-question | no | yes |"
    assert row in flat, "§ 14's captured-context row must be present and literal"

    callout_idx = flat.index(verbatim_callout)
    row_idx = flat.index(row)
    assert row_idx > callout_idx, "the § 14 row must immediately follow the Fast-PoC callout"


def test_success_event_is_unambiguous_across_template_and_schema():
    """`success_event` must read the same way everywhere: operator-confirmed
    in Full mode, blank/open-question in Fast-PoC, and never derived or
    invented — in the template, the schema, and the final-review checklist.
    """
    template = (
        TEST_DIR.parent / "references" / "speckit-template.md"
    ).read_text(encoding="utf-8")
    schema = (
        TEST_DIR.parent / "references" / "value-model-schema.md"
    ).read_text(encoding="utf-8")
    skill = (TEST_DIR.parent / "SKILL.md").read_text(encoding="utf-8")

    assert "operator-confirmed" in template.lower()
    assert "success_event" in schema
    assert "operator-confirmed" in schema.lower()

    review_line = next(
        line for line in skill.splitlines()
        if "SPEC § 14 Value Model has the correct" in line
    )
    idx = review_line.index("No numeric or decision field was invented")
    tail = review_line[idx:]
    assert "success_event" in tail, (
        "the final-review checklist's invented-value ban must name "
        "`success_event` explicitly, not just maturity_policy/baseline/accounting"
    )


def test_value_model_schema_defines_fractional_variance_and_explicit_cpi():
    schema = (
        TEST_DIR.parent / "references" / "value-model-schema.md"
    ).read_text(encoding="utf-8")
    lower = schema.lower()

    assert "fractional" in lower
    assert "[0, 1]" in schema
    assert "invalid" in lower
    assert "usd per successful interaction" in lower


def test_skill_md_documents_section_14_consistently():
    """Stale § 14 prose in SKILL.md: a template section-count that predates
    §§ 13-14, § 14 missing from the input-contract enumeration, and
    `check_pilot_contract.py` read as a plugin-local runtime dependency
    instead of repo-side CI.
    """
    skill = (TEST_DIR.parent / "SKILL.md").read_text(encoding="utf-8")

    ref_line = next(
        line for line in skill.splitlines()
        if line.startswith("| `references/speckit-template.md`")
    )
    assert "12 sections" not in ref_line, f"stale section count: {ref_line!r}"
    assert re.search(r"(?<!\d)14(?!\d)", ref_line), f"section count must include § 14: {ref_line!r}"

    match = re.search(
        r"Sections\s+\*\*(.+?)\*\*\s+are\s+\*\*input contracts\*\*", skill, re.DOTALL
    )
    assert match, "input-contract enumeration paragraph not found"
    enumeration = match.group(1)
    assert re.search(r"(?<!\d)14(?!\d)", enumeration), (
        f"§ 14 must be named in the input-contract enumeration, found: {enumeration!r}"
    )

    assert "repo-side CI" in skill or "repo's own CI" in skill, (
        "`check_pilot_contract.py` must be qualified as repo-side CI, not a "
        "plugin-local runtime dependency of this skill"
    )
