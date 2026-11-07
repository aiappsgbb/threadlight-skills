"""Selected governance with no package reads the same in every phase and environment.

The 2026-11-07 live re-proof ran the post-deploy safe-check with a system Python
that lacked the native governance packages (govern_bundle, govern_control_plane).
check() imported them before looking for the package, so the gap read
``package-invalid-or-incomplete`` instead of ``selected-governance-not-packaged``.
Pre-deploy and post-deploy both use ``safe_check._governance_static``.
"""
import json
import sys

import pytest

from test_governance_gates import reference, safe_check
from test_governance_review_blockers import project_manifest
from test_governance_wiring import contract

NATIVE = ("govern_bundle", "govern_bundle.policy_bundle", "govern_control_plane",
          "govern_control_plane.models", "govern_gateway")


@pytest.mark.parametrize("native_installed", [True, False])
def test_unpackaged_selected_governance_names_the_missing_package(tmp_path, monkeypatch, native_installed):
    if not native_installed:
        for name in NATIVE:
            monkeypatch.setitem(sys.modules, name, None)
    path, parent = project_manifest(tmp_path, contract())
    gate = reference("governance_static")
    assert gate.check(tmp_path, parent, manifest_path=path)["gaps"] == [
        "governance: selected-governance-not-packaged"]
    result = safe_check._governance_static(tmp_path, parent, manifest_path=path)
    assert result["gaps"] == ["governance: selected-governance-not-packaged"]
    assert "package-invalid-or-incomplete" not in json.dumps(result)


def test_off_contract_still_reports_nothing(tmp_path, monkeypatch):
    for name in NATIVE:
        monkeypatch.setitem(sys.modules, name, None)
    path, parent = project_manifest(tmp_path, contract(off=True))
    assert reference("governance_static").check(tmp_path, parent, manifest_path=path) == {}
