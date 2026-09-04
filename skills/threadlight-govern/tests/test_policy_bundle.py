"""All marked tests use the real pinned native loader, never a mock dispatcher."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills/threadlight-govern/scripts"
TEMPLATES = ROOT / "skills/threadlight-govern/references/policy-templates"
sys.path.insert(0, str(SCRIPTS))


def bundle_module():
    assert (SCRIPTS / "policy_bundle.py").exists(), "native bundle builder missing"
    return importlib.import_module("policy_bundle")


def test_native_template_replaces_retired_conditions_templates():
    assert (TEMPLATES / "manifest.yaml").is_file(), "native ACS manifest missing"
    assert (TEMPLATES / "safe.rego").is_file()
    assert not list(TEMPLATES.glob("*.policy.yaml"))


@pytest.mark.parametrize("report", [
    "<testsuites/>",
    '<testsuites><testsuite><testcase name="test_real_native_loader_accepts_generated_bundle"><skipped/></testcase></testsuite></testsuites>',
    '<testsuites><testsuite><testcase name="unrelated"/></testsuite></testsuites>',
    '<testsuites><testsuite><testcase name="test_real_native_loader_accepts_generated_bundle"><failure/></testcase></testsuite></testsuites>',
])
def test_pin_gate_rejects_empty_skipped_failed_or_incomplete_proof(tmp_path, report):
    import importlib.util
    path = ROOT / "scripts/ci/run-governance-pin-tests.py"
    assert path.exists()
    spec = importlib.util.spec_from_file_location("governance_pin_runner", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    junit = tmp_path / "report.xml"
    junit.write_text(report)
    with pytest.raises(RuntimeError):
        runner.verify_junit(junit)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    shutil.copytree(TEMPLATES, root)
    return root


@pytest.fixture
def built(source, tmp_path):
    return bundle_module().build_bundle(
        source=source, destination=tmp_path / "bundle",
        policy_id="returns-safe", version="1.0.0",
    )


@pytest.mark.governance_runtime
def test_real_native_loader_accepts_generated_bundle(built):
    from agent_control_specification import AgentControl
    assert AgentControl.from_path(str(built.manifest_path)) is not None
    assert built.manifest_path == built.root / "manifest.yaml"


@pytest.mark.governance_runtime
@pytest.mark.parametrize("case,point,tool,args,safe,decision", [
    ("missing-evidence", "pre_tool_call", "returns_apply_decision",
     {"refund_amount": 50}, {}, "deny"),
    ("refund-allow", "pre_tool_call", "returns_apply_decision",
     {"refund_amount": 50}, {"evidence": {"receipt_verified": True}}, "allow"),
    ("large-refund", "pre_tool_call", "returns_apply_decision",
     {"refund_amount": 501}, {"evidence": {"receipt_verified": True}}, "escalate"),
    ("approved-refund", "pre_tool_call", "returns_apply_decision",
     {"refund_amount": 501}, {"evidence": {"receipt_verified": True},
                             "escalations": {"refund_approved": True}}, "allow"),
    ("unrelated-tool", "pre_tool_call", "search_catalog", {}, {}, "allow"),
    ("model-approval-is-untrusted", "pre_tool_call", "returns_apply_decision",
     {"refund_amount": 501, "refund_approved": True},
     {"evidence": {"receipt_verified": True}}, "escalate"),
    ("model-evidence-is-untrusted", "pre_tool_call", "returns_apply_decision",
     {"refund_amount": 50, "receipt_verified": True}, {}, "deny"),
    ("invalid-amount", "pre_tool_call", "returns_apply_decision",
     {"refund_amount": -1}, {"evidence": {"receipt_verified": True}}, "deny"),
    ("missing-amount", "pre_tool_call", "returns_apply_decision",
     {}, {"evidence": {"receipt_verified": True}}, "deny"),
    ("post-tool-transform", "post_tool_call", "returns_apply_decision",
     {}, {}, "transform"),
    ("output-transform", "output", "returns_apply_decision", {}, {}, "transform"),
])
def test_real_policy_decisions(built, case, point, tool, args, safe, decision):
    from agent_control_specification import AgentControl
    engine = AgentControl.from_path(str(built.manifest_path))
    snapshot = {
        "tool_call": {"name": tool, "args": args},
        "tool_result": {"refund_id": "r-1", "customer_email": "private@example.invalid"},
        "output": {"text": "Refund processed", "customer_email": "private@example.invalid"},
        "safe": safe,
    }
    result = asyncio.run(engine.evaluate_intervention_point(point, snapshot))
    assert result.verdict.decision.value == decision, (case, result)
    assert result.verdict.reason and not result.verdict.reason.startswith("runtime_error")
    if decision == "transform":
        assert result.transformed_policy_target_applied
        assert "customer_email" not in result.transformed_policy_target
        assert result.transformed_policy_target != snapshot[
            "output" if point == "output" else "tool_result"
        ]


@pytest.mark.governance_runtime
def test_missing_opa_is_not_a_valid_policy_decision(built, monkeypatch):
    from agent_control_specification import AgentControl
    monkeypatch.setenv("ACS_OPA_PATH", str(built.root / "missing-opa"))
    engine = AgentControl.from_path(str(built.manifest_path))
    result = asyncio.run(engine.evaluate_intervention_point(
        "pre_tool_call", {"tool_call": {"name": "returns_apply_decision", "args": {}}}
    ))
    assert result.verdict.decision.value == "deny"
    assert "runtime_error" in result.verdict.reason


@pytest.mark.governance_runtime
def test_digest_is_deterministic_and_metadata_is_unsigned(source, tmp_path):
    pb = bundle_module()
    a = pb.build_bundle(source=source, destination=tmp_path / "a",
                        policy_id="returns-safe", version="1.0.0")
    b = pb.build_bundle(source=source, destination=tmp_path / "b",
                        policy_id="returns-safe", version="1.0.0")
    assert a.bundle_digest == b.bundle_digest
    assert a.files == b.files
    metadata = json.loads((a.root / "bundle-metadata.json").read_text())
    assert metadata["schema"] == "threadlight-policy-bundle/v1"
    assert metadata["policy_id"] == "returns-safe"
    assert metadata["version"] == "1.0.0"
    assert metadata["bundle_digest"] == a.bundle_digest
    assert metadata["signature"] is None
    assert {f["path"] for f in metadata["files"]} == {"manifest.yaml", "safe.rego"}
    assert pb.verify_bundle(a.root, expected_digest=a.bundle_digest) == a


@pytest.mark.governance_runtime
@pytest.mark.parametrize("mutation", ["bytes", "extra", "delete", "metadata", "signature", "symlink"])
def test_tampering_is_rejected(built, mutation):
    pb = bundle_module()
    if mutation == "bytes":
        with (built.root / "safe.rego").open("a") as stream:
            stream.write("\n# altered\n")
    elif mutation == "extra":
        (built.root / "extra.rego").write_text("package extra\n")
    elif mutation == "delete":
        (built.root / "safe.rego").unlink()
    elif mutation == "symlink":
        (built.root / "extra.rego").symlink_to(built.root / "safe.rego")
    else:
        path = built.root / "bundle-metadata.json"
        metadata = json.loads(path.read_text())
        metadata["version" if mutation == "metadata" else "signature"] = "forged"
        path.write_text(json.dumps(metadata))
    with pytest.raises((ValueError, OSError)):
        pb.verify_bundle(built.root, expected_digest=built.bundle_digest)


@pytest.mark.governance_runtime
@pytest.mark.parametrize("where", ["source", "destination", "file", "parent"])
def test_symlinks_rejected_before_publication(source, tmp_path, where):
    pb = bundle_module()
    dest = tmp_path / "bundle"
    if where == "source":
        link = tmp_path / "source-link"
        link.symlink_to(source, target_is_directory=True)
        source = link
    elif where == "destination":
        dest.symlink_to(tmp_path / "absent", target_is_directory=True)
    elif where == "parent":
        link = tmp_path / "parent-link"
        link.symlink_to(tmp_path, target_is_directory=True)
        dest = link / "bundle"
    else:
        (source / "link.rego").symlink_to(source / "safe.rego")
    with pytest.raises((ValueError, OSError)):
        pb.build_bundle(source=source, destination=dest, policy_id="p", version="1")
    assert not (tmp_path / "bundle/manifest.yaml").exists()


@pytest.mark.governance_runtime
@pytest.mark.parametrize("reference", ["../outside.rego", "/etc/passwd",
                                      "https://example.invalid/policy", r"..\outside.rego"])
def test_ref_escape_rejected(source, tmp_path, reference):
    import yaml
    path = source / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["policies"]["returns-safe"]["data"] = [reference]
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(ValueError):
        bundle_module().build_bundle(source=source, destination=tmp_path / "bundle",
                                     policy_id="p", version="1")
    assert not (tmp_path / "bundle").exists()


@pytest.mark.governance_runtime
def test_invalid_native_manifest_not_published(source, tmp_path):
    (source / "manifest.yaml").write_text("version: '1'\nname: legacy\nrules: []\n")
    with pytest.raises(ValueError):
        bundle_module().build_bundle(source=source, destination=tmp_path / "bundle",
                                     policy_id="p", version="1")
    assert not (tmp_path / "bundle").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source"]


@pytest.mark.governance_runtime
def test_destination_is_never_overwritten(source, tmp_path):
    dest = tmp_path / "bundle"
    dest.mkdir()
    with pytest.raises(FileExistsError):
        bundle_module().build_bundle(source=source, destination=dest,
                                     policy_id="p", version="1")
    assert list(dest.iterdir()) == []


@pytest.mark.governance_runtime
def test_binding_level_reference_escape_rejected(source, tmp_path):
    import yaml
    path = source / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["intervention_points"]["pre_tool_call"]["policy"]["data_paths"] = ["../outside.rego"]
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(ValueError, match="escapes"):
        bundle_module().build_bundle(source=source, destination=tmp_path / "bundle",
                                     policy_id="p", version="1")
    assert not (tmp_path / "bundle").exists()


@pytest.mark.governance_runtime
def test_local_extends_is_hashed_and_escaping_extends_rejected(source, tmp_path):
    import yaml
    pb = bundle_module()
    path = source / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    (source / "base.yaml").write_text(yaml.safe_dump({
        "agent_control_specification_version": "0.3.1-beta", "metadata": {"domain": "refund"},
    }))
    manifest["extends"] = ["base.yaml"]
    path.write_text(yaml.safe_dump(manifest))
    bundle = pb.build_bundle(source=source, destination=tmp_path / "bundle",
                             policy_id="p", version="1")
    assert "base.yaml" in {f["path"] for f in bundle.files}
    manifest["extends"] = ["../outside.yaml"]
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(ValueError, match="escapes"):
        pb.build_bundle(source=source, destination=tmp_path / "bad", policy_id="p", version="1")
    assert not (tmp_path / "bad").exists()


@pytest.mark.governance_runtime
def test_metadata_write_failure_never_publishes(source, tmp_path, monkeypatch):
    pb = bundle_module()
    real_open = Path.open
    def failing_open(path, *args, **kwargs):
        if path.name == "bundle-metadata.json" and args == ("xb",):
            raise OSError("synthetic write failure")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(OSError, match="synthetic"):
        pb.build_bundle(source=source, destination=tmp_path / "bundle", policy_id="p", version="1")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source"]


@pytest.mark.governance_runtime
def test_publication_race_is_no_replace(source, tmp_path, monkeypatch):
    pb = bundle_module()
    assert hasattr(pb, "_publish"), "atomic no-replace publication missing"
    original = pb._publish
    dest = tmp_path / "bundle"
    def racing_publish(staging, destination):
        destination.mkdir()
        (destination / "owner").write_text("other writer")
        original(staging, destination)
    monkeypatch.setattr(pb, "_publish", racing_publish)
    with pytest.raises(FileExistsError):
        pb.build_bundle(source=source, destination=dest, policy_id="p", version="1")
    assert (dest / "owner").read_text() == "other writer"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle", "source"]


@pytest.mark.governance_runtime
def test_exact_pin_runner_exists():
    assert (ROOT / "scripts/ci/run-governance-pin-tests.py").is_file()
