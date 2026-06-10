"""Tests for rights probe (Phase C2-C4).

Run as: python3 skills/threadlight-production-ready/tests/test_rights_probe.py

Covers:
- _classify_rights: pure role-list classifier
- _probe_provisioning_rights: live RBAC probe with mocked subprocess
- --no-rights-probe short-circuit via skip=True
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
from unittest.mock import patch, MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


def _fake_run(returncode=0, stdout="[]", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# --- C2: _classify_rights ---------------------------------------------------

def test_full_when_owner():
    assert mod._classify_rights(["Owner"]) == mod.RIGHTS_FULL


def test_full_when_contributor():
    assert mod._classify_rights(["Contributor"]) == mod.RIGHTS_FULL


def test_full_when_uaa():
    assert mod._classify_rights(["User Access Administrator"]) == mod.RIGHTS_FULL


def test_constrained_when_only_reader():
    assert mod._classify_rights(["Reader"]) == mod.RIGHTS_CONSTRAINED


def test_constrained_when_only_monitoring_reader():
    assert mod._classify_rights(["Monitoring Reader"]) == mod.RIGHTS_CONSTRAINED


def test_none_when_empty():
    assert mod._classify_rights([]) == mod.RIGHTS_NONE


def test_full_wins_over_reader():
    assert mod._classify_rights(["Reader", "Contributor"]) == mod.RIGHTS_FULL


# --- C3: _probe_provisioning_rights ----------------------------------------

def test_probe_owner():
    fake = _fake_run(stdout=json.dumps([{"roleDefinitionName": "Owner"}]))
    with patch.object(subprocess, "run", return_value=fake):
        result = mod._probe_provisioning_rights(
            "00000000-0000-0000-0000-000000000000", "rg-test"
        )
    assert result["rights_class"] == mod.RIGHTS_FULL
    assert result["roles"] == ["Owner"]
    assert result["probe_skipped"] is False


def test_probe_empty():
    fake = _fake_run(stdout="[]")
    with patch.object(subprocess, "run", return_value=fake):
        result = mod._probe_provisioning_rights("sub", "rg")
    assert result["rights_class"] == mod.RIGHTS_NONE
    assert result["roles"] == []


def test_probe_az_failure_returns_unknown():
    fake = _fake_run(returncode=1, stderr="ERROR: please run 'az login'")
    with patch.object(subprocess, "run", return_value=fake):
        result = mod._probe_provisioning_rights("sub", "rg")
    assert result["rights_class"] == mod.RIGHTS_UNKNOWN
    assert "az login" in result["error"]


# --- C4: --no-rights-probe short-circuit -----------------------------------

def test_probe_skipped_flag_short_circuits():
    result = mod._probe_provisioning_rights("sub", "rg", skip=True)
    assert result["rights_class"] == mod.RIGHTS_UNKNOWN
    assert result["probe_skipped"] is True
    assert result["roles"] == []


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
