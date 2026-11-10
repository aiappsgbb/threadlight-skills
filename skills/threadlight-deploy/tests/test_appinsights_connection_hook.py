"""Behaviour of the generated azd postprovision hook that connects App Insights
to the Foundry project. A fake ``az``/``azd`` on PATH simulates the service.

Live 2026-11-10 (eastus2): every connection write returned 500 InternalServerError
although the service saved it, so ARM retried the Bicep PUT until the deployment
was cancelled. The hook must accept a saved connection after an error, fail when
nothing was saved, skip the write when the connection already exists, and never
print the App Insights connection string.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
PILOT = DEPLOY / "references" / "hosted-agent" / "ghcp" / "references" / "pilot"
GENERATOR = DEPLOY / "scripts" / "generate_only.py"
FIXTURE = DEPLOY / "tests" / "fixtures" / "generate-only" / "order-returns"
PROJECT_ID = ("/subscriptions/s/resourceGroups/rg/providers/Microsoft.CognitiveServices/"
              "accounts/ais-x/projects/proj-x")
APPI_ID = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Insights/components/appi-x"
SECRET = "InstrumentationKey=00000000-1111-2222-3333-444444444444;IngestionEndpoint=https://x"

FAKE_AZ = r'''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(" ".join(a for a in args if not a.startswith("@")) + "\n")
method = args[args.index("--method") + 1] if "--method" in args else ""
if method == "get":
    print(open(state).read() if os.path.exists(state) else json.dumps({"value": []}))
    sys.exit(0)
if method == "put":
    body = json.load(open(args[args.index("--body") + 1][1:]))
    if os.environ.get("FAKE_PUT_PERSISTS") == "1":
        name = args[args.index("--url") + 1].split("/connections/")[1].split("?")[0]
        json.dump({"value": [{"name": name, "properties": body["properties"]}]}, open(state, "w"))
    if os.environ.get("FAKE_PUT_FAILS") == "1":
        sys.stderr.write('Internal Server Error({"error":{"code":"ServiceError","message":"InternalServerError"}})\n')
        sys.exit(1)
    print(json.dumps({"name": "ok"}))
    sys.exit(0)
sys.exit(2)
'''
FAKE_AZD = '#!/bin/sh\necho "azd $*" >> "$FAKE_LOG"\n'


def _hook(tmp_path: Path, *, existing: bool = False, put_fails: bool = False, persists: bool = True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("az", FAKE_AZ), ("azd", FAKE_AZD)):
        (bin_dir / name).write_text(body.replace("/usr/bin/env python3", sys.executable, 1) if name == "az" else body)
        (bin_dir / name).chmod(0o755)
    state, log = tmp_path / "state.json", tmp_path / "calls.log"
    if existing:
        state.write_text(json.dumps({"value": [{"name": "appi-x", "properties": {
            "category": "AppInsights", "target": APPI_ID.upper()}}]}))
    env = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "HOME": str(tmp_path),
        "AZURE_AI_PROJECT_ID": PROJECT_ID,
        "APPLICATIONINSIGHTS_RESOURCE_ID": APPI_ID,
        "APPLICATIONINSIGHTS_CONNECTION_STRING": SECRET,
        "THREADLIGHT_HOOK_RETRY_SLEEP": "0",
        "FAKE_STATE": str(state), "FAKE_LOG": str(log),
        "FAKE_PUT_FAILS": "1" if put_fails else "0",
        "FAKE_PUT_PERSISTS": "1" if persists else "0",
        "TMPDIR": str(tmp_path),
    }
    result = subprocess.run(["sh", str(PILOT / "hooks" / "postprovision.sh")], capture_output=True,
                            text=True, env=env, timeout=60)
    calls = log.read_text() if log.exists() else ""
    return result, calls


def test_creates_the_connection_and_records_its_id(tmp_path: Path):
    result, calls = _hook(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--method put" in calls
    assert f"connections/appi-x?api-version=" in calls
    assert f"azd env set AZURE_AI_APPINSIGHTS_CONNECTION_ID {PROJECT_ID}/connections/appi-x" in calls
    assert SECRET not in result.stdout + result.stderr + calls
    assert not list(tmp_path.glob("tmp*")), "request body file left behind"


def test_accepts_a_saved_connection_after_a_service_error(tmp_path: Path):
    result, calls = _hook(tmp_path, put_fails=True, persists=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "saved" in (result.stdout + result.stderr).lower()
    assert calls.count("--method put") == 1
    assert SECRET not in result.stdout + result.stderr + calls


def test_fails_when_the_write_errors_and_nothing_was_saved(tmp_path: Path):
    result, calls = _hook(tmp_path, put_fails=True, persists=False)
    assert result.returncode != 0
    assert "InternalServerError" in result.stderr
    assert "AZURE_AI_APPINSIGHTS_CONNECTION_ID" not in calls
    assert SECRET not in result.stdout + result.stderr + calls


def test_existing_connection_is_reused_without_a_write(tmp_path: Path):
    result, calls = _hook(tmp_path, existing=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--method put" not in calls
    assert f"AZURE_AI_APPINSIGHTS_CONNECTION_ID {PROJECT_ID}/connections/appi-x" in calls


def test_generated_project_wires_the_hook(tmp_path: Path):
    project = tmp_path / "order-returns"
    shutil.copytree(FIXTURE, project)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AZURE_", "AZD_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, str(GENERATOR), "--project", str(project)],
                            capture_output=True, text=True, env=env, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    hook = project / "hooks" / "postprovision.sh"
    assert hook.read_bytes() == (PILOT / "hooks" / "postprovision.sh").read_bytes()
    assert hook.stat().st_mode & stat.S_IXUSR
    yaml = pytest.importorskip("yaml")
    hooks = yaml.safe_load((project / "azure.yaml").read_text())["hooks"]
    assert hooks["postprovision"]["posix"]["run"] == "sh hooks/postprovision.sh"
    assert hooks["postprovision"]["posix"]["continueOnError"] is False
