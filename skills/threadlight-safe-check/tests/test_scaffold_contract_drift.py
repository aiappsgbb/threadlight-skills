"""Safe-check must accept the canonical threadlight-deploy scaffold.

Regression for the Phase 3 live runs: pre-deploy failed on a literal
``project: ./src/mcp`` substring although the GHCP pilot scaffold declares
the MCP service as ``project: .`` + ``docker.path: src/mcp/Dockerfile`` (the
shape that deployed); post-deploy silently checked zero selectors when they
were booleans, and ``accounts/deployments`` (never returned by
``az resource list``) produced a false missing-type gap.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import safe_check as sc  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
SCAFFOLD_AZURE_YAML = (REPO / "skills/threadlight-deploy/references/hosted-agent/ghcp/references/pilot/azure.yaml")


def _predeploy_gaps(azure_text: str) -> list[str]:
    root = Path(os.path.realpath(tempfile.mkdtemp()))
    (root / "specs").mkdir()
    (root / "infra").mkdir()
    (root / "src" / "mcp").mkdir(parents=True)
    (root / "src" / "mcp" / "Dockerfile").write_text("FROM x\n", encoding="utf-8")
    (root / "infra" / "main.bicep").write_text("// main\n", encoding="utf-8")
    (root / "azure.yaml").write_text(azure_text, encoding="utf-8")
    manifest = root / "specs" / "manifest.json"
    manifest.write_text(json.dumps({"deployment_manifest": {
        "module_selectors": {"aca-mcp": "yes"},
        "services": [{"name": "mcp", "host": "containerapp", "src": "src/mcp"}],
    }}), encoding="utf-8")
    out = root / "tests" / "pre.json"
    out.parent.mkdir()
    saved = sc._governance_static
    try:
        sc._governance_static = lambda *a, **k: {}
        sc.phase_predeploy(root, manifest, out)
    finally:
        sc._governance_static = saved
    return [g for g in json.loads(out.read_text(encoding="utf-8"))["gaps"] if "aca-mcp" in g]


def test_predeploy_accepts_canonical_scaffold_docker_path() -> None:
    assert _predeploy_gaps(SCAFFOLD_AZURE_YAML.read_text(encoding="utf-8")) == []


def test_predeploy_accepts_project_path_layout() -> None:
    assert _predeploy_gaps("services:\n  mcp:\n    host: containerapp\n    project: ./src/mcp\n") == []


def test_predeploy_still_flags_missing_mcp_service() -> None:
    assert _predeploy_gaps("services:\n  agent:\n    host: azure.ai.agent\n    project: ./src/agent\n")


def _postdeploy(selectors: dict, expected: list[str]) -> dict:
    root = Path(os.path.realpath(tempfile.mkdtemp()))
    (root / "specs").mkdir()
    manifest = root / "specs" / "manifest.json"
    manifest.write_text(json.dumps({"deployment_manifest": {
        "module_selectors": selectors, "expected_resource_types": expected}}), encoding="utf-8")
    out = root / "post.json"
    resources = json.dumps([{"type": "Microsoft.CognitiveServices/accounts", "name": "a"}])

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


def test_postdeploy_rejects_non_yes_no_selectors() -> None:
    payload = _postdeploy({"foundry-account": True}, ["Microsoft.CognitiveServices/accounts"])
    assert any("foundry-account" in g and "'yes' or 'no'" in g for g in payload["gaps"])


def test_design_flags_child_types_az_resource_list_cannot_see() -> None:
    root = Path(os.path.realpath(tempfile.mkdtemp()))
    (root / "specs").mkdir()
    manifest = root / "specs" / "manifest.json"
    manifest.write_text(json.dumps({"deployment_manifest": {
        "module_selectors": {"foundry-account": "yes"},
        "expected_resource_types": ["Microsoft.CognitiveServices/accounts",
                                    "Microsoft.CognitiveServices/accounts/deployments"]}}), encoding="utf-8")
    out = root / "design.json"
    saved = sc._repo_root_for_manifest
    try:
        sc._repo_root_for_manifest = lambda manifest, explicit_root=None: root
        sc.phase_design(manifest, out)
    finally:
        sc._repo_root_for_manifest = saved
    gaps = json.loads(out.read_text(encoding="utf-8"))["gaps"]
    assert any("accounts/deployments" in g and "az resource list" in g for g in gaps)
