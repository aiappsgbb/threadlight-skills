"""Post-deploy channel matching for Foundry hosted agents and --out semantics.

Regression for the live2 E2E: a Foundry playground channel served by an
``azure.ai.agent`` service was reported as ``no matching ACA`` although the
hosted agent was deployed and answering, and ``--out x.json`` created a
directory named ``x.json``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import safe_check as sc  # noqa: E402

AZURE_YAML = """name: pilot
services:
  mcp:
    host: containerapp
    project: ./src/mcp
  te-policy-agent:
    host: azure.ai.agent
    project: ./src/agent
"""


def _run_postdeploy(resource_types: list[str], *, dm: dict | None = None,
                    deferred: str | None = None) -> dict:
    root = Path(os.path.realpath(tempfile.mkdtemp()))
    (root / "specs").mkdir()
    (root / "azure.yaml").write_text(AZURE_YAML, encoding="utf-8")
    if deferred is not None:
        (root / "specs" / "deferred-steps.md").write_text(deferred, encoding="utf-8")
    manifest = root / "specs" / "manifest.json"
    manifest.write_text(json.dumps({"deployment_manifest": dm or {
        "channels": [{"name": "Foundry playground", "type": "web", "service": "te-policy-agent"}],
    }}), encoding="utf-8")
    out = root / "tests" / "postdeploy-manifest.json"
    out.parent.mkdir()
    resources = json.dumps([{"type": t, "name": t.rsplit("/", 1)[-1]} for t in resource_types])

    def fake_az(*args, **kwargs):
        if args[:2] == ("account", "show"):
            return '{"id":"11111111-1111-1111-1111-111111111111","tenantId":"11111111-1111-1111-1111-111111111111"}'
        if args[:2] == ("resource", "list") and "--resource-type" not in args:
            return resources
        return "[]"

    saved = sc._az, sc._repo_root_for_manifest, sc._load_effective_mcp_config
    try:
        sc._az = fake_az
        sc._repo_root_for_manifest = lambda manifest, explicit_root=None: root
        sc._load_effective_mcp_config = lambda r: {}
        sc.phase_postdeploy(manifest, out, "rg-1", repo_root=root)
    finally:
        sc._az, sc._repo_root_for_manifest, sc._load_effective_mcp_config = saved
    return json.loads(out.read_text(encoding="utf-8"))


def test_foundry_hosted_agent_channel_is_recognised() -> None:
    payload = _run_postdeploy(["Microsoft.CognitiveServices/accounts",
                               "Microsoft.CognitiveServices/accounts/projects"])
    assert not [g for g in payload["gaps"] if "Foundry playground" in g]
    (channel,) = payload["channels"]
    assert channel["status"] == "foundry_hosted_agent"
    assert channel["host"] == "azure.ai.agent"


def test_foundry_hosted_agent_channel_without_project_is_a_gap() -> None:
    payload = _run_postdeploy([])
    assert any("Foundry playground" in g and "Foundry project" in g for g in payload["gaps"])


def test_out_with_json_suffix_is_written_as_file() -> None:
    root = Path(os.path.realpath(tempfile.mkdtemp()))
    (root / "specs").mkdir()
    (root / "specs" / "manifest.json").write_text(json.dumps({"deployment_manifest": {}}), encoding="utf-8")
    subprocess.run([sys.executable, str(SCRIPTS / "safe_check.py"), "--phase", "design",
                    "--out", "reports/design.json", "--quiet"], cwd=root, capture_output=True, text=True)
    assert (root / "reports" / "design.json").is_file()


PROJECT = ["Microsoft.CognitiveServices/accounts", "Microsoft.CognitiveServices/accounts/projects"]


def test_design_agent_service_alias_resolves_to_the_hosted_agent() -> None:
    # Design manifests name the hosted agent service "agent"; deploy names the azd
    # service after the process. The manifest's own host must still be honoured.
    dm = {"services": [{"name": "agent", "host": "azure.ai.agent", "src": "src/agent"},
                       {"name": "mcp", "host": "containerapp", "src": "src/mcp"}],
          "channels": [{"name": "Foundry playground", "type": "web", "service": "agent"}]}
    payload = _run_postdeploy(PROJECT, dm=dm)
    assert not [g for g in payload["gaps"] if "Foundry playground" in g]
    assert payload["channels"][0]["status"] == "foundry_hosted_agent"


DEFERRED = (
    "| Step | Status | Note |\n|---|---|---|\n"
    "| SPEC § 11c modules `ai-search`, `storage-blob`, `foundry-iq-index` | not provisioned | "
    "Provision them with `threadlight-ground` after `azd up`. |\n"
)


def test_recorded_deferral_is_reported_as_deferred_not_missing() -> None:
    dm = {"expected_resource_types": PROJECT + ["Microsoft.Search/searchServices",
                                                "Microsoft.Storage/storageAccounts"]}
    payload = _run_postdeploy(PROJECT, dm=dm, deferred=DEFERRED)
    assert not [g for g in payload["gaps"] if g.startswith("missing resource type")]
    assert sorted(payload["deferred_resource_types"]) == [
        "Microsoft.Search/searchServices", "Microsoft.Storage/storageAccounts"]


def test_unrecorded_missing_type_is_still_a_gap() -> None:
    dm = {"expected_resource_types": PROJECT + ["Microsoft.Search/searchServices",
                                                "Microsoft.DocumentDB/databaseAccounts"]}
    payload = _run_postdeploy(PROJECT, dm=dm, deferred=DEFERRED)
    assert "missing resource type: Microsoft.DocumentDB/databaseAccounts" in payload["gaps"]
    assert "missing resource type: Microsoft.Search/searchServices" not in payload["gaps"]


def test_deferral_without_not_provisioned_status_does_not_excuse() -> None:
    dm = {"expected_resource_types": PROJECT + ["Microsoft.Search/searchServices"]}
    payload = _run_postdeploy(PROJECT, dm=dm, deferred=DEFERRED.replace("not provisioned", "done"))
    assert "missing resource type: Microsoft.Search/searchServices" in payload["gaps"]
