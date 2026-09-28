"""Recorded delivery coherence, not semantic/visual/hosted attestation."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from skills._shared import presenter
from skills._shared.tests.test_presenter import pilot, evidence, write


NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)


def setup(root, process="returns", *, expires_at=None):
    contract = pilot(root, process)
    if expires_at:
        contract["availability"]["expires_at"] = expires_at
    contract["delivery"] = {
        "version": "release-1",
        "surfaces": [{"role": role, "path": f"views/{role}.html"}
                     for role in ("page", "guide", "diagram", "sizing")],
    }
    write(root, presenter.CONTRACT, contract)
    refs = evidence(root, contract)
    package = json.loads((root / refs["package"]["path"]).read_text())
    package["facts"]["web_artifacts"] = {
        name: True for name in ("import_closure", "mime_nosniff", "private_files_absent", "image_runtime")}
    refs["package"]["sha256"] = presenter.sha256(write(root, refs["package"]["path"], package).read_bytes())
    for check in ("backend", "script", "human"):
        value = json.loads((root / refs[check]["path"]).read_text())
        value["predecessors"] = {key: refs[key]["sha256"] for key in presenter.PREDECESSORS[check]}
        refs[check]["sha256"] = presenter.sha256(write(root, refs[check]["path"], value).read_bytes())
    write(root, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    return contract, refs


def publish_views(root, contract):
    report = presenter.assess(root, now=NOW)
    block = presenter.render_delivery(report["delivery"]["facts"])
    for surface in contract["delivery"]["surfaces"]:
        write(root, surface["path"], f"<h1>Process view</h1>\n{block}")
    return report["delivery"]["facts"]


def qualify(root, refs, facts, contract):
    for check in ("script", "human"):
        ref = refs[check]
        value = json.loads((root / ref["path"]).read_text())
        value["facts"]["delivery"] = {
            "facts_sha256": presenter.canonical_hash(facts),
            "surfaces": {s["path"]: presenter.sha256((root / s["path"]).read_bytes())
                         for s in contract["delivery"]["surfaces"]},
        }
        if check == "script":
            value["facts"]["delivery"]["journey"] = {
                step: True for step in ("entry", "explanation", "outcome", "next_human_action")}
        else:
            value["facts"]["delivery"]["semantic_review"] = {
                "reviewer": contract["owner"], "qualification": "process-owner",
                "topology": True, "promises": True, "responsibilities": True,
                "current_vs_history": True,
            }
        value["predecessors"] = {key: refs[key]["sha256"] for key in presenter.PREDECESSORS[check]}
        ref["sha256"] = presenter.sha256(write(root, ref["path"], value).read_bytes())
    write(root, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})


@pytest.mark.parametrize("process", ["returns", "maintenance"])
def test_canonical_facts_flow_to_all_promised_surfaces(tmp_path, process):
    contract, refs = setup(tmp_path, process)
    facts = publish_views(tmp_path, contract)
    assert facts["process_id"] == process
    assert facts["version"] == "release-1"
    assert facts["outcome"] == contract["description"]["outcome"]
    assert facts["recorded_backend"] == "verified"
    assert facts["source_usable"] is True
    qualify(tmp_path, refs, facts, contract)
    report = presenter.assess(tmp_path, now=NOW)
    assert report["ready"]
    assert report["delivery"]["status"] == "verified"


@pytest.mark.parametrize("fault", ["old-version", "old-outcome", "other-process", "old-count",
                                  "missing", "duplicate-block"])
def test_stale_or_missing_surface_blocks_promotion(tmp_path, fault):
    contract, refs = setup(tmp_path)
    facts = publish_views(tmp_path, contract)
    qualify(tmp_path, refs, facts, contract)
    path = tmp_path / "views/page.html"
    if fault == "missing":
        path.unlink()
    elif fault == "duplicate-block":
        path.write_text(path.read_text() * 2)
    else:
        changed = dict(facts)
        changed[{"old-version": "version", "old-outcome": "outcome",
                 "other-process": "process_id", "old-count": "recorded_backend"}[fault]] = "obsolete"
        path.write_text(presenter.render_delivery(changed))
    assert not presenter.assess(tmp_path, now=NOW)["ready"]


@pytest.mark.parametrize("fault", ["unqualified", "wrong-owner", "wrong-topology", "missing-journey"])
def test_semantic_review_is_scoped_and_required(tmp_path, fault):
    contract, refs = setup(tmp_path)
    facts = publish_views(tmp_path, contract)
    qualify(tmp_path, refs, facts, contract)
    check = "script" if fault == "missing-journey" else "human"
    ref = refs[check]
    value = json.loads((tmp_path / ref["path"]).read_text())
    proof = value["facts"]["delivery"]
    if fault == "missing-journey":
        proof["journey"].pop("next_human_action")
    else:
        key = {"unqualified": "qualification", "wrong-owner": "reviewer",
               "wrong-topology": "topology"}[fault]
        proof["semantic_review"][key] = False if fault == "wrong-topology" else "unqualified"
    ref["sha256"] = presenter.sha256(write(tmp_path, ref["path"], value).read_bytes())
    write(tmp_path, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    assert not presenter.assess(tmp_path, now=NOW)["ready"]


def test_recorded_success_not_new_source_eligibility(tmp_path):
    contract, refs = setup(tmp_path, expires_at="2026-09-24T11:00:00Z")
    report = presenter.assess(tmp_path, now=NOW)
    facts = report["delivery"]["facts"]
    # Evidence ages independently; source expiry must never erase its recorded receipt.
    assert (tmp_path / refs["backend"]["path"]).is_file()
    assert facts["recorded_backend"] == "verified"
    assert facts["source_usable"] is False
    assert facts["historical_read"] == contract["availability"]["historical_read"]
    assert not report["ready"]


@pytest.mark.parametrize("value", [None, {}, {"version": "one", "surfaces": []}])
def test_malformed_selected_delivery_fails_closed(tmp_path, value):
    contract = pilot(tmp_path)
    contract["delivery"] = value
    write(tmp_path, presenter.CONTRACT, contract)
    assert presenter.assess(tmp_path, now=NOW)["states"]["source-ready"] == "blocked"


def test_unselected_contract_remains_unchanged(tmp_path):
    write(tmp_path, "specs/manifest.json", {})
    assert presenter.assess(tmp_path, now=NOW) == {"enabled": False}


def test_changed_topology_after_semantic_review_invalidates_promotion(tmp_path):
    contract, refs = setup(tmp_path)
    facts = publish_views(tmp_path, contract)
    qualify(tmp_path, refs, facts, contract)
    path = tmp_path / "views/diagram.html"
    path.write_text(path.read_text() + "<p>Wrong service owns the business decision</p>")
    report = presenter.assess(tmp_path, now=NOW)
    assert not report["ready"]
    assert report["delivery"]["reason"] == "delivery-review-source-binding"


def test_default_profile_and_absent_selected_contract(tmp_path):
    write(tmp_path, "specs/manifest.json", {"delivery_profile": "default"})
    assert presenter.assess(tmp_path, now=NOW) == {"enabled": False}
    write(tmp_path, "specs/manifest.json", {"delivery_profile": "presenter-ready"})
    assert presenter.assess(tmp_path, now=NOW)["states"]["source-ready"] == "blocked"


def test_surface_alias_cannot_replace_a_promised_role(tmp_path):
    contract, _ = setup(tmp_path)
    contract["delivery"]["surfaces"][1]["path"] = "./views/page.html"
    write(tmp_path, presenter.CONTRACT, contract)
    assert presenter.assess(tmp_path, now=NOW)["states"]["source-ready"] == "blocked"


def test_delivery_block_cli_prints_projection_not_acceptance(tmp_path, capsys):
    setup(tmp_path)
    assert presenter.main(["--root", str(tmp_path), "--delivery-block"]) == 0
    output = capsys.readouterr().out
    assert presenter.DELIVERY_START in output
    assert "not write authorization" in output
    assert not presenter.assess(tmp_path, now=NOW)["ready"]


def test_promised_surface_is_bound_in_immutable_publication(tmp_path):
    from skills._shared.tests.test_presenter import git
    contract, _ = setup(tmp_path)
    publish_views(tmp_path, contract)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "fixture")
    proof = presenter.verify_publication(tmp_path, contract, git(tmp_path, "rev-parse", "HEAD"))
    assert set(proof["artifacts"]) == {s["path"] for s in contract["delivery"]["surfaces"]}


def test_delivery_guide_exposes_executable_projection_and_semantic_limit():
    root = Path(__file__).resolve().parents[3]
    text = (root / "docs/presenter-ready.md").read_text()
    for term in ("--delivery-block", "semantic_review", "facts_sha256",
                 "current_vs_history", "not semantic verification", "computed dynamic"):
        assert term in text


@pytest.mark.parametrize("case", ["import_closure", "mime_nosniff", "private_files_absent", "image_runtime"])
def test_package_requires_actual_artifact_cases_not_loopback_alone(tmp_path, case):
    contract, refs = setup(tmp_path)
    ref = refs["package"]
    value = json.loads((tmp_path / ref["path"]).read_text())
    value["facts"]["web_artifacts"][case] = False
    ref["sha256"] = presenter.sha256(write(tmp_path, ref["path"], value).read_bytes())
    write(tmp_path, presenter.EVIDENCE, {"schema": "threadlight-presenter-evidence/v1", "checks": refs})
    report = presenter.assess(tmp_path, now=NOW)
    assert report["checks"]["package"]["status"] == "blocked"
    assert not report["ready"]
