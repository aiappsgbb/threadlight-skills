"""AOPS is opt-in operational evidence, never a second domain score."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("production_ready_agentops", ROOT / "scripts/production_ready.py")
pr = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pr
spec.loader.exec_module(pr)


def context(root):
    return pr.RepoContext(
        root=root, bicep_files=[], src_files=[], test_files=[], spec_text="",
        spec_12={}, spec_11b={}, azure_yaml_text="", docs_text="", azd_env={},
        manifest={}, bicep_graph=pr.BicepGraph([], []),
    )


def opt_in(root):
    (root / "agentops.yaml").write_text("version: 1\nagent: support:1\n")


def test_no_opt_in_is_non_scoring_even_with_stray_manifest(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/agentops-manifest.json").write_text('{"status":"blocked"}')
    finding = pr._check_agentops_manifest(context(tmp_path))
    assert finding.id == "AOPS-001"
    assert finding.pillar == "sre-handover"
    assert finding.status == "not-applicable"


def test_opt_in_missing_manifest_is_not_verified(tmp_path):
    opt_in(tmp_path)
    finding = pr._check_agentops_manifest(context(tmp_path))
    assert finding.status == "not-verified"
    assert "threadlight-agentops" in finding.detail


def test_unvalidated_manifest_cannot_pass(tmp_path):
    opt_in(tmp_path)
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/agentops-manifest.json").write_text(json.dumps({
        "schema": "threadlight-agentops-manifest/v1",
        "generated_at": "2999-01-01T00:00:00Z",
        "status": "pass", "verdict": "operational",
        "agents": [], "findings": [],
    }))
    assert pr._check_agentops_manifest(context(tmp_path)).status == "not-verified"


def test_sre_static_includes_aops_without_replacing_existing_checks(tmp_path):
    found = {f.id: f for f in pr._check_sre_static(context(tmp_path))}
    assert set(found) == {"SRE-001", "SRE-002", "SRE-003", "SRE-004", "SRE-005", "AOPS-001"}


def test_aops_recipe_pins_merged_sibling_not_certification():
    recipe = (ROOT / "references/remediation-recipes/AOPS-001.md").read_text()
    assert "sibling_skill: foundry-agentops" in recipe
    assert "2db28d1f52bf288f2d0fd40b7c8beb913ceeee09" in recipe
    assert "0.14.0" in recipe
    assert "not certification" in recipe.lower()


def test_production_ready_never_imports_native_producer():
    text = (ROOT / "scripts/production_ready.py").read_text()
    assert "import agentops_check" not in text
    assert '.agentops/results/' not in text


def agent(key="a", *, finding=None, blocker=None, status="verified"):
    return {
        "agent_key": key, "root": key, "verdict": "operational",
        "capabilities": {"config": {"status": status}},
        "findings": [finding] if finding else [],
        "provenance": {"artifacts": {"bound/receipt.json": "f" * 64}, "receipt_sha256": "e" * 64},
        "domains": {"evals": {"status": "verified", "verdict": "fail" if blocker else "pass",
                              "blockers": [blocker] if blocker else []}},
    }


def validated(monkeypatch, agents):
    document = {"agents": agents, "generated_at": datetime.now(timezone.utc).isoformat()}
    real = pr._agentops_contract()
    monkeypatch.setattr(pr, "_agentops_contract", lambda: SimpleNamespace(
        discover_opted_in_agents=lambda root: [{"root": item["root"]} for item in agents],
        load_manifest=lambda root: document, read_json=real.read_json,
        canonical_hash=real.canonical_hash,
    ))
    return document


def canonical_evals(root, document, *, source_hash=None, status="must-fix"):
    contract = pr._agentops_contract()
    names = {
        "eval_scenarios_present": "EVAL-001", "eval_datasets_present": "EVAL-002",
        "dataset_shape_ok": "EVAL-003", "thresholds_declared": "EVAL-004",
        "schedule_present": "EVAL-005", "run_history_present": "EVAL-006",
        "online_eval_wired": "EVAL-101", "latest_eval_run_fresh": "EVAL-102/EVAL-103",
        "alert_wired": "EVAL-104", "latest_pass_rate_ok": "EVAL-105",
        "ab_comparison_present": "F3",
    }
    records = [{
        "agent_key": item["agent_key"], "root": item["root"],
        "source": "specs/agentops-manifest.json",
        "source_manifest_sha256": source_hash or contract.canonical_hash(document),
        "capabilities": {"latest_pass_rate_ok": status},
        "evidence_refs": sorted(item["provenance"]["artifacts"]),
        "artifacts": item["provenance"]["artifacts"],
        "receipt_sha256": item["provenance"]["receipt_sha256"],
        "domain_status": "verified", "represented_blockers": ["AOPS-EVAL-QUALITY"],
    } for item in document["agents"]]
    capabilities = {key: {"check_id": value, "status": "pass"} for key, value in names.items()}
    capabilities["latest_pass_rate_ok"].update(
        status=status, sources=[{
            "source": "specs/agentops-manifest.json", "agent_key": item["agent_key"],
            "status": status, "evidence_refs": sorted(item["provenance"]["artifacts"]),
        } for item in document["agents"]],
    )
    result = {
        "schema": "threadlight-evals-manifest/v1", "tool_version": "0.3.0",
        "captured_at": datetime.now(timezone.utc).isoformat(), "freshness_window_days": 7,
        "verdict": "offline-only" if status == "must-fix" else "comprehensive",
        "capabilities": capabilities, "metrics": {},
        "must_fix": ["latest_pass_rate_ok"] if status == "must-fix" else [],
        "should_fix": [], "not_verified": [],
        "agentops": {"source": "specs/agentops-manifest.json", "agents": records,
                     "source_manifest_sha256": source_hash or contract.canonical_hash(document)},
    }
    (root / "specs").mkdir(exist_ok=True)
    (root / "specs/evals-manifest.json").write_text(json.dumps(result))
    return result


def test_worst_per_agent_includes_unknown_operational_blocker(tmp_path, monkeypatch):
    validated(monkeypatch, [agent("a"), agent("b", finding={
        "code": "AOPS-DOCTOR-BLOCKED", "owner": "evals", "severity": "must-fix",
    })])
    assert pr._check_agentops_manifest(context(tmp_path)).status == "must-fix"


def test_domain_name_alone_cannot_hide_blocker(tmp_path, monkeypatch):
    validated(monkeypatch, [agent(blocker={
        "code": "AOPS-EVAL-QUALITY", "owner": "evals", "severity": "must-fix",
    })])
    assert pr._check_agentops_manifest(context(tmp_path)).status == "must-fix"


def test_exact_quality_blocker_with_canonical_proof_is_not_double_scored(tmp_path, monkeypatch):
    document = validated(monkeypatch, [agent(blocker={
        "code": "AOPS-EVAL-QUALITY", "owner": "evals", "severity": "must-fix",
    })])
    canonical_evals(tmp_path, document)
    finding = pr._check_agentops_manifest(context(tmp_path))
    assert finding.status == "pass"
    assert "canonical" in finding.detail


def test_native_blocked_with_only_proved_quality_finding_is_not_an_operations_block(tmp_path, monkeypatch):
    document = validated(monkeypatch, [agent(finding={
        "code": "AOPS-EVAL-QUALITY", "owner": "evals", "severity": "must-fix",
    })])
    document["agents"][0]["verdict"] = "blocked"
    document["agents"][0]["domains"]["evals"]["verdict"] = "fail"
    canonical_evals(tmp_path, document)
    assert pr._check_agentops_manifest(context(tmp_path)).status == "pass"


def test_forged_mapping_or_stale_source_cannot_deduplicate(tmp_path, monkeypatch):
    document = validated(monkeypatch, [agent(blocker={
        "code": "AOPS-EVAL-QUALITY", "owner": "evals", "severity": "must-fix",
    })])
    canonical_evals(tmp_path, document, source_hash="0" * 64)
    assert pr._check_agentops_manifest(context(tmp_path)).status == "must-fix"
    canonical_evals(tmp_path, document, status="pass")
    assert pr._check_agentops_manifest(context(tmp_path)).status == "must-fix"
    canonical_evals(tmp_path, document)
    document["agents"][0]["domains"]["evals"]["blockers"][0]["code"] = "unknown-operational-blocker"
    assert pr._check_agentops_manifest(context(tmp_path)).status == "must-fix"


def test_invalid_canonical_counts_do_not_hide_quality_blocker(tmp_path, monkeypatch):
    document = validated(monkeypatch, [agent(blocker={
        "code": "AOPS-EVAL-QUALITY", "owner": "evals", "severity": "must-fix",
    })])
    canonical = canonical_evals(tmp_path, document)
    canonical["must_fix"] = []
    (tmp_path / "specs/evals-manifest.json").write_text(json.dumps(canonical))
    assert pr._check_agentops_manifest(context(tmp_path)).status == "must-fix"


def test_forged_representation_string_is_not_consumer_proof(tmp_path, monkeypatch):
    document = validated(monkeypatch, [agent(blocker={
        "code": "AOPS-EVAL-QUALITY", "owner": "evals", "severity": "must-fix",
    })])
    canonical = canonical_evals(tmp_path, document)
    canonical["agentops"]["agents"][0]["represented_blockers"] = "AOPS-EVAL-QUALITY"
    (tmp_path / "specs/evals-manifest.json").write_text(json.dumps(canonical))
    assert pr._check_agentops_manifest(context(tmp_path)).status == "must-fix"


def test_real_shared_manifest_is_consumed_and_omitted_agent_rejected(tmp_path):
    contract = pr._agentops_contract()
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        opt_in(tmp_path / name)
    (tmp_path / "specs").mkdir()
    document = contract.assess_repository(tmp_path)
    target = tmp_path / "specs/agentops-manifest.json"
    target.write_text(json.dumps(document))
    assert pr._check_agentops_manifest(context(tmp_path)).status == "not-verified"
    document["agents"].pop()
    document["summary"]["agents_total"] -= 1
    target.write_text(json.dumps(document))
    assert pr._check_agentops_manifest(context(tmp_path)).status == "not-verified"
    assert "invalid" in pr._check_agentops_manifest(context(tmp_path)).detail


def test_real_shared_multiagent_hard_failure_wins(tmp_path):
    contract = pr._agentops_contract()
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        opt_in(tmp_path / name)
    native = tmp_path / "b/.agentops/results/latest/results.json"
    native.parent.mkdir(parents=True)
    native.write_text('{"version":1,"rows":"invalid"}')
    (tmp_path / "specs").mkdir()
    document = contract.assess_repository(tmp_path)
    (tmp_path / "specs/agentops-manifest.json").write_text(json.dumps(document))
    assert pr._check_agentops_manifest(context(tmp_path)).status == "must-fix"


def test_non_opt_in_leaves_sre_score_unchanged(tmp_path):
    checks = pr._check_sre_static(context(tmp_path))
    original = [finding for finding in checks if finding.id != "AOPS-001"]
    assert pr._score_pillar(checks) == pr._score_pillar(original)


def real_fixture(root, state="healthy"):
    path = ROOT.parent / "threadlight-agentops/tests/fixture_helpers.py"
    fixture_spec = importlib.util.spec_from_file_location("readiness_agentops_fixtures", path)
    helpers = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(helpers)
    return helpers.create_agentops_fixture(root, state=state, redteam=False)


def test_real_signed_healthy_manifest_reaches_operations_pass(tmp_path):
    real_fixture(tmp_path)
    finding = pr._check_agentops_manifest(context(tmp_path))
    assert finding.status == "pass", finding.detail
    assert "not deployment certification" in finding.detail


def test_real_domain_consumer_receipt_represents_quality_only(tmp_path):
    document = real_fixture(tmp_path, "quality-fail")
    # Generated canonical evidence is not application source.
    (tmp_path / ".git/info/exclude").write_text("specs/evals-manifest.json\n")
    script = ROOT.parent / "threadlight-evals/scripts/evals_check.py"
    eval_spec = importlib.util.spec_from_file_location("readiness_real_evals", script)
    evals = importlib.util.module_from_spec(eval_spec)
    eval_spec.loader.exec_module(evals)
    result = evals.manifest(str(tmp_path), evals.evaluate(str(tmp_path)))
    (tmp_path / "specs/evals-manifest.json").write_text(json.dumps(result))
    contract = pr._agentops_contract()
    current = contract.load_manifest(tmp_path)
    assert current == document
    selected = current["agents"][0]
    blocker = selected["domains"]["evals"]["blockers"][0]
    assert pr._agentops_domain_blocker_represented(context(tmp_path), current, selected, blocker)
    assert current["verdict"] == "blocked"
    finding = pr._check_agentops_manifest(context(tmp_path))
    assert finding.status == "pass", finding.detail
    assert "canonical evals" in finding.detail
