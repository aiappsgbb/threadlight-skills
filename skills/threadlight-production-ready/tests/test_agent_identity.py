"""Unit tests for the agent-identity binding producer (agent_identity.py).

stdlib only; bare ``test_`` functions + ``assert`` (matches sibling tests).
"""
import importlib.util
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "agent_identity", ROOT / "scripts" / "agent_identity.py"
)
m = importlib.util.module_from_spec(_spec)
sys.modules["agent_identity"] = m
_spec.loader.exec_module(m)


def _write_repo(**files: str) -> pathlib.Path:
    """Materialize a tiny repo on disk; keys are repo-relative paths."""
    root = pathlib.Path(tempfile.mkdtemp())
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _arm(*resources) -> str:
    return json.dumps({
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "resources": list(resources),
    })


def _uami(name="id-agent", tags=None):
    r = {"type": "Microsoft.ManagedIdentity/userAssignedIdentities",
         "apiVersion": "2023-01-31", "name": name, "location": "eastus"}
    if tags:
        r["tags"] = tags
    return r


def _role(guid, principal_type="ServicePrincipal", scope=None):
    props = {"roleDefinitionId":
             f"/subscriptions/x/providers/Microsoft.Authorization/roleDefinitions/{guid}",
             "principalId": "[reference('id-agent').principalId]",
             "principalType": principal_type}
    if scope:
        props["scope"] = scope
    return {"type": "Microsoft.Authorization/roleAssignments",
            "apiVersion": "2022-04-01", "name": "ra1", "properties": props}


OWNER_GUID = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
CONTRIBUTOR_GUID = "b24988ac-6180-42a0-ab88-20f7382dd24c"
READER_GUID = "acdd72a7-3385-48ef-bd42-f606fba81ae7"


def _by_id(subjects):
    return {s.id: s for s in subjects}


def _find(findings):
    return {f["id"]: f for f in findings}


# --- discovery ------------------------------------------------------------

def test_uami_in_arm_is_discovered_passwordless():
    root = _write_repo(**{"infra/main.json": _arm(_uami())})
    subs = _by_id(m.discover(root))
    assert "id-agent" in subs
    assert subs["id-agent"].type == "uami"
    assert subs["id-agent"].passwordless is True


def test_federated_credential_upgrades_subject_type():
    fed = {"type": "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials",
           "apiVersion": "2023-01-31", "name": "id-agent/gh-oidc",
           "dependsOn": ["[resourceId('Microsoft.ManagedIdentity/userAssignedIdentities','id-agent')]"]}
    root = _write_repo(**{"infra/main.json": _arm(_uami(), fed)})
    subs = _by_id(m.discover(root))
    assert subs["id-agent"].type == "federated"
    assert subs["id-agent"].passwordless is True


def test_uami_declared_in_bicep_is_discovered():
    bicep = (
        "resource agentId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {\n"
        "  name: 'id-bicep-agent'\n"
        "  location: location\n"
        "  tags: { owner: 'team@contoso.com' }\n"
        "}\n"
    )
    root = _write_repo(**{"infra/identity.bicep": bicep})
    subs = _by_id(m.discover(root))
    assert "id-bicep-agent" in subs
    assert subs["id-bicep-agent"].passwordless is True


# --- IAM-006 passwordless binding -----------------------------------------

def test_uami_only_iam006_pass():
    root = _write_repo(**{"infra/main.json": _arm(_uami())})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-006"]["status"] == "pass"


def test_client_secret_credential_in_src_iam006_must_fix():
    root = _write_repo(**{
        "infra/main.json": _arm(_uami()),
        "src/app.py": "from azure.identity import ClientSecretCredential\ncred = ClientSecretCredential(t, c, s)\n",
    })
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-006"]["status"] == "must-fix"
    subs = _by_id(m.discover(root))
    assert any(s.type == "app-secret" for s in subs.values())


def test_password_credentials_in_bicep_iam006_must_fix():
    bicep = (
        "resource app 'Microsoft.Graph/applications@1.0' = {\n"
        "  displayName: 'agent-app'\n"
        "  passwordCredentials: [ { displayName: 'secret' } ]\n"
        "}\n"
    )
    root = _write_repo(**{"infra/app.bicep": bicep})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-006"]["status"] == "must-fix"


# --- IAM-007 responsible owner --------------------------------------------

def test_uami_with_owner_tag_iam007_pass():
    root = _write_repo(**{"infra/main.json": _arm(
        _uami(tags={"owner": "ai-platform@contoso.com"}))})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-007"]["status"] == "pass"


def test_uami_without_owner_iam007_should_fix():
    root = _write_repo(**{"infra/main.json": _arm(_uami())})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-007"]["status"] == "should-fix"


def test_governance_manifest_supplies_owner_iam007_pass():
    gov = json.dumps({"schema": "threadlight.agent-identity-governance/v1",
                      "subjects": {"id-agent": {"owner": "ops@contoso.com"}}})
    root = _write_repo(**{"infra/main.json": _arm(_uami()),
                          "agent-identity.governance.json": gov})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-007"]["status"] == "pass"


# --- IAM-008 least privilege ----------------------------------------------

def test_owner_role_assignment_iam008_must_fix():
    root = _write_repo(**{"infra/main.json": _arm(_uami(), _role(OWNER_GUID))})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-008"]["status"] == "must-fix"


def test_contributor_role_assignment_iam008_must_fix():
    root = _write_repo(**{"infra/main.json": _arm(_uami(), _role(CONTRIBUTOR_GUID))})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-008"]["status"] == "must-fix"


def test_scoped_builtin_role_iam008_pass():
    root = _write_repo(**{"infra/main.json": _arm(_uami(), _role(READER_GUID))})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-008"]["status"] == "pass"


def test_wildcard_graph_permission_iam008_must_fix():
    bicep = (
        "resource agentId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {\n"
        "  name: 'id-agent'\n}\n"
        "var perms = [ 'Directory.ReadWrite.All' ]\n"
    )
    root = _write_repo(**{"infra/identity.bicep": bicep})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-008"]["status"] == "must-fix"


def test_uami_no_role_assignment_iam008_should_fix():
    root = _write_repo(**{"infra/main.json": _arm(_uami())})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-008"]["status"] == "should-fix"


# --- IAM-009 lifecycle ----------------------------------------------------

def test_federated_lifecycle_iam009_pass():
    fed = {"type": "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials",
           "apiVersion": "2023-01-31", "name": "id-agent/gh-oidc",
           "dependsOn": ["[resourceId('Microsoft.ManagedIdentity/userAssignedIdentities','id-agent')]"]}
    root = _write_repo(**{"infra/main.json": _arm(_uami(), fed)})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-009"]["status"] == "pass"


def test_plain_uami_without_review_iam009_should_fix():
    root = _write_repo(**{"infra/main.json": _arm(_uami())})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-009"]["status"] == "should-fix"


def test_uami_with_review_tag_iam009_pass():
    root = _write_repo(**{"infra/main.json": _arm(
        _uami(tags={"owner": "x@y.com", "reviewBy": "2026-12-31"}))})
    bom, findings = m.assess(root)
    assert _find(findings)["IAM-009"]["status"] == "pass"


# --- edges + contract -----------------------------------------------------

def test_empty_repo_all_not_applicable():
    root = _write_repo(**{"README.md": "# nothing\n"})
    bom, findings = m.assess(root)
    by = _find(findings)
    for fid in ("IAM-006", "IAM-007", "IAM-008", "IAM-009"):
        assert by[fid]["status"] == "not-applicable"
    assert bom["subjects"] == []


def test_malformed_arm_with_identity_marker_does_not_crash():
    bad = '{ "resources": [ { "type": "Microsoft.ManagedIdentity/userAssignedIdentities"'  # truncated
    root = _write_repo(**{"infra/broken.json": bad})
    bom, findings = m.assess(root)  # must not raise
    assert _find(findings)["IAM-006"]["status"] in ("should-fix", "not-applicable")


def test_assess_returns_bom_and_findings_shape():
    root = _write_repo(**{"infra/main.json": _arm(_uami())})
    bom, findings = m.assess(root)
    assert bom["schema"].startswith("threadlight.agent-identity/")
    assert bom["generator_version"] == m.IDENTITY_VERSION
    assert set(bom["summary"]) >= {
        "subject_count", "passwordless", "secret_based",
        "owned", "over_privileged", "must_fix", "should_fix"}
    for f in findings:
        assert set(f) >= {"id", "title", "status", "detail", "offenders"}
    assert {f["id"] for f in findings} == {
        "IAM-006", "IAM-007", "IAM-008", "IAM-009"}


def test_check_reports_must_fix_in_summary():
    root = _write_repo(**{"infra/main.json": _arm(_uami(), _role(OWNER_GUID))})
    bom, findings = m.assess(root)
    assert bom["summary"]["must_fix"] >= 1
    assert bom["summary"]["over_privileged"] >= 1
