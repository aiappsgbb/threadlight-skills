"""Tests for phase decision + banner (Phase C5-C6).

Run as: python3 skills/threadlight-production-ready/tests/test_phase_decision.py

Covers:
- _phase_decision: pure function mapping (framing, rights_result) → phase2_mode
- _emit_phase_banner: side-effecting banner renderer (writes to sink)
"""
import importlib.util
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


def _framing(rights="full", restricted=False):
    return {
        "provisioning_rights": rights,
        "restricted_environment": restricted,
        "target_subscription_id": "00000000-0000-0000-0000-000000000000",
        "target_resource_group": "rg-test",
        "target_posture": "standard-ai-gateway",
    }


# --- C5: _phase_decision ---------------------------------------------------

def test_full_rights_unrestricted_selfservice():
    rights = {"rights_class": mod.RIGHTS_FULL, "roles": ["Owner"],
              "probe_skipped": False, "error": None}
    d = mod._phase_decision(_framing("full", False), rights)
    assert d["phase2_mode"] == mod.PHASE_SELF_SERVICE


def test_constrained_rights_central_handoff():
    rights = {"rights_class": mod.RIGHTS_CONSTRAINED, "roles": ["Reader"],
              "probe_skipped": False, "error": None}
    d = mod._phase_decision(_framing("constrained", False), rights)
    assert d["phase2_mode"] == mod.PHASE_CENTRAL_HANDOFF


def test_restricted_environment_forces_handoff_even_with_owner():
    rights = {"rights_class": mod.RIGHTS_FULL, "roles": ["Owner"],
              "probe_skipped": False, "error": None}
    d = mod._phase_decision(_framing("full", True), rights)
    assert d["phase2_mode"] == mod.PHASE_CENTRAL_HANDOFF
    assert "restricted environment" in d["reason"].lower()


def test_unknown_rights_warns_and_assumes_constrained():
    rights = {"rights_class": mod.RIGHTS_UNKNOWN, "roles": [],
              "probe_skipped": True, "error": None}
    d = mod._phase_decision(_framing("full", False), rights)
    assert d["phase2_mode"] == mod.PHASE_CENTRAL_HANDOFF
    assert d["warning"] is not None


def test_no_rights_blocked():
    rights = {"rights_class": mod.RIGHTS_NONE, "roles": [],
              "probe_skipped": False, "error": None}
    d = mod._phase_decision(_framing("full", False), rights)
    assert d["phase2_mode"] == mod.PHASE_BLOCKED


# --- C6: _emit_phase_banner ------------------------------------------------

def test_banner_emits_without_error():
    framing = _framing("full", False)
    rights = {"rights_class": mod.RIGHTS_FULL, "roles": ["Owner"],
              "probe_skipped": False, "error": None}
    dec = mod._phase_decision(framing, rights)
    buf = io.StringIO()
    mod._emit_phase_banner(framing, rights, dec, sink=buf)
    text = buf.getvalue()
    assert "self-service" in text
    assert "Owner" in text
    assert "rg-test" in text


def test_banner_includes_warning_when_present():
    framing = _framing("full", False)
    rights = {"rights_class": mod.RIGHTS_UNKNOWN, "roles": [],
              "probe_skipped": True, "error": None}
    dec = mod._phase_decision(framing, rights)
    buf = io.StringIO()
    mod._emit_phase_banner(framing, rights, dec, sink=buf)
    text = buf.getvalue()
    assert "central-team handoff" in text
    assert "warning" in text.lower()


def test_banner_empty_roles_renders_dash():
    framing = _framing("full", False)
    rights = {"rights_class": mod.RIGHTS_NONE, "roles": [],
              "probe_skipped": False, "error": None}
    dec = mod._phase_decision(framing, rights)
    buf = io.StringIO()
    mod._emit_phase_banner(framing, rights, dec, sink=buf)
    text = buf.getvalue()
    assert "—" in text  # em-dash placeholder for empty roles list
    assert "blocked" in text


if __name__ == "__main__":
    failures = []
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                failures.append((name, e))
                print(f"FAIL {name}: {e}", file=sys.stderr)
    if failures:
        sys.exit(1)
    print("OK")
