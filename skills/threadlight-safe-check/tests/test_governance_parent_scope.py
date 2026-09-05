"""Parent CLI context is independent of the signed collector's selected target."""
import asyncio
from copy import deepcopy
import importlib.util
import json
import subprocess
import sys

import pytest

from test_governance_gates import ROOT, safe_check
from test_governance_probe import TENANT, native_collector_harness, packaged_collector_project


OTHER_SUB = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_TENANT = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
RG = "rg-staging"


@pytest.fixture(params=["canonical", "example"])
def parent(request):
    if request.param == "canonical":
        return safe_check
    spec = importlib.util.spec_from_file_location(
        "example_safe_check", ROOT / "examples/returns-triage-governed/tests/safe_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParentAzure:
    """Only the Azure process boundary is replaced; default can change mid-gate."""
    def __init__(self, account, *, available=True):
        self.account = account
        self.available = available
        self.calls = []
        self.default_subscription = account.get("id") if isinstance(account, dict) else None
        self.real_run = subprocess.run

    def __call__(self, command, **kwargs):
        if not isinstance(command, list) or command[0] != "az":
            return self.real_run(command, **kwargs)
        self.calls.append(command)
        assert command[1:3] not in (["account", "set"], ["account", "clear"])
        assert command[1] != "login"
        if command[1:3] == ["account", "show"]:
            if not self.available:
                raise subprocess.CalledProcessError(1, command, stderr="PRIVATE CLI failure")
            value = deepcopy(self.account)
            if "--query" in command and command[command.index("--query") + 1] == "{t:tenantId,s:name,sid:id}":
                value = ({"t": value.get("tenantId"), "s": value.get("name"), "sid": value.get("id")}
                         if isinstance(value, dict) else value)
        else:
            # Identical RG/resources in both subscriptions must not hide a mismatch.
            value = []
            self.default_subscription = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")


async def parent_gate(parent, monkeypatch, project, h, *, entry="phase", subscription=None):
    dependencies = dict(credential=h.credential, signer=h.signer, run=h.run, http=h.http, timeout=8)
    if entry == "cli":
        import governance_references.governance_probe as installed
        collect = installed.collect_project
        async def local_dependencies(*args, **options):
            return await collect(*args, **{**options, **dependencies})
        monkeypatch.setattr(installed, "collect_project", local_dependencies)
        monkeypatch.chdir(project)
        argv = ["safe-check", "--phase", "post-deploy", "--rg", RG]
        if subscription is not None:
            argv.extend(["--subscription", subscription])
        monkeypatch.setattr(sys, "argv", argv)
        try:
            result = await asyncio.to_thread(parent.main)
        except SystemExit as exc:
            pytest.fail(f"CLI must produce a gate result, not exit {exc.code}")
        output = project / "tests/postdeploy-manifest.json"
    else:
        output = project / "post.json"
        options = {} if subscription is None else {"subscription": subscription}
        result = await asyncio.to_thread(parent.phase_postdeploy,
            project / "specs/manifest.json", output, RG, project, dependencies, **options)
    return result, json.loads(output.read_text())


@pytest.mark.parametrize("entry", ["cli", "phase"])
@pytest.mark.parametrize("account", [
    {"id": OTHER_SUB, "tenantId": TENANT},
    {"id": OTHER_SUB, "tenantId": OTHER_TENANT},
    {"id": TENANT, "tenantId": OTHER_TENANT},
], ids=["same-tenant-other-sub", "other-tenant-other-sub", "other-tenant"])
def test_parent_account_mismatch_blocks_real_collector_before_registration(tmp_path, monkeypatch, parent, entry, account):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, config = packaged_collector_project(tmp_path, h)
            original = config.read_bytes()
            azure = ParentAzure({**account, "name": "parent", "privateField": "PRIVATE"})
            monkeypatch.setattr(parent.subprocess, "run", azure)
            result, report = await parent_gate(parent, monkeypatch, project, h, entry=entry)
            assert result == 1
            assert report["governance_gaps"], report
            assert report["governance_probes"] == []
            assert not any(method == "POST" for method, _, _ in h.calls), h.calls
            assert not any(".services.ai.azure.com" in host for _, host, _ in h.calls)
            assert "missing resource type: Microsoft.Storage/storageAccounts" in report["gaps"]
            assert not any(b["status"] == "enforced" for b in report["governance_health"]["bindings"])
            assert report["observed_target"]["subscription"] == TENANT
            assert report["observed_target"]["tenant"] == TENANT
            assert config.read_bytes() == original
            assert "PRIVATE" not in json.dumps(report)
    asyncio.run(case())


@pytest.mark.parametrize("account,available", [
    ({}, True), ([], True), (None, True),
    ({"id": TENANT}, True), ({"tenantId": TENANT}, True),
    ({"id": "not-a-uuid", "tenantId": TENANT}, True),
    ({"id": TENANT, "tenantId": "not-a-uuid"}, True),
    ({"id": False, "tenantId": TENANT}, True),
    ({"id": TENANT, "tenantId": TENANT}, False),
], ids=["empty", "array", "null", "missing-tenant", "missing-sub", "bad-sub", "bad-tenant", "boolean", "unavailable"])
def test_parent_missing_context_is_gap_without_registration(tmp_path, monkeypatch, parent, account, available):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, _ = packaged_collector_project(tmp_path, h)
            azure = ParentAzure(account, available=available)
            monkeypatch.setattr(parent.subprocess, "run", azure)
            result, report = await parent_gate(parent, monkeypatch, project, h, entry="cli")
            assert result == 1
            assert report["governance_gaps"], report
            assert report["governance_probes"] == []
            assert not h.calls, h.calls
            assert not h.run.calls
            assert all(command[1:3] == ["account", "show"] for command in azure.calls)
            assert not any(b["status"] == "enforced" for b in report["governance_health"]["bindings"])
            assert "PRIVATE" not in json.dumps(report)
    asyncio.run(case())


@pytest.mark.parametrize("entry", ["cli", "phase"])
@pytest.mark.parametrize("selector", [None, TENANT, "Staging subscription"])
def test_parent_matching_account_passes_and_pins_all_reads(tmp_path, monkeypatch, parent, entry, selector):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, _ = packaged_collector_project(tmp_path, h)
            manifest = project / "specs/manifest.json"
            document = json.loads(manifest.read_text())
            document["deployment_manifest"]["expected_resource_types"] = []
            manifest.write_text(json.dumps(document))
            azure = ParentAzure({"id": TENANT, "tenantId": TENANT, "name": "Staging subscription", "privateField": "PRIVATE"})
            monkeypatch.setattr(parent.subprocess, "run", azure)
            result, report = await parent_gate(parent, monkeypatch, project, h, entry=entry, subscription=selector)
            assert result == 0, report
            assert report["gaps"] == []
            assert len(report["governance_probes"]) == 2
            assert report["governance_health"]["bindings"][0]["status"] == "enforced"
            assert report["parent_target"] == {"tenant": TENANT, "subscription": TENANT, "resource_group": RG}
            account_calls = [command for command in azure.calls if command[1:3] == ["account", "show"]]
            assert len(account_calls) == 1
            if selector is None:
                assert "--subscription" not in account_calls[0]
            else:
                assert account_calls[0][account_calls[0].index("--subscription") + 1] == selector
            reads = [command for command in azure.calls if command[1:3] != ["account", "show"]]
            assert len(reads) == 4
            assert all("--subscription" in command and command[command.index("--subscription") + 1] == TENANT for command in reads)
            assert "PRIVATE" not in json.dumps(report)
    asyncio.run(case())


@pytest.mark.parametrize("field,value", [
    ("subscription_id", OTHER_SUB), ("subscription", OTHER_SUB),
    ("tenant_id", OTHER_TENANT), ("tenant", OTHER_TENANT),
    ("subscription_id", None), ("tenant_id", []),
])
def test_parent_manifest_selectors_must_match_observed_account(tmp_path, monkeypatch, parent, field, value):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, _ = packaged_collector_project(tmp_path, h)
            manifest = project / "specs/manifest.json"
            document = json.loads(manifest.read_text())
            document["deployment_manifest"][field] = value
            manifest.write_text(json.dumps(document))
            before = manifest.read_bytes()
            azure = ParentAzure({"id": TENANT, "tenantId": TENANT, "name": "Staging subscription"})
            monkeypatch.setattr(parent.subprocess, "run", azure)
            result, report = await parent_gate(parent, monkeypatch, project, h)
            assert result == 1
            assert report["governance_gaps"]
            assert not h.calls
            assert report["governance_probes"] == []
            assert manifest.read_bytes() == before
    asyncio.run(case())


def test_parent_manifest_display_name_canonicalizes_without_selecting_global_account(tmp_path, monkeypatch, parent):
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, _ = packaged_collector_project(tmp_path, h)
            manifest = project / "specs/manifest.json"
            document = json.loads(manifest.read_text())
            document["deployment_manifest"].update(
                subscription_id="Staging subscription", tenant_id=TENANT)
            manifest.write_text(json.dumps(document))
            azure = ParentAzure({"id": TENANT, "tenantId": TENANT, "name": "Staging subscription"})
            monkeypatch.setattr(parent.subprocess, "run", azure)
            result, report = await parent_gate(parent, monkeypatch, project, h)
            assert result == 1
            assert report["governance_gaps"] == []
            assert len(report["governance_probes"]) == 2
            assert report["parent_target"]["subscription"] == TENANT
            assert any("--subscription" in command and
                       command[command.index("--subscription") + 1] == "Staging subscription"
                       for command in azure.calls if command[1:3] == ["account", "show"])
    asyncio.run(case())


@pytest.mark.parametrize("governance", [None, {"mode": "off"}])
def test_parent_freezes_followup_reads_without_changing_legacy_health_gaps(tmp_path, monkeypatch, parent, governance):
    document = {"deployment_manifest": {"module_selectors": {
        "aca-bot": "yes", "app-insights": "yes", "cosmos-db": "yes"},
        "integrations": [{"id": "erp", "availability": "real"}]}}
    if governance is not None:
        document["governance"] = governance
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(document))
    (tmp_path / "mcp-config.json").write_text('{"servers":{"erp":{"url":"https://mock.example/mcp"}}}')
    azure = ParentAzure({"id": TENANT, "tenantId": TENANT})
    def run(command, **kwargs):
        result = azure(command, **kwargs)
        if command[:3] == ["az", "account", "show"]:
            return result
        if command[1:3] == ["resource", "list"]:
            kind = command[command.index("--resource-type") + 1] if "--resource-type" in command else None
            value = [{"name": "bot"}] if kind == "Microsoft.BotService/botServices" else []
        elif command[1:3] == ["containerapp", "list"]:
            value = [{"name": "bot", "image": "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"}]
        elif command[1:4] == ["containerapp", "job", "list"]:
            value = [{"name": "job", "image": "example/image@sha256:" + "a" * 64}]
        elif command[1:5] == ["containerapp", "job", "execution", "list"]:
            value = [{"name": "execution", "status": "Failed"}]
        elif command[1:3] == ["bot", "show"]:
            value = {"appType": "UserAssignedMSI"}
        elif command[1:3] == ["containerapp", "show"]:
            value = []
        elif command[1:3] == ["cosmosdb", "list"]:
            value = [{"name": "db", "pna": "Disabled"}]
        else:
            pytest.fail(f"unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
    monkeypatch.setattr(parent.subprocess, "run", run)
    output = tmp_path / "post.json"
    assert parent.phase_postdeploy(manifest, output, RG, tmp_path) == 1
    report = json.loads(output.read_text())
    assert "governance_gaps" not in report
    assert len(report["gaps"]) == 6, report["gaps"]
    for part in ("placeholder", "job-success", "appin-existence", "bot-authtype",
                 "cosmos-firewall", "integration erp"):
        assert any(part in gap for gap in report["gaps"])
    reads = [command for command in azure.calls if command[1:3] != ["account", "show"]]
    assert len(reads) == 9
    assert all(command[command.index("--subscription") + 1] == TENANT for command in reads)
