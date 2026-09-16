"""Private native context selection does not grant authorization or log in."""
import json
import os
from pathlib import Path

import pytest

from test_native_observer_route import runtime


def test_native_context_replaces_inherited_cloud_credentials_and_restores_them(tmp_path, monkeypatch):
    context = tmp_path / "context.json"
    context.write_text(json.dumps({
        "schema": "threadlight-agentops-context/v1",
        "environment": {"AZURE_TOKEN_CREDENTIALS": "AzureCliCredential",
                        "AZURE_CONFIG_DIR": str(tmp_path / "native-cli"),
                        "AZD_CONFIG_DIR": str(tmp_path / "native-azd")},
    }))
    context.chmod(0o600)
    monkeypatch.setenv("AZURE_CLIENT_ID", "broad-deploy-client")
    monkeypatch.setenv("AZD_CONFIG_DIR", "populated-deploy-context")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://not-approved.invalid")
    with runtime.native_context(context):
        assert "AZURE_CLIENT_ID" not in os.environ
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ
        assert os.environ["AZD_CONFIG_DIR"] == str(tmp_path / "native-azd")
    assert os.environ["AZURE_CLIENT_ID"] == "broad-deploy-client"
    assert os.environ["AZD_CONFIG_DIR"] == "populated-deploy-context"


@pytest.mark.parametrize("environment", [{"PATH": "/malicious"}, {"GITHUB_RUN_ID": "another"}, {}])
def test_native_context_cannot_replace_process_or_ci_authority(tmp_path, environment):
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"schema": "threadlight-agentops-context/v1", "environment": environment}))
    context.chmod(0o600)
    with pytest.raises(ValueError):
        with runtime.native_context(context):
            pytest.fail("invalid native context accepted")


def test_native_context_is_not_approval(tmp_path, monkeypatch):
    (tmp_path / "agentops.yaml").write_text("version: 1\nagent: local:1\n")
    context = tmp_path / "context.json"
    context.write_text(json.dumps({
        "schema": "threadlight-agentops-context/v1",
        "environment": {"AZURE_TOKEN_CREDENTIALS": "AzureCliCredential",
                        "AZURE_CONFIG_DIR": str(tmp_path / "cli"), "AZD_CONFIG_DIR": str(tmp_path / "azd")},
    }))
    context.chmod(0o600)
    assert runtime.main(["--repo", str(tmp_path), "--run-eval", "--context", str(context)]) == 1
    assert not (tmp_path / ".agentops").exists()


def test_runtime_reports_fixed_refusal_code_without_raw_diagnostics(tmp_path, monkeypatch, capsys):
    (tmp_path / "agentops.yaml").write_text("version: 1\nagent: local:1\n")
    def expired(*args, **kwargs):
        raise runtime._observer().contract.AgentOpsValidationError("owner-approval-expired")
    monkeypatch.setattr(runtime, "observe", expired)
    assert runtime.main(["--repo", str(tmp_path), "--run-eval"]) == 1
    assert "owner-approval-expired" in capsys.readouterr().err
