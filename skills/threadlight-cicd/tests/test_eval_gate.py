"""CAF requirement: standardized evaluation + AI red teaming integrated into CI/CD.

The generated production pipeline must carry an **eval gate** and a
**red-team gate** after deploy that run the threadlight Discover legs against
the freshly deployed agent and enforce their verdicts. Both gates are
OIDC / WIF only (no secret), and honour a soft|hard mode:

  * soft  -> warn-only (continue-on-error true); the pipeline stays green.
  * hard  -> block the pipeline on a non-pass eval / red-team verdict.
"""
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

def test_github_has_eval_and_redteam_gates_after_deploy():
    wf = _gh_workflow(_gh_framing())
    assert "eval-gate:" in wf
    assert "red-team-gate:" in wf
    # both gates run after deploy, never before
    assert "needs: deploy" in wf
    assert wf.index("jobs:") < wf.index("eval-gate:")
    assert wf.index("deploy:") < wf.index("eval-gate:")


def test_github_gates_invoke_the_discover_legs_and_manifests():
    wf = _gh_workflow(_gh_framing())
    assert "threadlight-evals" in wf
    assert "threadlight-redteam" in wf
    assert "specs/evals-manifest.json" in wf
    assert "specs/redteam-manifest.json" in wf


def test_github_gates_are_oidc_and_secret_free():
    wf = _gh_workflow(_gh_framing())
    assert "azure/login@" in wf
    assert "AZURE_CREDENTIALS" not in wf
    assert "client-secret" not in wf
    assert "{{" not in wf


def test_github_soft_mode_is_warn_only_by_default():
    wf = _gh_workflow(_gh_framing())
    # default mode is soft -> the verdict-enforcement step does not fail the run
    assert "continue-on-error: true" in wf


def test_github_hard_mode_blocks_the_pipeline():
    wf = _gh_workflow(_gh_framing(eval_gate="hard"))
    # hard gate -> enforcement step must not be allowed to continue on error
    assert "continue-on-error: false" in wf
    # and the mode is surfaced in the rendered file
    assert "hard" in wf


# --- Azure DevOps ---------------------------------------------------------

def test_ado_has_eval_and_redteam_gate_stages_after_deploy():
    pipe = _ado_pipeline(_ado_framing())
    assert "eval_gate" in pipe
    assert "red_team_gate" in pipe
    assert "dependsOn: deploy" in pipe
    assert pipe.index("stage: deploy") < pipe.index("eval_gate")


def test_ado_gates_invoke_the_discover_legs_and_manifests():
    pipe = _ado_pipeline(_ado_framing())
    assert "threadlight-evals" in pipe
    assert "threadlight-redteam" in pipe
    assert "specs/evals-manifest.json" in pipe
    assert "specs/redteam-manifest.json" in pipe


def test_ado_gates_are_wif_and_secret_free():
    pipe = _ado_pipeline(_ado_framing())
    assert "AzureCLI@2" in pipe
    assert "AZURE_CREDENTIALS" not in pipe
    assert "clientSecret" not in pipe
    assert "{{" not in pipe


def test_ado_soft_default_and_hard_block():
    soft = _ado_pipeline(_ado_framing())
    assert "continueOnError: true" in soft
    hard = _ado_pipeline(_ado_framing(eval_gate="hard"))
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
