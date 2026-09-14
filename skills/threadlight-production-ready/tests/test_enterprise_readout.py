"""Presentation-only enterprise handoff from the actual readiness manifest."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "production_ready_enterprise", ROOT / "scripts/production_ready.py"
)
pr = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pr
spec.loader.exec_module(pr)


def manifest_for(rows, *, include_experimental=False):
    grouped = {}
    for fid, status in rows:
        finding = pr._mk_finding(fid, status)
        grouped.setdefault(finding.pillar, []).append(finding)
    manifest = pr._build_manifest(
        posture={
            "declared": "standard-ai-gateway",
            "detected": None,
            "resolved": "standard-ai-gateway",
        },
        pillar_results_raw=grouped,
        pillar_results_waived=grouped,
        evidence=[],
        not_verified=[f for fs in grouped.values() for f in fs if f.status == "not-verified"],
        waivers={}, tiers={0: True}, warnings=[], agt_profile="none",
        safe_check_ref={}, quick=False, static_only=True,
        include_experimental=include_experimental,
    )
    return manifest, grouped


def render(manifest, grouped):
    return pr._render_report(manifest, manifest["posture"], grouped, [], {}, [])


def test_enterprise_areas_cover_each_existing_pillar_once():
    areas = pr.ENTERPRISE_AREAS
    assert [area["title"] for area in areas] == ["DevOps", "Runtime governance", "Operations"]
    pillars = [pillar for area in areas for pillar in area["pillars"]]
    assert len(pillars) == len(set(pillars))
    assert set(pillars) == set(pr.PILLAR_IDS)
    assert {meta["pillar"] for meta in pr.FINDING_CATALOG.values()} <= set(pillars)


def test_complete_catalog_has_one_handoff_reference_per_finding():
    manifest, _ = manifest_for(
        [(fid, "not-verified") for fid in pr.FINDING_CATALOG],
        include_experimental=True,
    )
    readout = "\n".join(pr._render_enterprise_handoff(manifest))
    refs = re.findall(r"`([A-Z]+(?:-V4)?-\d{3})`", readout)
    assert len(refs) == len(set(refs)) == len(pr.FINDING_CATALOG)
    assert set(refs) == set(pr.FINDING_CATALOG)


def test_handoff_distinguishes_baseline_modules_and_existing_statuses():
    manifest, _ = manifest_for([
        ("NET-001", "must-fix"), ("IAM-001", "not-verified"),
        ("AGT-001", "not-verified"), ("HITL-008", "waived"),
        ("SRE-001", "should-fix"), ("COST-001", "pass"),
    ])
    readout = "\n".join(pr._render_enterprise_handoff(manifest))
    devops, runtime, operations = (
        readout.split(f"### {title}\n", 1)[1].split("\n### ", 1)[0]
        for title in ("DevOps", "Runtime governance", "Operations")
    )
    assert "Private networking" in devops and "dedicated workload identity" in devops
    assert "code execution disabled by default" in runtime
    assert "backend authorization" in runtime
    assert "Owner and escalation" in operations and "recovery" in operations
    assert all("**Optional / conditional modules:**" in area for area in (devops, runtime, operations))
    assert "SAFE/ACS tool governance" in runtime
    assert "Azure SRE Agent" in operations
    assert "`NET-001`" in devops and "`AGT-001`" not in devops
    assert "`AGT-001`" in runtime and "waived: 1" in runtime
    assert "`SRE-001`" in operations and "pass: 1" in operations
    assert "not-verified: 1" in runtime
    assert "not proof that the enterprise baseline is satisfied" in readout
    assert "not inferred from a domain, template or finding" in readout


@pytest.mark.parametrize("rows", [
    [],
    [("NET-001", "not-applicable"), ("AGT-001", "not-applicable"), ("SRE-001", "not-applicable")],
])
def test_missing_or_inapplicable_findings_never_accept_the_baseline(rows):
    manifest, grouped = manifest_for(rows)
    readout = "\n".join(pr._render_enterprise_handoff(manifest))
    assert readout.count("baseline acceptance is not established") == 3
    report = render(manifest, grouped)
    for overclaim in ("all checks executed", "Everything was checked", "Pilot is production-ready"):
        assert overclaim not in report


def test_handoff_uses_emitted_scope_including_experimental_selection():
    rows = [("NET-001", "pass"), ("NET-103", "not-verified")]
    manifest, _ = manifest_for(rows)
    assert "`NET-103`" not in "\n".join(pr._render_enterprise_handoff(manifest))
    selected, _ = manifest_for(rows, include_experimental=True)
    assert "`NET-103`" in "\n".join(pr._render_enterprise_handoff(selected))


def test_render_is_pure_and_preserves_manifest_plan_and_details(monkeypatch):
    manifest, grouped = manifest_for([
        ("NET-001", "must-fix"), ("AGT-001", "not-verified"), ("SRE-001", "should-fix"),
    ])
    manifest = json.loads(json.dumps(manifest))
    before_manifest, before_grouped = deepcopy(manifest), deepcopy(grouped)
    plan = pr.build_apply_plan(manifest=manifest, recipes={}, framing={})

    def unexpected_io(*args, **kwargs):
        raise AssertionError("Rendering must not run a probe, read files or generate artifacts")

    monkeypatch.setattr(pr.subprocess, "run", unexpected_io)
    monkeypatch.setattr(Path, "read_text", unexpected_io)
    monkeypatch.setattr(Path, "write_text", unexpected_io)
    report = render(manifest, grouped)
    assert report == render(manifest, grouped)
    assert manifest == before_manifest and grouped == before_grouped
    assert pr.build_apply_plan(manifest=manifest, recipes={}, framing={}) == plan
    assert report.index("## Enterprise handoff") < report.index("## 1. Executive summary")
    for finding in ("NET-001", "AGT-001", "SRE-001"):
        assert f"| `{finding}` |" in report
    assert "## 8. Outcome KPI scorecard" in report
    assert "### Evidence register" in report
    assert "## 4. Pillar scorecard" in report


def test_optional_modules_do_not_relax_a_raw_hard_gate():
    manifest, grouped = manifest_for([("HITL-008", "waived")])
    manifest["would_fail_hard_gate"] = True
    report = render(manifest, grouped)
    preview = report.split("## 3. Hard-gate preview", 1)[1].split("## 4.", 1)[0]
    assert "**Would fail a hard gate.**" in preview
    assert "would pass a hard gate today" not in preview
    assert "waivers or overrides do not clear this raw flag" in preview


def test_report_does_not_prescribe_citadel_or_destructive_rollback():
    manifest, grouped = manifest_for([])
    report = render(manifest, grouped)
    assert "Recommended enterprise posture: Citadel-spoke" not in report
    assert "Optional Citadel integration" in report
    assert "azd down --force --purge" not in report
    assert "previously accepted immutable version" in report
    assert "not permission to change deployment or traffic" in report


def test_skill_describes_three_areas_without_activating_modules():
    text = (ROOT / "SKILL.md").read_text()
    for title in ("DevOps", "Runtime governance", "Operations"):
        assert title in text
    assert "presentation-only" in text
    assert "No new score, gate, manifest or prototype stage" in text
    assert "Explicit opt-in" in text
