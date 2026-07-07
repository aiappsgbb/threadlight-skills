"""Integration: agent-identity findings (IAM-006..009) surface through Pillar 03."""
import importlib.util
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py")
pr = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = pr
_spec.loader.exec_module(pr)


def _repo(**files: str) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp())
    (root / "specs").mkdir(parents=True, exist_ok=True)
    (root / "specs" / "SPEC.md").write_text("# spec\n", encoding="utf-8")
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _arm(*resources) -> str:
    return json.dumps({"$schema": "x", "contentVersion": "1.0.0.0",
                       "resources": list(resources)})


def _uami(name="id-agent", tags=None):
    r = {"type": "Microsoft.ManagedIdentity/userAssignedIdentities",
         "apiVersion": "2023-01-31", "name": name, "location": "eastus"}
    if tags:
        r["tags"] = tags
    return r


def _by_id(findings):
    return {f.id: f for f in findings}


OWNER_GUID = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"


def test_uami_surfaces_iam006_pass_through_pillar():
    root = _repo(**{"infra/main.json": _arm(
        _uami(tags={"owner": "ai@contoso.com", "reviewBy": "2026-12-31"}))})
    ctx = pr.RepoContext.from_repo(root, {})
    by = _by_id(pr._check_identity_static(ctx))
    assert by["IAM-006"].status == "pass"
    assert by["IAM-007"].status == "pass"
    assert ctx.agent_identity is not None
    assert ctx.agent_identity["summary"]["subject_count"] == 1


def test_owner_role_surfaces_iam008_must_fix_through_pillar():
    role = {"type": "Microsoft.Authorization/roleAssignments", "apiVersion": "2022-04-01",
            "name": "ra", "properties": {
                "roleDefinitionId": f"/x/providers/Microsoft.Authorization/roleDefinitions/{OWNER_GUID}",
                "principalType": "ServicePrincipal", "principalId": "p"}}
    root = _repo(**{"infra/main.json": _arm(_uami(), role)})
    ctx = pr.RepoContext.from_repo(root, {})
    by = _by_id(pr._check_identity_static(ctx))
    assert by["IAM-008"].status == "must-fix"


def test_no_identity_all_not_applicable_through_pillar():
    root = _repo(**{"README.md": "# nothing\n"})
    ctx = pr.RepoContext.from_repo(root, {})
    by = _by_id(pr._check_identity_static(ctx))
    for fid in ("IAM-006", "IAM-007", "IAM-008", "IAM-009"):
        assert by[fid].status == "not-applicable"


def test_producer_unavailable_yields_not_verified(monkeypatch):
    monkeypatch.setattr(pr, "_load_agent_identity", lambda: None)
    root = _repo(**{"infra/main.json": _arm(_uami())})
    ctx = pr.RepoContext.from_repo(root, {})
    by = _by_id(pr._check_identity_static(ctx))
    for fid in ("IAM-006", "IAM-007", "IAM-008", "IAM-009"):
        assert by[fid].status == "not-verified"
