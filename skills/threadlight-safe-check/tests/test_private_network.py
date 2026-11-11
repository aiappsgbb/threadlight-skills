"""Post-deploy network verification for the private profile.

When ``specs/deployment-posture.md`` records ``networking: private`` (written by
``generate_only.py --profile private``), the post-deploy gate checks the actual
Azure resources: public network access off, an approved private endpoint with a
DNS zone group whose zones are linked to the VNet, App Insights ingestion only
through a private link scope, an internal Container Apps environment, and
hosted-agent VNet injection on the Foundry account. The pure function is tested
here with ARM-shaped dictionaries; the post-deploy phase feeds it ``az`` reads.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT))
import safe_check as sc  # noqa: E402

RG = "/subscriptions/s/resourceGroups/rg"
VNET = f"{RG}/providers/Microsoft.Network/virtualNetworks/vnet"
ACCOUNT = f"{RG}/providers/Microsoft.CognitiveServices/accounts/ais"
ACR = f"{RG}/providers/Microsoft.ContainerRegistry/registries/cr"
APPI = f"{RG}/providers/Microsoft.Insights/components/appi"
AMPLS = f"{RG}/providers/microsoft.insights/privateLinkScopes/ampls"
CAE = f"{RG}/providers/Microsoft.App/managedEnvironments/cae"
KV = f"{RG}/providers/Microsoft.KeyVault/vaults/kv"
ZONE = f"{RG}/providers/Microsoft.Network/privateDnsZones/privatelink.services.ai.azure.com"


def _resources(**overrides):
    base = {
        ACCOUNT: {"type": "Microsoft.CognitiveServices/accounts", "properties": {
            "publicNetworkAccess": "Disabled",
            "networkInjections": [{"scenario": "agent", "subnetArmId": f"{VNET}/subnets/snet-agent"}]}},
        ACR: {"type": "Microsoft.ContainerRegistry/registries", "properties": {
            "publicNetworkAccess": "Enabled",
            "networkRuleSet": {"defaultAction": "Deny", "ipRules": [{"value": "20.44.19.64/26"}]}}},
        APPI: {"type": "Microsoft.Insights/components", "properties": {
            "publicNetworkAccessForIngestion": "Disabled"}},
        AMPLS: {"type": "microsoft.insights/privateLinkScopes", "properties": {
            "accessModeSettings": {"ingestionAccessMode": "PrivateOnly"}}},
        CAE: {"type": "Microsoft.App/managedEnvironments", "properties": {
            "vnetConfiguration": {"internal": True}}},
    }
    base.update(overrides)
    return [{"id": rid, "name": rid.rsplit("/", 1)[-1], **body} for rid, body in base.items() if body]


def _endpoints(*targets, status="Approved", zones=(ZONE,)):
    return [{"name": f"pe-{t.rsplit('/', 1)[-1]}", "targets": [{"id": t, "status": status}], "zones": list(zones)}
            for t in targets]


GOOD_PES = (ACCOUNT, ACR, AMPLS)
LINKED = {ZONE: [VNET]}


def test_private_deployment_has_no_gap():
    gaps, results = sc.private_network_gaps(_resources(), _endpoints(*GOOD_PES), LINKED)
    assert gaps == []
    acr = next(r for r in results if r["name"] == "cr")
    assert acr["status"] == "OK" and "ACR Tasks" in acr["note"]


def test_public_foundry_account_is_a_gap():
    resources = _resources(**{ACCOUNT: {"type": "Microsoft.CognitiveServices/accounts", "properties": {
        "publicNetworkAccess": "Enabled",
        "networkInjections": [{"scenario": "agent", "subnetArmId": f"{VNET}/subnets/a"}]}}})
    gaps, _ = sc.private_network_gaps(resources, _endpoints(*GOOD_PES), LINKED)
    assert any("ais" in g and "publicNetworkAccess" in g for g in gaps)


def test_foundry_without_agent_injection_is_a_gap():
    resources = _resources(**{ACCOUNT: {"type": "Microsoft.CognitiveServices/accounts",
                                        "properties": {"publicNetworkAccess": "Disabled"}}})
    gaps, _ = sc.private_network_gaps(resources, _endpoints(*GOOD_PES), LINKED)
    assert any("network injection" in g for g in gaps)


def test_missing_or_pending_private_endpoint_is_a_gap():
    gaps, _ = sc.private_network_gaps(_resources(), _endpoints(ACCOUNT, AMPLS), LINKED)
    assert any("cr" in g and "private endpoint" in g for g in gaps)
    gaps, _ = sc.private_network_gaps(_resources(), _endpoints(*GOOD_PES, status="Pending"), LINKED)
    assert any("Approved" in g for g in gaps)


def test_unlinked_dns_zone_is_a_gap():
    gaps, _ = sc.private_network_gaps(_resources(), _endpoints(*GOOD_PES), {ZONE: []})
    assert any("DNS" in g and "privatelink.services.ai.azure.com" in g for g in gaps)
    gaps, _ = sc.private_network_gaps(_resources(), _endpoints(*GOOD_PES, zones=()), LINKED)
    assert any("DNS zone group" in g for g in gaps)


def test_acr_open_to_everyone_is_a_gap():
    resources = _resources(**{ACR: {"type": "Microsoft.ContainerRegistry/registries", "properties": {
        "publicNetworkAccess": "Enabled", "networkRuleSet": {"defaultAction": "Allow", "ipRules": []}}}})
    gaps, _ = sc.private_network_gaps(resources, _endpoints(*GOOD_PES), LINKED)
    assert any("cr" in g for g in gaps)


def test_public_app_insights_ingestion_and_external_environment_are_gaps():
    resources = _resources(**{
        APPI: {"type": "Microsoft.Insights/components", "properties": {"publicNetworkAccessForIngestion": "Enabled"}},
        CAE: {"type": "Microsoft.App/managedEnvironments", "properties": {"vnetConfiguration": None}},
    })
    gaps, _ = sc.private_network_gaps(resources, _endpoints(*GOOD_PES), LINKED)
    assert any("appi" in g and "ingestion" in g for g in gaps)
    assert any("cae" in g and "internal" in g for g in gaps)


def test_selected_key_vault_must_be_private_too():
    resources = _resources(**{KV: {"type": "Microsoft.KeyVault/vaults", "properties": {"publicNetworkAccess": "Enabled"}}})
    gaps, _ = sc.private_network_gaps(resources, _endpoints(*GOOD_PES), LINKED)
    assert any("kv" in g and "publicNetworkAccess" in g for g in gaps)
    assert any("kv" in g and "private endpoint" in g for g in gaps)


def test_posture_reader(tmp_path):
    (tmp_path / "specs").mkdir()
    posture = tmp_path / "specs" / "deployment-posture.md"
    assert sc.network_posture(tmp_path) == "public"
    posture.write_text("```yaml\nprofile: private\nposture:\n  networking: private (Foundry, ACR)\n```\n")
    assert sc.network_posture(tmp_path) == "private"
    posture.write_text("```yaml\nposture:\n  networking: public\n```\n")
    assert sc.network_posture(tmp_path) == "public"
    # T-0 posture value for production-bound pilots whose private networking is still deferred.
    posture.write_text("```yaml\noverrides:\n  networking: private-required   # status: deferred\n```\n")
    assert sc.network_posture(tmp_path) == "public"
