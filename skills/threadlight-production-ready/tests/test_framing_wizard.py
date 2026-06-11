"""Tests for the v0.4.0 framing wizard.

Loaded as a stdlib module via importlib (no pytest), to match the v0.3.0
convention. Runs with `python3 tests/test_framing_wizard.py`.
"""
import importlib.util
import io
import json
import pathlib
import sys
import tempfile

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "production_ready.py"
_spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
# Register in sys.modules BEFORE exec — Python 3.14 dataclass introspection
# needs the module discoverable via sys.modules[cls.__module__].
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# A1: FRAMING_QUESTIONS canonical list
# ---------------------------------------------------------------------------

def test_framing_questions_have_required_fields():
    qs = mod.FRAMING_QUESTIONS
    assert len(qs) == 8, "v0.5.0 adds azure_tenant_id (#33)"
    for q in qs:
        assert {"id", "prompt", "kind", "required"}.issubset(q.keys())
        assert q["kind"] in {"text", "choice", "bool"}
        if q["kind"] == "choice":
            assert q.get("choices"), f"choice question {q['id']} needs choices"


def test_framing_question_ids_are_canonical():
    ids = {q["id"] for q in mod.FRAMING_QUESTIONS}
    assert ids == {
        "target_subscription_id",
        "target_resource_group",
        "target_posture",
        "provisioning_rights",
        "central_platform_team",
        "restricted_environment",
        "cicd_target",
        "azure_tenant_id",  # v0.5.0 (#33) — runbook substitution
    }


# ---------------------------------------------------------------------------
# A2: run_framing_wizard reads stdin
# ---------------------------------------------------------------------------

def test_wizard_reads_answers_from_stdin():
    fake_in = io.StringIO(
        "00000000-0000-0000-0000-000000000000\n"
        "my-rg\n"
        "citadel-spoke\n"
        "y\n"
        "n\n"
        "n\n"
        "github-actions\n"
        "11111111-1111-1111-1111-111111111111\n"  # azure_tenant_id
    )
    fake_out = io.StringIO()
    answers = mod.run_framing_wizard(istream=fake_in, ostream=fake_out)
    assert answers["target_subscription_id"] == "00000000-0000-0000-0000-000000000000"
    assert answers["target_resource_group"] == "my-rg"
    assert answers["target_posture"] == "citadel-spoke"
    assert answers["provisioning_rights"] is True
    assert answers["central_platform_team"] is False
    assert answers["restricted_environment"] is False
    assert answers["cicd_target"] == "github-actions"
    assert answers["azure_tenant_id"] == "11111111-1111-1111-1111-111111111111"


def test_wizard_rejects_invalid_choice():
    fake_in = io.StringIO(
        "00000000-0000-0000-0000-000000000000\n"
        "my-rg\n"
        "banana\n"          # invalid posture → re-prompt
        "citadel-spoke\n"   # accepted
        "y\n"
        "n\n"
        "n\n"
        "github-actions\n"
        "11111111-1111-1111-1111-111111111111\n"  # azure_tenant_id
    )
    fake_out = io.StringIO()
    answers = mod.run_framing_wizard(istream=fake_in, ostream=fake_out)
    assert answers["target_posture"] == "citadel-spoke"


# ---------------------------------------------------------------------------
# A3: load_framing_file
# ---------------------------------------------------------------------------

def test_framing_file_loader_reads_json():
    payload = {
        "target_subscription_id": "00000000-0000-0000-0000-000000000000",
        "target_resource_group": "my-rg",
        "target_posture": "citadel-spoke",
        "provisioning_rights": True,
        "central_platform_team": False,
        "restricted_environment": True,
        "cicd_target": "github-actions",
        "azure_tenant_id": "11111111-1111-1111-1111-111111111111",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        tmp = pathlib.Path(f.name)
    answers = mod.load_framing_file(tmp)
    assert answers == payload


def test_framing_file_rejects_missing_required():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"target_subscription_id": "x"}, f)
        tmp = pathlib.Path(f.name)
    try:
        mod.load_framing_file(tmp)
        raise AssertionError("should have raised")
    except SystemExit as e:
        assert "missing required" in str(e).lower()


# ---------------------------------------------------------------------------
# A5: args parser accepts --onboard flags
# ---------------------------------------------------------------------------

def test_args_parser_accepts_onboard_flags():
    args = mod._parse_args([
        "--onboard",
        "--framing-file", "/tmp/x.json",
        "--apply-plan-out", "/tmp/apply-plan.json",
        "--scaffold-cicd",
        "--no-rights-probe",
        "--target-sub", "00000000-0000-0000-0000-000000000000",
        "--target-rg", "rg-test",
    ])
    assert args.onboard is True
    assert args.framing_file == "/tmp/x.json"
    assert args.apply_plan_out == "/tmp/apply-plan.json"
    assert args.scaffold_cicd is True
    assert args.no_rights_probe is True
    assert args.target_sub == "00000000-0000-0000-0000-000000000000"
    assert args.target_rg == "rg-test"


# ---------------------------------------------------------------------------
# v0.5.0 — azure_tenant_id (#33)
# ---------------------------------------------------------------------------

def test_framing_questions_includes_azure_tenant_id():
    ids = [q["id"] for q in mod.FRAMING_QUESTIONS]
    assert "azure_tenant_id" in ids, (
        "Wizard must collect tenant ID so runbook <tenant-id> placeholder is filled. See #33."
    )


def test_framing_question_count_is_eight():
    assert len(mod.FRAMING_QUESTIONS) == 8, (
        "v0.5.0 ships 8 framing questions (azure_tenant_id added per #33)."
    )


def test_azure_tenant_id_question_validation():
    q = next(q for q in mod.FRAMING_QUESTIONS if q["id"] == "azure_tenant_id")
    assert q.get("required", True), "azure_tenant_id must be required"
    # Help text or prompt should mention UUID format so operator knows what to enter
    haystack = (q.get("help", "") + " " + q.get("prompt", "")).lower()
    assert "uuid" in haystack, "question should mention UUID format in prompt or help text"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
