"""Resilience posture for long-running hosted agents (#225).

REL-009..013 are inventory of the repository; only REL-014 can be satisfied, and
only by executed hosted crash-test evidence bound to the current image.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/production_ready.py"
spec = importlib.util.spec_from_file_location("resilience_readiness", SCRIPT)
pr = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pr
spec.loader.exec_module(pr)

IMAGE = "crexample.azurecr.io/pilot/agent:azd-deploy-1"
RESILIENT_SRC = """
app = ResponsesAgentServerHost(options=ResponsesServerOptions(resilient_background=True))
if context.is_recovery and context.persisted_response is not None: pass
yield stream.checkpoint()
with effect_scope(context.response_id, index): pass
body = {"store": True, "background": True, "stream": True}
"""
RECONCILER = "def reconcile_once(jobs): ...  # deadline_at -> deadline_exceeded\n"
IDS = ("REL-009", "REL-010", "REL-011", "REL-012", "REL-013", "REL-014")


def context(tmp_path, *, spec="", src="", docs="", image=IMAGE):
    (tmp_path / "evidence").mkdir(exist_ok=True)
    return SimpleNamespace(
        root=tmp_path, spec_text=spec, spec_12={}, bicep_text="", docs_text=docs, src_text=src,
        azure_yaml_text="", bicep_graph=None, manifest={},
        azd_env={"SERVICE_AGENT_IMAGE_NAME": image} if image else {})


def findings(ctx):
    return {f.id: f for f in pr._check_resilience_posture(ctx)}


def hosted(ctx, **change):
    record = {
        "evidence": "hosted", "verdict": "recovered", "image": IMAGE, "agent_version": "3",
        "captured_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        "response_id": "caresp_x", "entries": [{"is_recovery": False}, {"is_recovery": True}],
        "replay": {"response.created": 1, "response.in_progress": 2, "response.completed": 1},
    }
    record.update(change)
    (ctx.root / "evidence/crash-test-hosted.json").write_text(json.dumps(record))


def test_catalog_places_the_checks_in_reliability_as_static_inventory():
    for fid in IDS:
        entry = pr.FINDING_CATALOG[fid]
        assert entry["pillar"] == "reliability" and entry["tier"] == 0


def test_short_turn_agent_is_not_applicable(tmp_path):
    result = findings(context(tmp_path, spec="Answers in a few seconds."))
    assert {result[fid].status for fid in IDS} == {"not-applicable"}


def test_long_turn_foreground_agent_fails_the_background_and_recovery_checks(tmp_path):
    result = findings(context(tmp_path, spec="Turns can take up to 10 minutes (long-running research)."))
    assert result["REL-009"].status == "must-fix"
    assert result["REL-010"].status == "must-fix"
    assert result["REL-013"].status == "should-fix"
    assert all(result[fid].detail.startswith("inventory:") for fid in IDS[:5])


def test_honest_not_crash_resilient_label_is_accepted_without_claiming_resilience(tmp_path):
    src = 'body = {"store": True, "background": True}\n' + RECONCILER
    result = findings(context(tmp_path, spec="long-running", src=src,
                              docs="Background only, not crash-resilient."))
    assert result["REL-009"].status == "pass"
    assert result["REL-010"].status == "pass" and "not crash-resilient" in result["REL-010"].detail
    assert result["REL-011"].status == result["REL-012"].status == result["REL-014"].status == "not-applicable"
    assert result["REL-013"].status == "pass"


def test_resilient_code_without_checkpoints_or_fencing_is_not_enough(tmp_path):
    src = 'ResponsesServerOptions(resilient_background=True)\nbody = {"store": True, "background": True}\n'
    result = findings(context(tmp_path, spec="long-running", src=src))
    assert result["REL-010"].status == "pass"
    assert result["REL-011"].status == "must-fix"
    assert result["REL-012"].status == "must-fix"


def test_complete_inventory_without_hosted_evidence_never_passes_rel_014(tmp_path):
    ctx = context(tmp_path, spec="long-running", src=RESILIENT_SRC + RECONCILER)
    result = findings(ctx)
    assert all(result[fid].status == "pass" for fid in IDS[:5])
    assert result["REL-014"].status == "must-fix"
    assert result["REL-014"].detail.startswith("executed hosted evidence:")


def test_local_harness_evidence_is_not_hosted_evidence(tmp_path):
    ctx = context(tmp_path, spec="long-running", src=RESILIENT_SRC + RECONCILER)
    hosted(ctx, evidence="local")
    result = findings(ctx)["REL-014"]
    assert result.status == "not-verified" and "local" in result.detail


@pytest.mark.parametrize("change", [
    {"image": "crexample.azurecr.io/pilot/agent:older"},
    {"captured_at": (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()},
    {"captured_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    {"verdict": "not_recovered"},
    {"replay": {"response.created": 2, "response.in_progress": 2, "response.completed": 1}},
])
def test_stale_unbound_or_unsuccessful_hosted_evidence_does_not_pass(tmp_path, change):
    ctx = context(tmp_path, spec="long-running", src=RESILIENT_SRC + RECONCILER)
    hosted(ctx, **change)
    assert findings(ctx)["REL-014"].status != "pass"


def test_unknown_current_image_cannot_bind_hosted_evidence(tmp_path):
    ctx = context(tmp_path, spec="long-running", src=RESILIENT_SRC + RECONCILER, image=None)
    hosted(ctx)
    assert findings(ctx)["REL-014"].status == "not-verified"


def test_fresh_hosted_evidence_for_the_current_image_passes(tmp_path):
    ctx = context(tmp_path, spec="long-running", src=RESILIENT_SRC + RECONCILER)
    hosted(ctx)
    result = findings(ctx)["REL-014"]
    assert result.status == "pass" and result.evidence_refs == ["evidence/crash-test-hosted.json"]


def test_reliability_pillar_runs_the_resilience_checks(tmp_path):
    ctx = context(tmp_path, spec="long-running", src=RESILIENT_SRC + RECONCILER)
    ctx.manifest = {"deployment_manifest": {}}
    assert set(IDS) <= {f.id for f in pr._check_reliability_static(ctx)}
