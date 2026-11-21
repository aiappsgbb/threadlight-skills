"""Behaviour of the MAF template postprovision hook that grants data-plane roles.

With ``infra.provider: microsoft.foundry`` a fresh ``azd provision`` gives the
project managed identity only AcrPull and the deploying user no Foundry data-plane
role, so ``azd ai eval generate``, evaluation rules and app-only Insights runs fail
until roles are added by hand (#212). A fake ``az`` on PATH records role
assignments so the hook can be checked offline: role IDs only, the documented
scopes, and a re-run that creates nothing.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

MAF = Path(__file__).resolve().parents[1] / "references" / "hosted-agent" / "maf" / "references"
HOOK = MAF / "hooks" / "postprovision.sh"
ACCOUNT = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/cog-x"
PROJECT = ACCOUNT + "/projects/proj-x"
APPI = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Insights/components/appi-x"
LAW = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/log-x"
JUDGE = "/subscriptions/s/resourceGroups/rg2/providers/Microsoft.CognitiveServices/accounts/judge-x"
MI, USER = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
FOUNDRY_USER = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
OPENAI_USER = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
MONITORING_READER = "43d0d8ad-25c7-4714-9337-8ba259a9fe05"
PRIVILEGED_READER = "dbc9c667-e97f-4491-aee6-90b9cf960190"
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

FAKE_AZ = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
state = os.environ["FAKE_STATE"]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
def opt(name):
    return args[args.index(name) + 1]
rows = json.load(open(state)) if os.path.exists(state) else []
if args[:1] == ["rest"]:
    print(os.environ.get("FAKE_MI", ""))
elif args[:3] == ["ad", "signed-in-user", "show"]:
    print(os.environ["FAKE_USER"])
elif args[:3] == ["role", "assignment", "list"]:
    print(sum(1 for r in rows if r["scope"] == opt("--scope") and r["role"] == opt("--role")
              and "'" + r["principal"] + "'" in opt("--query")))
elif args[:3] == ["role", "assignment", "create"]:
    rows.append({"principal": opt("--assignee-object-id"), "type": opt("--assignee-principal-type"),
                 "role": opt("--role"), "scope": opt("--scope")})
    json.dump(rows, open(state, "w"))
else:
    sys.exit(2)
'''


def _run(tmp_path: Path, **overrides):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "az").write_text(FAKE_AZ.replace("/usr/bin/env python3", sys.executable, 1))
    (bin_dir / "az").chmod(0o755)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(tmp_path),
        "AZURE_AI_PROJECT_ID": PROJECT, "AZURE_APPLICATION_INSIGHTS_RESOURCE_ID": APPI,
        "AZURE_LOG_ANALYTICS_WORKSPACE_RESOURCE_ID": LAW, "THREADLIGHT_HOOK_RETRY_SLEEP": "0",
        "FAKE_STATE": str(tmp_path / "state.json"), "FAKE_LOG": str(tmp_path / "calls.log"),
        "FAKE_MI": MI, "FAKE_USER": USER,
    }
    env.update(overrides)
    result = subprocess.run(["sh", str(HOOK)], capture_output=True, text=True, env=env, timeout=60)
    state = tmp_path / "state.json"
    rows = json.loads(state.read_text()) if state.exists() else []
    calls = [json.loads(line) for line in (tmp_path / "calls.log").read_text().splitlines()] \
        if (tmp_path / "calls.log").exists() else []
    return result, {(r["principal"], r["role"], r["scope"]) for r in rows}, rows, calls


def test_fresh_provision_grants_documented_roles_by_id(tmp_path):
    result, granted, rows, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert granted == {
        (MI, FOUNDRY_USER, ACCOUNT), (MI, OPENAI_USER, ACCOUNT),
        (MI, MONITORING_READER, APPI), (MI, MONITORING_READER, LAW),
        (USER, FOUNDRY_USER, ACCOUNT),
    }
    assert {r["type"] for r in rows if r["principal"] == MI} == {"ServicePrincipal"}
    roles = [c[c.index("--role") + 1] for c in calls if "--role" in c]
    assert roles and all(GUID.match(role) for role in roles)


def test_rerun_is_a_no_op(tmp_path):
    first, granted, _, _ = _run(tmp_path)
    assert first.returncode == 0, first.stderr
    (tmp_path / "calls.log").unlink()
    second, again, _, calls = _run(tmp_path)
    assert second.returncode == 0, second.stderr
    assert again == granted
    assert not [c for c in calls if c[:3] == ["role", "assignment", "create"]]
    assert "already present" in second.stdout


def test_protected_genai_content_uses_privileged_reader_and_judge_account(tmp_path):
    result, granted, _, _ = _run(tmp_path, THREADLIGHT_PROTECTED_GENAI_CONTENT="true",
                                 THREADLIGHT_JUDGE_ACCOUNT_ID=JUDGE)
    assert result.returncode == 0, result.stderr
    assert (MI, PRIVILEGED_READER, APPI) in granted and (MI, PRIVILEGED_READER, LAW) in granted
    assert not {g for g in granted if g[1] == MONITORING_READER}
    assert (MI, OPENAI_USER, JUDGE) in granted and (MI, OPENAI_USER, ACCOUNT) not in granted


def test_pipeline_principal_and_skip_deployer(tmp_path):
    result, granted, rows, _ = _run(tmp_path, AZURE_PRINCIPAL_ID="33333333-3333-3333-3333-333333333333",
                                    AZURE_PRINCIPAL_TYPE="ServicePrincipal")
    assert result.returncode == 0, result.stderr
    pipeline = [r for r in rows if r["principal"].startswith("3333")]
    assert pipeline == [{"principal": "33333333-3333-3333-3333-333333333333", "type": "ServicePrincipal",
                         "role": FOUNDRY_USER, "scope": ACCOUNT}]
    skipped = tmp_path / "skip"
    skipped.mkdir()
    result, granted, _, _ = _run(skipped, THREADLIGHT_SKIP_DEPLOYER_ROLE="true")
    assert result.returncode == 0, result.stderr
    assert {g[0] for g in granted} == {MI}


def test_missing_project_identity_fails_closed(tmp_path):
    result, granted, _, _ = _run(tmp_path, FAKE_MI="")
    assert result.returncode != 0
    assert not granted
    assert "managed identity" in result.stderr


def test_hook_never_uses_role_display_names():
    body = HOOK.read_text()
    assert "--role \"$3\"" in body
    for name in ("Azure AI User", "Foundry User\"", "Monitoring Reader\""):
        assert f"--role \"{name}" not in body and f"--role '{name}" not in body


def test_template_azure_yaml_wires_the_hook():
    spec = yaml.safe_load((MAF / "yaml" / "azure.yaml").read_text())
    hook = spec["hooks"]["postprovision"]["posix"]
    assert hook["run"] == "sh hooks/postprovision.sh"
    assert hook.get("continueOnError") is False
