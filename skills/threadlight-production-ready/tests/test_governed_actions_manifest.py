#!/usr/bin/env python3
"""production-ready consumes the governed-actions manifest as THREE aggregates.

`threadlight-governed-actions` owns the detailed consequential-action
assessment (inventory, mediation paths, enforcement, approval binding,
output/audit payload-freedom, pin integrity, GitHub Copilot change plane).
production-ready must never re-run any of that. It reads the emitted
`tests/governed-actions-manifest.json` and folds it into exactly three
aggregate findings:

    AGT-007  (agent-governance, runtime)
    HITL-008 (hitl-audit, approval + output)
    SUP-014  (supply-chain, change plane)

The manifest is UNTRUSTED input. Before a single child status is believed the
consumer re-derives everything it can from the target repository: schema,
assessor identity/version, lifecycle phase, clean source bound to the observed
repository and commit, recomputed policy hashes, the policy-set digest each
referenced evidence entry binds itself to, the selected target environment,
freshness, evidence resolution, and summary/finding agreement. Anything short
of that is `not-verified` — never `pass`, and the manifest's own
`summary.verdict` is never believed on its own.

The fixtures are the REAL Task 12 goldens: the emitted conformant manifest
plus the exact repository tree its policy hashes were taken over, so the
consumer is exercised against a genuine producer artefact rather than a
hand-rolled shape that could drift from it.

pytest-style (bare ``test_`` functions + ``assert``); no extra deps.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"

sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402

SKILLS_DIR = SKILL_DIR.parent
GOVERNED_ACTIONS_DIR = SKILLS_DIR / "threadlight-governed-actions"
GOLDEN_MANIFEST = GOVERNED_ACTIONS_DIR / "tests" / "golden" / "conformant-manifest.json"
GOLDEN_TARGET_TREE = GOVERNED_ACTIONS_DIR / "tests" / "fixtures" / "conformant-maf"

# Identity the golden manifest binds itself to.
GOLDEN_REPOSITORY = "octo-org/governed-actions-fixtures"
GOLDEN_COMMIT = "0123456789abcdef0123456789abcdef01234567"
# Inside the golden's own freshness window (2026-09-01T12:00Z .. 2026-09-02T12:00Z).
FRESH_NOW = datetime(2026, 9, 1, 18, 0, 0, tzinfo=timezone.utc)

MANIFEST_RELPATH = ("tests", "governed-actions-manifest.json")

AGGREGATE_IDS = ("AGT-007", "HITL-008", "SUP-014")

# The three aggregates and the child finding ids each one owns. OPS-001 is the
# only child owned by two aggregates (it is a `both`-plane operational-alerting
# finding that bears on runtime governance AND the change plane).
EXPECTED_AGGREGATES = {
    "AGT-007": ("runtime", {
        "ACT-001", "ACT-002", "MED-001", "MED-002", "MED-003",
        "ENF-001", "ENF-002", "PIN-001", "OPS-001",
    }),
    "HITL-008": ("approval-output", {
        "APR-001", "OUT-001", "AUD-001",
    }),
    "SUP-014": ("change", {
        "GHCP-001", "GHCP-002", "GHCP-003",
        "GHCP-004", "GHCP-005", "GHCP-006", "OPS-001",
    }),
}

EXPECTED_PILLARS = {
    "AGT-007": "agent-governance",
    "HITL-008": "hitl-audit",
    "SUP-014": "supply-chain",
}

ALL_CHILD_IDS = frozenset().union(*(ids for _, ids in EXPECTED_AGGREGATES.values()))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _golden() -> dict:
    return json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))


def _with_probe_modes(manifest: dict | None = None) -> dict:
    manifest = manifest or _golden()
    path_modes = {
        path["path_id"]: path["mode"] for path in manifest["mediation_paths"]
    }
    for probe in manifest["conformance"]["application_probes"]:
        path_id = probe["path_id"]
        probe["mode"] = path_modes[path_id] if path_id is not None else None
    return manifest


def _rebuild_summary(manifest: dict) -> dict:
    """Restamp `summary` so it agrees with `findings` (counts included).

    The verdict is deliberately *optimistic* — always `governed` — so no test
    can pass merely because the consumer believed a pessimistic verdict string
    instead of reading the child statuses it is supposed to aggregate.
    """
    buckets = {
        "pass": [], "must_fix": [], "should_fix": [],
        "not_verified": [], "not_applicable": [],
    }
    key = {
        "pass": "pass", "must-fix": "must_fix", "should-fix": "should_fix",
        "not-verified": "not_verified", "not-applicable": "not_applicable",
    }
    for finding in manifest["findings"]:
        buckets[key[finding["status"]]].append(finding["finding_id"])
    manifest["summary"] = {
        "verdict": "governed",
        "pass": sorted(buckets["pass"]),
        "must_fix": sorted(buckets["must_fix"]),
        "should_fix": sorted(buckets["should_fix"]),
        "not_verified": sorted(buckets["not_verified"]),
        "not_applicable": sorted(buckets["not_applicable"]),
    }
    return manifest


def _child_finding(finding_id: str, status: str) -> dict:
    """A well-formed child finding entry citing no evidence."""
    return {
        "finding_id": finding_id,
        "status": status,
        "phase": "pre-deploy",
        "plane": "both",
        "reason_code": "fixture",
        "summary": "fixture child finding",
        "details": "fixture child finding",
        "affected_actions": [],
        "affected_paths": [],
        "evidence_refs": [],
        "remediation_ids": [],
        "residual_risk_ref": None,
    }


def with_children(*children: tuple[str, str]) -> dict:
    """The golden manifest with `children` (id, status) added to `findings`."""
    manifest = _golden()
    manifest["findings"].extend(_child_finding(fid, st) for fid, st in children)
    return _rebuild_summary(manifest)


def make_target(
    tmp_path: Path,
    manifest: dict | str | None,
    *,
    repository: str = GOLDEN_REPOSITORY,
) -> Path:
    """Materialise the golden target repository under `tmp_path`.

    The tree is the exact fixture the golden manifest's `policy_hashes` were
    taken over, so the consumer's own recomputation has something real to
    agree (or disagree) with. `origin` is set so the consumer can observe the
    repository identity the manifest claims to be bound to. Each call gets a
    fresh directory so one test may build several targets.
    """
    root = tmp_path / f"pilot-{len(list(tmp_path.iterdir()))}"
    shutil.copytree(GOLDEN_TARGET_TREE, root)
    subprocess.run(["git", "init", "-q", str(root)], check=True,
                   capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin",
         f"https://github.com/{repository}.git"],
        check=True, capture_output=True,
    )
    if manifest is not None:
        write_manifest(root, manifest)
    return root


def write_manifest(root: Path, manifest: dict | str) -> None:
    path = root.joinpath(*MANIFEST_RELPATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        manifest if isinstance(manifest, str)
        else json.dumps(manifest, indent=2) + "\n"
    )
    path.write_text(payload, encoding="utf-8")


def make_committed_target(tmp_path: Path, manifest: dict) -> Path:
    """A target repo with a real HEAD, and a manifest genuinely bound to it.

    `make_target` never commits, so `_governed_actions_head_commit` returns ""
    and nothing can ever be trusted through it. This variant rebinds the
    golden's identity (commit, capture instant, freshness window, and the
    referenced evidence that must agree with them) onto a live repository, so
    the trusted path through `_run_pillar`'s fold is actually exercised.
    """
    root = make_target(tmp_path, None)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@example.invalid",
         "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "fixture"],
        check=True, capture_output=True,
    )
    head = pr._governed_actions_head_commit(root)
    assert len(head) == 40, "fixture repository has no usable HEAD"

    now = datetime.now(timezone.utc)
    captured = now - timedelta(hours=1)
    oldest = now - timedelta(hours=2)
    stamp = oldest.strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = copy.deepcopy(manifest)
    manifest["source"]["commit"] = head
    manifest["captured_at"] = captured.strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["freshness"]["oldest_source_at"] = stamp
    manifest["freshness"]["expires_at"] = (
        oldest + timedelta(hours=int(manifest["freshness"]["valid_for_hours"]))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    for entry in manifest["evidence"]:
        if entry["source_commit"] == GOLDEN_COMMIT:
            entry["source_commit"] = head
        if entry["collected_at"] is not None:
            entry["collected_at"] = stamp
    write_manifest(root, manifest)
    return root


def _ctx(root: Path) -> "pr.RepoContext":
    return pr.RepoContext(
        root=root, bicep_files=[], src_files=[], test_files=[], spec_text="",
        spec_12={}, spec_11b={}, azure_yaml_text="", docs_text="", azd_env={},
        manifest={},
    )


def aggregate(
    tmp_path: Path,
    manifest: dict | str | None,
    *,
    repository: str = GOLDEN_REPOSITORY,
    source_commit: str = GOLDEN_COMMIT,
    now: datetime = FRESH_NOW,
) -> dict[str, str]:
    """Load + aggregate against a materialised target; return {id: status}."""
    root = make_target(tmp_path, manifest, repository=repository)
    loaded = pr.load_governed_actions_manifest(root)
    findings = pr.aggregate_governed_actions(loaded, source_commit, now)
    assert tuple(f.id for f in findings) == AGGREGATE_IDS
    return {f.id: f.status for f in findings}


def all_not_verified(statuses: dict[str, str]) -> bool:
    return all(statuses[fid] == "not-verified" for fid in AGGREGATE_IDS)


def validation_reason(tmp_path: Path, manifest: dict) -> str | None:
    root = make_target(tmp_path, manifest)
    loaded = pr.load_governed_actions_manifest(root)
    assert loaded is not None
    return pr._validate_governed_actions_manifest(
        loaded, GOLDEN_COMMIT, FRESH_NOW
    )


def test_legacy_evidence_contract_is_not_supported(tmp_path):
    manifest = _golden()
    manifest.pop("evidence_contract", None)
    assert all_not_verified(aggregate(tmp_path, manifest))


@pytest.mark.parametrize("family", ("approval", "output", "audit", "all"))
def test_removing_persisted_ledger_evidence_and_refs_never_passes(tmp_path, family):
    manifest = _golden()
    removed = {
        e["evidence_id"] for e in manifest["evidence"]
        if e["kind"].endswith("-ledger-records")
        and (family == "all" or e["kind"].startswith(family + "-"))
    }
    assert removed
    manifest["evidence"] = [e for e in manifest["evidence"] if e["evidence_id"] not in removed]
    for entry in manifest["findings"] + manifest["conformance"]["application_probes"]:
        entry["evidence_refs"] = [ref for ref in entry["evidence_refs"] if ref not in removed]
    assert all_not_verified(aggregate(tmp_path, manifest))


@pytest.mark.parametrize("probe_id", ("approval-anti-replay", "output-mediation", "payload-free-audit"))
def test_removing_probe_family_cannot_be_implicit_pass(tmp_path, probe_id):
    manifest = _golden()
    manifest["conformance"]["application_probes"] = [
        p for p in manifest["conformance"]["application_probes"] if p["probe_id"] != probe_id
    ]
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_partial_approval_sequence_cannot_pass_consumer(tmp_path):
    manifest = _golden()
    probes = manifest["conformance"]["application_probes"]
    probes.remove(next(p for p in probes if p["probe_id"] == "approval-anti-replay"))
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_nonpassing_probe_cannot_be_hidden_by_absent_findings(tmp_path):
    manifest = _golden()
    probe = next(p for p in manifest["conformance"]["application_probes"]
                 if p["probe_id"] == "output-mediation")
    probe["status"] = "not-verified"
    assert all_not_verified(aggregate(tmp_path, manifest))


DEPLOYED_TARGET = {
    "agent_name": "refund-agent", "agent_version": "3",
    "image_digest": "sha256:" + "b" * 64,
    "policy_digest": "sha256:" + "c" * 64,
    "environment": "staging", "subscription": "subscription-1",
    "resource_group": "rg-refund-staging",
}


def test_consumer_accepts_exact_local_deployment_binding_schema(tmp_path):
    manifest = _golden()
    manifest["deployed_target"] = dict(DEPLOYED_TARGET)
    for ref in manifest["evidence"]:
        ref["deployed_target"] = dict(DEPLOYED_TARGET)
        ref["target_environment"] = "staging"
    assert validation_reason(tmp_path, manifest) is None


@pytest.mark.parametrize("field", tuple(DEPLOYED_TARGET))
def test_consumer_rejects_live_deployment_binding_mismatch(tmp_path, field):
    manifest = _golden()
    manifest["deployed_target"] = dict(DEPLOYED_TARGET)
    cited = manifest["conformance"]["application_probes"][0]["evidence_refs"][0]
    ref = next(ref for ref in manifest["evidence"] if ref["evidence_id"] == cited)
    ref["live_verified"] = True
    ref["deployed_target"] = {**DEPLOYED_TARGET, field: "different"}
    ref["target_environment"] = "staging"
    reason = validation_reason(tmp_path, manifest)
    assert reason and "deployment" in reason


def test_consumer_never_upgrades_local_probe_to_live_enforcement(tmp_path):
    manifest = _golden()
    manifest["deployed_target"] = dict(DEPLOYED_TARGET)
    cited = manifest["conformance"]["application_probes"][0]["evidence_refs"][0]
    ref = next(ref for ref in manifest["evidence"] if ref["evidence_id"] == cited)
    ref.update(live_verified=True, deployed_target=dict(DEPLOYED_TARGET), target_environment="staging")
    reason = validation_reason(tmp_path, manifest)
    assert reason and ("local" in reason or "selected deployment" in reason)


# ---------------------------------------------------------------------------
# catalog + boundary: three aggregates, no duplicated child detail
# ---------------------------------------------------------------------------


def test_catalog_declares_exactly_the_three_aggregate_findings():
    for fid, pillar in EXPECTED_PILLARS.items():
        meta = pr.FINDING_CATALOG.get(fid)
        assert meta is not None, f"{fid} missing from FINDING_CATALOG"
        assert meta["pillar"] == pillar
        assert meta["tier"] == 0, f"{fid} reads an artefact; it is not a live probe"


def test_aggregate_ownership_map_is_exactly_the_declared_contract():
    assert pr.GOVERNED_ACTIONS_AGGREGATES.keys() == EXPECTED_AGGREGATES.keys()
    for fid, (domain, children) in EXPECTED_AGGREGATES.items():
        got_domain, got_children = pr.GOVERNED_ACTIONS_AGGREGATES[fid]
        assert got_domain == domain
        assert set(got_children) == children


def test_child_finding_ids_are_never_copied_into_the_detailed_catalog():
    """production-ready aggregates; it does not restate the child taxonomy."""
    duplicated = sorted(ALL_CHILD_IDS & set(pr.FINDING_CATALOG))
    assert not duplicated, (
        f"child governed-actions finding id(s) duplicated into production-ready's "
        f"own catalog: {duplicated}"
    )


def test_production_ready_never_invokes_the_governed_actions_probes():
    """The consumer reads an artefact. It must not run or import the assessor."""
    source = SCRIPT.read_text(encoding="utf-8")
    for module in ("probes", "mediation", "inventory", "ghcp",
                   "governed_actions", "maf_adapter", "scaffold"):
        assert f'import_module("{module}")' not in source
        assert f"\nimport {module}" not in source
        assert f"\nfrom {module} import" not in source
    # No path manipulation towards, or shelling out to, the producer's scripts.
    assert "threadlight-governed-actions/scripts" not in source
    assert 'threadlight-governed-actions", "scripts' not in source
    # The only governed-actions artefact production-ready may touch is the
    # emitted manifest, read from the TARGET repo — never the skill directory.
    assert source.count('"threadlight-governed-actions"') == 1, (
        "the assessor name should appear once, as the expected assessor identity"
    )


def test_aggregation_imports_no_governed_actions_module(tmp_path):
    before = set(sys.modules)
    aggregate(tmp_path, _golden())
    leaked = sorted(
        name for name in set(sys.modules) - before
        if name in {"probes", "mediation", "inventory", "ghcp",
                    "governed_actions", "maf_adapter", "render", "contracts"}
    )
    assert not leaked, f"consumer imported governed-actions module(s): {leaked}"


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_valid_governed_manifest_makes_all_three_aggregates_pass(tmp_path):
    statuses = aggregate(tmp_path, _golden())
    assert statuses == {"AGT-007": "pass", "HITL-008": "pass", "SUP-014": "pass"}


def test_loader_returns_none_when_the_manifest_is_absent(tmp_path):
    root = make_target(tmp_path, None)
    assert pr.load_governed_actions_manifest(root) is None


def test_loader_returns_the_parsed_manifest(tmp_path):
    root = make_target(tmp_path, _golden())
    loaded = pr.load_governed_actions_manifest(root)
    assert isinstance(loaded, dict)
    assert loaded["schema"] == "threadlight-governed-actions-manifest/v1"


# ---------------------------------------------------------------------------
# child status -> owning aggregate only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["must-fix", "should-fix", "not-verified"])
@pytest.mark.parametrize(
    "child,owner",
    [
        ("MED-001", "AGT-007"),
        ("MED-003", "AGT-007"),
        ("ENF-002", "AGT-007"),
        ("PIN-001", "AGT-007"),
        ("APR-001", "HITL-008"),
        ("OUT-001", "HITL-008"),
        ("AUD-001", "HITL-008"),
        ("GHCP-001", "SUP-014"),
        ("GHCP-006", "SUP-014"),
    ],
)
def test_child_status_maps_only_to_its_owning_aggregate(tmp_path, child, owner, status):
    statuses = aggregate(tmp_path, with_children((child, status)))
    assert statuses[owner] == status
    for other in AGGREGATE_IDS:
        if other != owner:
            assert statuses[other] == "pass", (
                f"{child} leaked out of {owner} into {other}"
            )


@pytest.mark.parametrize("status", ["must-fix", "should-fix", "not-verified"])
def test_ops_001_maps_to_governance_and_supply_chain(tmp_path, status):
    manifest = _golden()
    for finding in manifest["findings"]:
        if finding["finding_id"] == "OPS-001":
            finding["status"] = status
    statuses = aggregate(tmp_path, _rebuild_summary(manifest))
    assert statuses["AGT-007"] == status
    assert statuses["SUP-014"] == status
    assert statuses["HITL-008"] == "pass"


def test_not_applicable_child_does_not_hide_a_pass(tmp_path):
    statuses = aggregate(tmp_path, with_children(
        ("APR-001", "not-applicable"), ("OUT-001", "pass"),
    ))
    assert statuses["HITL-008"] == "pass"


def test_wholly_not_applicable_domain_stays_not_applicable(tmp_path):
    """`not-applicable` is last in precedence: it wins only when alone."""
    statuses = aggregate(tmp_path, with_children(("APR-001", "not-applicable")))
    assert statuses["HITL-008"] == "not-applicable"


def test_status_precedence_is_must_fix_over_not_verified_over_should_fix(tmp_path):
    statuses = aggregate(tmp_path, with_children(
        ("APR-001", "should-fix"), ("OUT-001", "not-verified"),
    ))
    assert statuses["HITL-008"] == "not-verified"

    statuses = aggregate(tmp_path, with_children(
        ("APR-001", "should-fix"), ("OUT-001", "not-verified"),
        ("AUD-001", "must-fix"),
    ))
    assert statuses["HITL-008"] == "must-fix"

    statuses = aggregate(tmp_path, with_children(
        ("APR-001", "should-fix"), ("OUT-001", "not-applicable"),
    ))
    assert statuses["HITL-008"] == "should-fix"


def test_summary_verdict_alone_is_never_trusted(tmp_path):
    """A `governed` verdict over a must-fix child is still a must-fix."""
    manifest = with_children(("MED-001", "must-fix"))
    assert manifest["summary"]["verdict"] == "governed"
    statuses = aggregate(tmp_path, manifest)
    assert statuses["AGT-007"] == "must-fix"


# ---------------------------------------------------------------------------
# untrusted manifests: never pass, always not-verified
# ---------------------------------------------------------------------------


def test_missing_manifest_is_not_verified(tmp_path):
    assert all_not_verified(aggregate(tmp_path, None))


def test_unparseable_manifest_is_not_verified(tmp_path):
    assert all_not_verified(aggregate(tmp_path, "{not json"))


def test_non_object_manifest_is_not_verified(tmp_path):
    assert all_not_verified(aggregate(tmp_path, "[]"))


def test_wrong_schema_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["schema"] = "threadlight-governed-actions-manifest/v2"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_application_probe_missing_mode_is_not_verified(tmp_path):
    manifest = _with_probe_modes()
    del manifest["conformance"]["application_probes"][0]["mode"]
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_approval_probe_missing_action_id_is_not_verified(tmp_path):
    manifest = _golden()
    probe = next(p for p in manifest["conformance"]["application_probes"]
                 if p["probe_id"] == "approval-anti-replay")
    del probe["action_id"]
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_application_probe_malformed_mode_is_not_verified(tmp_path):
    manifest = _with_probe_modes()
    manifest["conformance"]["application_probes"][0]["mode"] = 42
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_path_probe_mode_must_match_mediation_path(tmp_path):
    manifest = _with_probe_modes()
    probe = next(
        item
        for item in manifest["conformance"]["application_probes"]
        if item["path_id"] is not None
    )
    probe["mode"] = "provider-hosted-tool"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_unknown_top_level_key_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["extra"] = {"forged": True}
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_missing_required_section_is_not_verified(tmp_path):
    manifest = _golden()
    del manifest["policy_hashes"]
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_unsupported_assessor_version_is_not_verified(tmp_path):
    """No broad forward trust: an unreviewed future assessor is not believed."""
    manifest = _golden()
    manifest["assessor"]["version"] = "9.9.9"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_foreign_assessor_name_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["assessor"]["name"] = "some-other-assessor"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_design_phase_manifest_is_not_verified(tmp_path):
    """Design-phase evidence never certifies a deployable pilot."""
    manifest = _golden()
    manifest["phase"] = "design"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_post_deploy_phase_is_accepted(tmp_path):
    manifest = _golden()
    manifest["phase"] = "post-deploy"
    statuses = aggregate(tmp_path, manifest)
    assert statuses == {"AGT-007": "pass", "HITL-008": "pass", "SUP-014": "pass"}


def test_dirty_source_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["source"]["dirty"] = True
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_source_commit_mismatch_is_not_verified(tmp_path):
    assert all_not_verified(aggregate(
        tmp_path, _golden(), source_commit="f" * 40))


def test_unknown_target_commit_is_not_verified(tmp_path):
    assert all_not_verified(aggregate(tmp_path, _golden(), source_commit=""))


def test_repository_mismatch_is_not_verified(tmp_path):
    assert all_not_verified(aggregate(
        tmp_path, _golden(), repository="octo-org/some-other-repo"))


def test_policy_hash_drift_is_not_verified(tmp_path):
    """The consumer recomputes the policy hashes from the target repo."""
    root = make_target(tmp_path, _golden())
    policy = root / "governance" / "alerts.json"
    policy.write_text(policy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    loaded = pr.load_governed_actions_manifest(root)
    findings = pr.aggregate_governed_actions(loaded, GOLDEN_COMMIT, FRESH_NOW)
    assert all_not_verified({f.id: f.status for f in findings})


def test_missing_policy_file_is_not_verified(tmp_path):
    root = make_target(tmp_path, _golden())
    (root / "governance" / "probe-contract.json").unlink()
    loaded = pr.load_governed_actions_manifest(root)
    findings = pr.aggregate_governed_actions(loaded, GOLDEN_COMMIT, FRESH_NOW)
    assert all_not_verified({f.id: f.status for f in findings})


def test_manifest_policy_set_binding_mismatch_is_not_verified(tmp_path):
    """Referenced evidence must bind to the policy set the manifest records."""
    manifest = _golden()
    for entry in manifest["evidence"]:
        if entry["policy_set_sha256"] is not None:
            entry["policy_set_sha256"] = "sha256:" + "0" * 64
            break
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_referenced_evidence_repository_mismatch_is_not_verified(tmp_path):
    manifest = _golden()
    for entry in manifest["evidence"]:
        if entry["evidence_id"].startswith("audit-0001~"):
            entry["repository"] = "octo-org/elsewhere"
            break
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_referenced_evidence_commit_mismatch_is_not_verified(tmp_path):
    manifest = _golden()
    for entry in manifest["evidence"]:
        if entry["evidence_id"].startswith("audit-0001~"):
            entry["source_commit"] = "a" * 40
            break
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_unresolvable_evidence_reference_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["findings"][0]["evidence_refs"] = ["EVID-does-not-exist"]
    assert all_not_verified(aggregate(tmp_path, _rebuild_summary(manifest)))


def test_evidence_collected_after_capture_is_not_verified(tmp_path):
    manifest = _golden()
    for entry in manifest["evidence"]:
        if entry["evidence_id"].startswith("audit-0001~"):
            entry["collected_at"] = "2026-09-01T12:00:01Z"
            break
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_target_environment_mismatch_is_not_verified(tmp_path):
    """Evidence from another environment never speaks for the selected one."""
    root = make_target(tmp_path, None)
    env = root / ".azure" / "prod"
    env.mkdir(parents=True)
    (env / ".env").write_text('AZURE_ENV_NAME="prod"\n', encoding="utf-8")
    manifest = _golden()
    for entry in manifest["evidence"]:
        if entry["evidence_id"].startswith("audit-0001~"):
            entry["target_environment"] = "staging"
    write_manifest(root, manifest)
    loaded = pr.load_governed_actions_manifest(root)
    findings = pr.aggregate_governed_actions(loaded, GOLDEN_COMMIT, FRESH_NOW)
    assert all_not_verified({f.id: f.status for f in findings})


def test_inconsistent_target_environments_are_not_verified(tmp_path):
    manifest = _golden()
    seen = 0
    for entry in manifest["evidence"]:
        if entry["evidence_id"].startswith("audit-0001~"):
            entry["target_environment"] = "staging" if seen else "prod"
            seen += 1
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_stale_manifest_is_not_verified(tmp_path):
    assert all_not_verified(aggregate(
        tmp_path, _golden(), now=FRESH_NOW + timedelta(days=3)))


def test_stale_freshness_status_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["freshness"]["status"] = "stale"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_freshness_window_that_does_not_add_up_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["freshness"]["expires_at"] = "2026-09-30T12:00:00Z"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_capture_in_the_future_is_not_verified(tmp_path):
    assert all_not_verified(aggregate(
        tmp_path, _golden(), now=FRESH_NOW - timedelta(days=2)))


def test_summary_count_mismatch_is_not_verified(tmp_path):
    """A summary that under-reports its own findings is not trusted."""
    manifest = with_children(("MED-001", "must-fix"))
    manifest["summary"]["must_fix"] = []
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_summary_status_mismatch_is_not_verified(tmp_path):
    manifest = with_children(("MED-001", "must-fix"))
    manifest["summary"]["must_fix"] = []
    manifest["summary"]["pass"] = sorted(manifest["summary"]["pass"] + ["MED-001"])
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_summary_referencing_an_absent_finding_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["summary"]["must_fix"] = ["APR-001"]
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_malformed_finding_entry_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["findings"].append("not-an-object")
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_non_list_mediation_paths_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["mediation_paths"] = {"not": "an array"}
    assert validation_reason(tmp_path, manifest) == "mediation_paths must be an array"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_non_object_mediation_path_entry_degrades_all_aggregates_without_exception(tmp_path):
    manifest = _golden()
    manifest["mediation_paths"].insert(0, "not-an-object")
    assert (
        validation_reason(tmp_path, manifest)
        == "mediation_paths entries must be objects"
    )
    root = make_target(tmp_path, manifest)
    loaded = pr.load_governed_actions_manifest(root)
    findings = pr.aggregate_governed_actions(loaded, GOLDEN_COMMIT, FRESH_NOW)
    expected_reason = (
        "governed-actions manifest not trusted: "
        "mediation_paths entries must be objects."
    )
    for finding in findings:
        assert finding.status == "not-verified"
        assert finding.detail == expected_reason


def test_malformed_mediation_path_evidence_refs_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["mediation_paths"][0]["evidence_refs"] = [17]
    assert (
        validation_reason(tmp_path, manifest)
        == "mediation_paths has malformed evidence_refs"
    )
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_dangling_mediation_path_evidence_ref_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["mediation_paths"][0]["evidence_refs"].append(
        "sha256:" + "d" * 64
    )
    assert validation_reason(
        tmp_path, manifest
    ) == "a finding or probe cites evidence that is not in the manifest"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_unknown_child_finding_id_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["findings"].append(_child_finding("ACT-001", "pass"))
    manifest["findings"][-1]["finding_id"] = "XXX-999"
    manifest["summary"]["pass"] = sorted(manifest["summary"]["pass"] + ["XXX-999"])
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_invalid_child_status_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["findings"].append(_child_finding("ACT-001", "pass"))
    manifest["findings"][-1]["status"] = "waived"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_empty_policy_set_is_not_verified(tmp_path):
    """With no recorded policy set there is nothing to bind evidence to."""
    manifest = _golden()
    manifest["policy_hashes"] = []
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_untrusted_manifest_detail_never_claims_verification(tmp_path):
    root = make_target(tmp_path, None)
    findings = pr.aggregate_governed_actions(
        pr.load_governed_actions_manifest(root), GOLDEN_COMMIT, FRESH_NOW)
    for finding in findings:
        assert finding.status == "not-verified"
        assert finding.detail, "a not-verified aggregate must say why"


# ---------------------------------------------------------------------------
# pillar wiring
# ---------------------------------------------------------------------------


def test_each_aggregate_is_folded_into_exactly_one_pillar(tmp_path):
    root = make_target(tmp_path, _golden())
    ctx = _ctx(root)
    seen: dict[str, list[str]] = {}
    for pillar in pr.PILLAR_IDS:
        for finding in pr._governed_actions_findings_for_pillar(ctx, pillar):
            seen.setdefault(finding.id, []).append(pillar)
    assert {fid: pillars[0] for fid, pillars in seen.items()} == EXPECTED_PILLARS
    assert all(len(pillars) == 1 for pillars in seen.values())


# ---------------------------------------------------------------------------
# freshness window: the producer anchors expiry to the OLDEST relied-upon
# evidence instant, never to its own capture instant. A consumer that
# re-derives it from `captured_at` rejects every real manifest, because
# evidence is always collected before the manifest is rendered.
# ---------------------------------------------------------------------------


def test_expiry_is_anchored_to_oldest_source_at_not_captured_at(tmp_path):
    manifest = _golden()
    # Producer shape: evidence gathered at 12:00, manifest rendered at 13:30,
    # so expires_at is 12:00 + 24h — NOT 13:30 + 24h.
    manifest["captured_at"] = "2026-09-01T13:30:00Z"
    assert manifest["freshness"]["oldest_source_at"] == "2026-09-01T12:00:00Z"
    assert manifest["freshness"]["expires_at"] == "2026-09-02T12:00:00Z"
    statuses = aggregate(tmp_path, manifest, now=FRESH_NOW)
    assert statuses == {fid: "pass" for fid in AGGREGATE_IDS}


def test_expires_at_not_derived_from_oldest_source_at_is_not_verified(tmp_path):
    manifest = _golden()
    manifest["freshness"]["expires_at"] = "2026-09-05T12:00:00Z"
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_capture_after_the_expiry_window_is_not_verified(tmp_path):
    """`status: fresh` is a claim; captured_at past expires_at disproves it."""
    manifest = _golden()
    manifest["captured_at"] = "2026-09-03T00:00:00Z"
    statuses = aggregate(
        tmp_path, manifest, now=datetime(2026, 9, 3, 1, 0, tzinfo=timezone.utc)
    )
    assert all_not_verified(statuses)


# ---------------------------------------------------------------------------
# evidence phase binds to the CITING FINDING's phase, not to a fixed allowlist
# ---------------------------------------------------------------------------


def _evidence(manifest: dict, evidence_id: str) -> dict:
    for entry in manifest["evidence"]:
        if entry["evidence_id"] == evidence_id:
            return entry
    raise AssertionError(f"{evidence_id} not in the golden evidence pack")


def test_design_phase_evidence_cited_by_a_design_phase_finding_is_trusted(tmp_path):
    """A static-source citation is bound to the phase that actually read it."""
    manifest = _golden()
    entry = _evidence(manifest, "EVID-spec-section-8")
    assert entry["phase"] == "design"
    entry["collected_at"] = manifest["captured_at"]
    entry["repository"] = manifest["source"]["repository"]
    entry["source_commit"] = manifest["source"]["commit"]
    child = _child_finding("MED-002", "pass")
    child["phase"] = "design"
    child["evidence_refs"] = ["EVID-spec-section-8"]
    manifest["findings"].append(child)
    statuses = aggregate(tmp_path, _rebuild_summary(manifest))
    assert statuses == {fid: "pass" for fid in AGGREGATE_IDS}


def test_evidence_phase_matching_no_citing_finding_is_not_verified(tmp_path):
    manifest = _golden()
    entry = _evidence(manifest, "EVID-spec-section-8")
    entry["collected_at"] = manifest["captured_at"]
    entry["repository"] = manifest["source"]["repository"]
    entry["source_commit"] = manifest["source"]["commit"]
    child = _child_finding("MED-002", "pass")
    child["phase"] = "post-deploy"
    child["evidence_refs"] = ["EVID-spec-section-8"]
    manifest["findings"].append(child)
    assert all_not_verified(aggregate(tmp_path, _rebuild_summary(manifest)))


def test_null_policy_set_binding_on_relied_upon_evidence_is_tolerated(tmp_path):
    """The schema declares policy_set_sha256 nullable; the producer allows it."""
    manifest = _golden()
    entry = _evidence(manifest, "EVID-spec-section-8")
    assert entry["policy_set_sha256"] is None
    entry["phase"] = "pre-deploy"
    entry["collected_at"] = manifest["captured_at"]
    entry["repository"] = manifest["source"]["repository"]
    entry["source_commit"] = manifest["source"]["commit"]
    child = _child_finding("ACT-002", "pass")
    child["evidence_refs"] = ["EVID-spec-section-8"]
    manifest["findings"].append(child)
    statuses = aggregate(tmp_path, _rebuild_summary(manifest))
    assert statuses == {fid: "pass" for fid in AGGREGATE_IDS}


# ---------------------------------------------------------------------------
# untrusted JSON of the wrong TYPE must degrade, never raise
# ---------------------------------------------------------------------------

UNHASHABLE_FIELDS = [
    ("assessor-version", lambda m: m["assessor"].__setitem__("version", [])),
    ("manifest-phase", lambda m: m.__setitem__("phase", ["pre-deploy"])),
    ("finding-id", lambda m: m["findings"][0].__setitem__("finding_id", {})),
    ("finding-status", lambda m: m["findings"][0].__setitem__("status", [])),
    ("finding-phase", lambda m: m["findings"][0].__setitem__("phase", [])),
    ("finding-plane", lambda m: m["findings"][0].__setitem__("plane", [1])),
    ("evidence-phase", lambda m: [e.__setitem__("phase", []) for e in m["evidence"]]),
    ("summary-verdict", lambda m: m["summary"].__setitem__("verdict", {})),
    ("schema", lambda m: m.__setitem__("schema", ["x"])),
    ("source-dirty", lambda m: m["source"].__setitem__("dirty", [])),
    ("freshness-status", lambda m: m["freshness"].__setitem__("status", [])),
]


@pytest.mark.parametrize("label,mutate", UNHASHABLE_FIELDS, ids=[f[0] for f in UNHASHABLE_FIELDS])
def test_unhashable_json_values_degrade_instead_of_raising(tmp_path, label, mutate):
    manifest = _golden()
    mutate(manifest)
    assert all_not_verified(aggregate(tmp_path, manifest))


def test_pillar_wiring_reports_a_trusted_manifest_verdict(tmp_path):
    """End-to-end: a genuinely trustworthy manifest reaches the pillar fold."""
    root = make_committed_target(tmp_path, with_children(("AUD-001", "must-fix")))
    ctx = _ctx(root)
    assert [
        (f.id, f.status)
        for f in pr._governed_actions_findings_for_pillar(ctx, "hitl-audit")
    ] == [("HITL-008", "must-fix")]
    assert [
        (f.id, f.status)
        for f in pr._governed_actions_findings_for_pillar(ctx, "agent-governance")
    ] == [("AGT-007", "pass")]


def test_pillar_wiring_passes_a_trusted_conformant_manifest(tmp_path):
    root = make_committed_target(tmp_path, _golden())
    ctx = _ctx(root)
    for pillar, fid in (
        ("agent-governance", "AGT-007"),
        ("hitl-audit", "HITL-008"),
        ("supply-chain", "SUP-014"),
    ):
        findings = pr._governed_actions_findings_for_pillar(ctx, pillar)
        assert [(f.id, f.status) for f in findings] == [(fid, "pass")]


def test_aggregation_never_mutates_the_caller_manifest(tmp_path):
    root = make_target(tmp_path, _golden())
    loaded = pr.load_governed_actions_manifest(root)
    snapshot = copy.deepcopy(loaded)
    pr.aggregate_governed_actions(loaded, GOLDEN_COMMIT, FRESH_NOW)
    assert loaded == snapshot


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
