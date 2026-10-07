"""The mandatory full Foundry package gate is wired into production-ready.

A workspace without a complete ``specs/foundry-package-manifest.json`` can never
be reported as ready, regardless of the legacy scorecard.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from skills._shared.tests.foundry_package_fixtures import seed_foundry_package  # noqa: E402

_PR_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "production_ready.py"
_spec = importlib.util.spec_from_file_location("production_ready", _PR_SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("production_ready", mod)
_spec.loader.exec_module(mod)


def test_missing_manifest_is_incomplete(tmp_path):
    result = mod._foundry_package_gate(tmp_path)
    assert result["status"] == "INCOMPLETE"
    assert result["missing"]


def test_malformed_manifest_fails_closed(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "foundry-package-manifest.json").write_text("{not json", encoding="utf-8")
    result = mod._foundry_package_gate(tmp_path)
    assert result["status"] == "INCOMPLETE"
    assert "malformed" in result["missing"][0]


def test_complete_manifest_is_complete(tmp_path):
    seed_foundry_package(tmp_path)
    assert mod._foundry_package_gate(tmp_path)["status"] == "COMPLETE"


def test_fallback_agent_is_incomplete(tmp_path):
    seed_foundry_package(tmp_path)
    path = tmp_path / "specs" / "foundry-package-manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["agent"]["fallback_active"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    result = mod._foundry_package_gate(tmp_path)
    assert result["status"] == "INCOMPLETE"
    assert any("fallback_active" in m for m in result["missing"])


def test_main_overrides_ready_recommendation_and_report_says_incomplete():
    source = _PR_SCRIPT.read_text(encoding="utf-8")
    assert 'out_manifest["foundry_package"] = package' in source
    assert 'out_manifest["go_live_recommendation"] = "not_ready"' in source
    assert "Full Foundry package:** INCOMPLETE" in source


def test_incomplete_package_keeps_hard_gate_flag_as_raw_must_fix_only():
    source = _PR_SCRIPT.read_text(encoding="utf-8")
    start = source.index('out_manifest["foundry_package"] = package')
    block = source[start:start + 600]
    assert 'out_manifest["would_fail_hard_gate"] = True' not in block
