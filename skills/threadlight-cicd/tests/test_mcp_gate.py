"""The generated production pipeline carries an MCP supply-chain gate after
deploy that enforces the mcp-sbom summary (soft = warn-only, hard = block on a
must-fix MCP finding). OIDC/WIF only; no secret; no unrendered tokens."""
import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "generate_pipeline", ROOT / "scripts" / "generate_pipeline.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["generate_pipeline"] = mod
_spec.loader.exec_module(mod)


def _gh_framing(**over):
    f = {
        "platform": "github-actions",
        "target_subscription_id": "11111111-1111-1111-1111-111111111111",
        "target_resource_group": "rg-pilot-prod",
        "target_location": "eastus2",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "repo_full_name": "aiappsgbb/contoso-pilot",
        "env_name": "prod",
    }
    f.update(over)
    return f


def _ado_framing(**over):
    f = {
        "platform": "azure-devops",
        "target_subscription_id": "11111111-1111-1111-1111-111111111111",
        "target_resource_group": "rg-pilot-prod",
        "target_location": "eastus2",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "ado_org": "contoso",
        "ado_project": "AI-Pilots",
        "ado_service_connection": "sc-contoso-pilot-prod",
        "env_name": "prod",
    }
    f.update(over)
    return f


def _gh_workflow(framing):
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(framing, out_root=tmp)
    return (tmp / ".github/workflows/azd-deploy-prod.yml").read_text()


def _ado_pipeline(framing):
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(framing, out_root=tmp)
    return (tmp / "azure-pipelines.yml").read_text()


# --- GitHub Actions -------------------------------------------------------

def test_github_has_mcp_gate_after_deploy():
    wf = _gh_workflow(_gh_framing())
    assert "mcp-supply-chain-gate:" in wf
    assert "needs: deploy" in wf
    assert wf.index("deploy:") < wf.index("mcp-supply-chain-gate:")


def test_github_mcp_gate_reads_the_sbom_sidecar():
    wf = _gh_workflow(_gh_framing())
    assert "tests/mcp-sbom.json" in wf
    assert "must_fix" in wf


def test_github_mcp_gate_oidc_and_no_tokens():
    wf = _gh_workflow(_gh_framing())
    assert "azure/login@" in wf
    assert "{{" not in wf


def test_github_mcp_soft_default_and_hard_block():
    soft = _gh_workflow(_gh_framing())
    assert "mode=soft" in soft
    hard = _gh_workflow(_gh_framing(mcp_gate="hard"))
    assert "mode=hard" in hard
    assert "continue-on-error: false" in hard


# --- Azure DevOps ---------------------------------------------------------

def test_ado_has_mcp_gate_stage_after_deploy():
    pipe = _ado_pipeline(_ado_framing())
    assert "mcp_supply_chain_gate" in pipe
    assert "dependsOn: deploy" in pipe
    assert pipe.index("stage: deploy") < pipe.index("mcp_supply_chain_gate")


def test_ado_mcp_gate_reads_the_sbom_sidecar():
    pipe = _ado_pipeline(_ado_framing())
    assert "tests/mcp-sbom.json" in pipe
    assert "must_fix" in pipe


def test_ado_mcp_soft_default_and_hard_block():
    soft = _ado_pipeline(_ado_framing())
    assert "mode=soft" in soft
    assert "{{" not in soft
    hard = _ado_pipeline(_ado_framing(mcp_gate="hard"))
    assert "mode=hard" in hard
    assert "continueOnError: false" in hard


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"OK   {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
