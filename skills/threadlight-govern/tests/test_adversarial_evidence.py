"""Reproducible business-pressure scenarios; illustrative dialogue is not model execution."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = ROOT / "skills/threadlight-deploy/references/governance"
pytestmark = pytest.mark.governance_runtime


def scenario_module():
    spec = importlib.util.spec_from_file_location(
        "threadlight_adversarial_evidence", REFERENCE / "adversarial_evidence.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_real_pep_adversarial_matrix_records_receipts_and_business_observations(tmp_path):
    async def run():
        module = scenario_module()
        report = await module.run_local(tmp_path)
        assert report["scope"] == "local-native-pep-with-synthetic-authorities-and-business-store"
        assert report["model_execution"]["status"] == "not-run"
        rows = {row["case"]: row for row in report["cases"]}
        zero_dispatch = {
            "missing-attestation", "tampered-token", "wrong-issuer", "other-case",
            "other-subject", "uncovered-revision", "expired-attestation", "text-only-human-approval",
            "policy-ineligible",
        }
        assert zero_dispatch <= rows.keys()
        for name in zero_dispatch:
            row = rows[name]
            assert row["downstream_posts"] == row["business_effects"] == 0
            assert row["before"] == row["after"]
            assert row["governance_receipts"], row
        for name in ("changed-amount-at-backend", "backend-ineligible"):
            row = rows[name]
            assert row["downstream_posts"] == 1 and row["business_effects"] == 0
            assert row["before"] == row["after"]
            assert row["backend_outcome"] == "business_conflict"
        assert rows["positive"]["business_effects"] == 1
        assert rows["positive"]["after"]["status"] == "closed"
        assert rows["positive-replay"]["downstream_posts"] == rows["positive-replay"]["business_effects"] == 0
        assert rows["positive-replay"]["downstream_gets"] == 1
        assert report["business_effect_count"] == 1
        assert report["passed"] is True
        wire = json.dumps(report)
        for forbidden in ("attestation", "Authorization", "private_key"):
            assert f'"{forbidden}":' not in wire
        assert "eyJ" not in wire
        persisted = json.loads((tmp_path / "adversarial-evidence.json").read_text())
        assert persisted == report
    asyncio.run(run())


def test_conversations_are_labelled_illustrative_and_preserve_original_subject():
    module = scenario_module()
    for domain, original in (("returns", "RMA-EVIDENCE-BLOCKED"), ("loan", "LOAN-EVIDENCE-BLOCKED")):
        scenario = module.conversation(domain)
        assert scenario["kind"] == "illustrative-multi-turn-fixture"
        assert len(scenario["turns"]) >= 5
        assert all(turn["role"] == "user" for turn in scenario["turns"])
        assert original in scenario["turns"][0]["content"]
        assert "ipotetic" in " ".join(turn["content"] for turn in scenario["turns"])
        assert "quella versione" in scenario["turns"][-1]["content"]


def test_interrupted_model_conversation_cannot_make_overall_result_green(tmp_path, monkeypatch):
    module = scenario_module()
    async def failed_model(**kwargs):
        return {"status": "failed", "error_class": "fixture-rate-limit", "turns": []}
    monkeypatch.setattr(module, "run_model", failed_model)
    report = asyncio.run(module.run_local(
        tmp_path, cloud={"deployment": "fixture", "version": "fixture"}, token=None))
    assert report["deterministic_passed"] is True
    assert report["passed"] is False
