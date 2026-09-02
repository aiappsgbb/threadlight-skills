"""Tests for threadlight-governed-actions' operational alert catalog
assessment (Task 8, design section 17).

Governance is only as useful as its own failure-signaling: if an
interceptor crash, an approval replay, an audit-delivery failure, or a
drifted branch-protection/deployment-identity control would never itself
raise an alert, a customer could be silently unprotected. ``assess_alerts``
proves the assessed repository actually declares all eight required alert
classes as enabled, stable (a ``reason_code``/``correlation_id`` pair), and
payload-free -- never inventing a business policy, threshold, or approver
of its own -- and reports the single ``OPS-001`` finding this project's
finding catalog already reserves for that gap.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_alerts.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from alerts import REQUIRED_ALERT_CLASSES, assess_alerts


_REQUIRED_ALERT_CLASSES = (
    "unmediated-action",
    "interceptor-failure",
    "approval-replay",
    "output-mediator-failure",
    "audit-delivery-failure",
    "tuple-drift",
    "repository-protection-drift",
    "deployment-identity-drift",
)


def _write_catalog(root: Path, overrides=None, omit=()) -> Path:
    definitions = {
        name: {
            "enabled": True,
            "reason_code": f"ALERT-{name.upper()}",
            "correlation_id": f"corr-{name}",
        }
        for name in _REQUIRED_ALERT_CLASSES
        if name not in omit
    }
    if overrides:
        for name, patch in overrides.items():
            definitions.setdefault(name, {}).update(patch)
    governance_dir = root / "governance"
    governance_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = governance_dir / "alerts.json"
    catalog_path.write_text(json.dumps(definitions), encoding="utf-8")
    return catalog_path


def test_required_alert_classes_match_design_section_17():
    assert REQUIRED_ALERT_CLASSES == _REQUIRED_ALERT_CLASSES


def test_complete_catalog_passes(tmp_path):
    _write_catalog(tmp_path)
    finding, evidence = assess_alerts(tmp_path, phase="pre-deploy", live_evidence=None)
    assert finding.finding_id == "OPS-001"
    assert finding.status == "pass"


def test_missing_catalog_file_is_should_fix(tmp_path):
    finding, evidence = assess_alerts(tmp_path, phase="pre-deploy", live_evidence=None)
    assert finding.finding_id == "OPS-001"
    assert finding.status == "should-fix"
    assert evidence == ()


def test_missing_alert_class_is_should_fix(tmp_path):
    _write_catalog(tmp_path, omit=("audit-delivery-failure",))
    finding, evidence = assess_alerts(tmp_path, phase="pre-deploy", live_evidence=None)
    assert finding.finding_id == "OPS-001"
    assert finding.status == "should-fix"
    assert "audit-delivery-failure" in finding.details


@pytest.mark.parametrize(
    "patch",
    [
        {"enabled": False},
        {"enabled": "yes"},
        {"reason_code": ""},
        {"reason_code": 1},
        {"correlation_id": ""},
        {"correlation_id": None},
    ],
)
def test_incomplete_definition_is_should_fix(tmp_path, patch):
    _write_catalog(tmp_path, overrides={"tuple-drift": patch})
    finding, evidence = assess_alerts(tmp_path, phase="pre-deploy", live_evidence=None)
    assert finding.status == "should-fix"
    assert "tuple-drift" in finding.details


def test_definition_with_payload_field_is_should_fix(tmp_path):
    _write_catalog(
        tmp_path, overrides={"tuple-drift": {"message": "do not leak this customer payload"}}
    )
    finding, evidence = assess_alerts(tmp_path, phase="pre-deploy", live_evidence=None)
    assert finding.status == "should-fix"
    assert "do not leak this customer payload" not in finding.details


def test_proven_mandatory_event_loss_is_must_fix(tmp_path):
    _write_catalog(tmp_path)
    finding, evidence = assess_alerts(
        tmp_path,
        phase="pre-deploy",
        live_evidence={"dropped_alert_classes": ("interceptor-failure",)},
    )
    assert finding.finding_id == "OPS-001"
    assert finding.status == "must-fix"


def test_inaccessible_live_state_is_not_verified(tmp_path):
    _write_catalog(tmp_path)
    finding, evidence = assess_alerts(
        tmp_path,
        phase="pre-deploy",
        live_evidence={"unavailable_alert_classes": ("audit-delivery-failure",)},
    )
    assert finding.finding_id == "OPS-001"
    assert finding.status == "not-verified"


def test_dropped_event_takes_precedence_over_unavailable_live_state(tmp_path):
    _write_catalog(tmp_path)
    finding, evidence = assess_alerts(
        tmp_path,
        phase="pre-deploy",
        live_evidence={
            "dropped_alert_classes": ("interceptor-failure",),
            "unavailable_alert_classes": ("audit-delivery-failure",),
        },
    )
    assert finding.status == "must-fix"


def test_pass_never_leaks_definition_contents_into_finding(tmp_path):
    _write_catalog(tmp_path)
    finding, evidence = assess_alerts(tmp_path, phase="pre-deploy", live_evidence=None)
    assert "ALERT-" not in finding.details
    assert "corr-" not in finding.details


def test_finding_phase_matches_requested_phase(tmp_path):
    _write_catalog(tmp_path)
    finding, evidence = assess_alerts(tmp_path, phase="post-deploy", live_evidence=None)
    assert finding.phase == "post-deploy"


def test_empty_catalog_object_is_should_fix(tmp_path):
    governance_dir = tmp_path / "governance"
    governance_dir.mkdir(parents=True)
    (governance_dir / "alerts.json").write_text("{}", encoding="utf-8")
    finding, evidence = assess_alerts(tmp_path, phase="pre-deploy", live_evidence=None)
    assert finding.status == "should-fix"


def test_malformed_catalog_json_is_should_fix(tmp_path):
    governance_dir = tmp_path / "governance"
    governance_dir.mkdir(parents=True)
    (governance_dir / "alerts.json").write_text("not json", encoding="utf-8")
    finding, evidence = assess_alerts(tmp_path, phase="pre-deploy", live_evidence=None)
    assert finding.status == "should-fix"


_REFERENCES = Path(__file__).resolve().parent.parent / "references"


def test_ops_001_plane_is_both_and_matches_finding_catalog(tmp_path):
    """OPS-001 is a design-mandated "both" (change *and* runtime) finding:
    alert-catalog completeness is knowable from the repository at
    pre-deploy time, but proven event loss is only ever knowable from
    live/runtime evidence -- so the plane can never be narrowed to just
    one. This must never drift from the project's own finding catalog."""
    catalog = json.loads((_REFERENCES / "finding-catalog.json").read_text(encoding="utf-8"))
    entry = next(item for item in catalog["findings"] if item["finding_id"] == "OPS-001")
    assert entry["plane"] == "both"

    _write_catalog(tmp_path)
    finding, evidence = assess_alerts(tmp_path, phase="pre-deploy", live_evidence=None)
    assert finding.plane == entry["plane"] == "both"
