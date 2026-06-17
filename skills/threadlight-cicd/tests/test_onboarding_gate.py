"""Tests for the onboarding-path decision gate.

The gate runs FIRST, before any artifact is generated. It asks whether a
central platform environment (Citadel hub / shared gateway / networking) is
required and branches into one of three paths:

  1. required + NOT deployed  -> hub-deploy-then-spoke (hand off to citadel-hub-deploy)
  2. required + already exists -> spoke-onboard       (citadel-spoke-onboarding)
  3. not required             -> standalone           (validate the path first)
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "generate_pipeline", ROOT / "scripts" / "generate_pipeline.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["generate_pipeline"] = mod
_spec.loader.exec_module(mod)


def test_required_and_missing_resolves_hub_deploy_then_spoke():
    r = mod.resolve_onboarding_path({
        "central_env_required": True,
        "central_env_exists": False,
    })
    assert r["path"] == "hub-deploy-then-spoke"
    assert r["posture"] == "citadel-spoke"
    assert r["rbac_scope"] == "spoke-rg"
    joined = " ".join(r["next_actions"]).lower()
    assert "citadel-hub-deploy" in joined
    assert "citadel-spoke-onboarding" in joined


def test_required_and_exists_resolves_spoke_onboard():
    r = mod.resolve_onboarding_path({
        "central_env_required": True,
        "central_env_exists": True,
    })
    assert r["path"] == "spoke-onboard"
    assert r["posture"] == "citadel-spoke"
    assert r["rbac_scope"] == "spoke-rg"
    joined = " ".join(r["next_actions"]).lower()
    assert "citadel-spoke-onboarding" in joined
    # must NOT ask the pilot to deploy the hub when it already exists
    assert "citadel-hub-deploy" not in joined


def test_not_required_resolves_standalone_and_demands_validation():
    r = mod.resolve_onboarding_path({
        "central_env_required": False,
        "target_posture": "standard-ai-gateway",
    })
    assert r["path"] == "standalone"
    assert r["rbac_scope"] == "target-rg"
    # the gate must force a validation pass before generating
    assert r["needs_validation"] is True


def test_standalone_defaults_posture_when_unset():
    r = mod.resolve_onboarding_path({"central_env_required": False})
    assert r["path"] == "standalone"
    assert r["posture"] in ("standard-ai-gateway", "agt", "direct")


def test_spoke_paths_never_grant_hub_scope():
    """Boundary invariant: a spoke pilot's RBAC scope is the spoke RG, never
    the hub — regardless of whether the hub already exists."""
    for exists in (True, False):
        r = mod.resolve_onboarding_path({
            "central_env_required": True,
            "central_env_exists": exists,
        })
        assert r["rbac_scope"] == "spoke-rg"
