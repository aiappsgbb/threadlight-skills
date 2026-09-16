"""Exact native CLI against a loopback-only test app. No cloud or model calls.

Run explicitly in the isolated agentops-accelerator==0.14.0 environment.
Native results are produced by the installed package, never fixture wire models.
"""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from test_verified_pipeline import framing, generator
from test_native_observer_route import runtime
from test_release_runner import runner


@pytest.mark.skipif(os.environ.get("THREADLIGHT_NATIVE_AGENTOPS_TESTS") != "1",
                    reason="requires explicit isolated exact-native environment; not cloud acceptance")
@pytest.mark.parametrize("quality_passes", [True, False])
def test_actual_native_release_bridge_and_canonical_acceptance(tmp_path, monkeypatch, quality_passes):
    assert version("agentops-accelerator") == "0.14.0"
    from agentops.core.agentops_config import classify_agent
    from agentops.core.results import TargetInfo
    requests = []

    class Echo(BaseHTTPRequestHandler):
        def do_POST(self):
            row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(row)
            body = json.dumps({"text": row["message"] if quality_passes else "incorrect"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        root = tmp_path / "app"
        root.mkdir()
        agent_root = root / "agent"
        agent_root.mkdir()
        url = f"http://127.0.0.1:{server.server_port}"
        (agent_root / "agentops.yaml").write_text(
            f"version: 1\nagent: {url}\ndataset: data.jsonl\nexecution: local\n"
            "publish: false\nevaluators: [F1ScoreEvaluator]\nthresholds:\n  f1_score: '>=1'\n")
        (agent_root / "data.jsonl").write_text(
            '{"input":"local echo","expected":"local echo","tool_calls":[],"tool_outputs":[]}\n')
        (agent_root / "requirements.txt").write_text("agentops-accelerator==0.14.0\n")
        generator.generate(framing("github-actions"), root)
        (root / "evals").mkdir()
        (root / "evals/scenarios.md").write_text(
            "# Evaluation scenario\nReturn the supplied local echo; require f1_score >= 1.\n")
        (root / "evals/data.jsonl").write_text((agent_root / "data.jsonl").read_text())
        (root / "evals/nightly.sh").write_text(
            "#!/bin/sh\nset -eu\n"
            "python3 .threadlight/skills/threadlight-cicd/scripts/agentops_runtime.py "
            '--repo . --run-eval --context "${THREADLIGHT_AGENTOPS_CONTEXT:?}"\n')
        (root / ".github/workflows/evaluation.yml").write_text(
            "name: Scheduled evaluation\non:\n  schedule:\n    - cron: '0 2 * * *'\n"
            "jobs:\n  evaluation:\n    runs-on: self-hosted\n    steps:\n"
            "      - uses: actions/checkout@v4\n      - run: sh evals/nightly.sh\n"
            "      - name: Evaluation failure alert\n        if: failure()\n"
            "        run: echo '::error::Scheduled evaluation failed'\n")
        target = classify_agent(url)
        native_target = TargetInfo(**{key: getattr(target, key) for key in
                                    ("kind", "raw", "protocol", "name", "version", "url", "deployment")})
        contract = runtime._contract()
        binding = {
            "schema": "threadlight-agentops-binding/v1",
            "target_sha256": contract.canonical_hash(native_target.model_dump()),
            "environment_sha256": contract.sha256(b"loopback-only native test, no cloud authority"),
            "thresholds": [{"metric": "f1_score", "criteria": ">=", "expected": 1.0}],
            "artifact_paths": {"dataset": "data.jsonl", "workflow": ".github/workflows/azd-deploy-prod.yml"},
        }
        (agent_root / ".threadlight").mkdir()
        (agent_root / ".threadlight/agentops-binding.json").write_text(json.dumps(binding))
        for command in (["init", "-q"], ["config", "user.name", "Local native test"],
                        ["config", "user.email", "fixture@example.invalid"], ["add", "."],
                        ["commit", "-qm", "Reviewed loopback-only test inputs"]):
            subprocess.run(["git", *command], cwd=root, check=True, capture_output=True)
        for name in list(os.environ):
            if name.startswith(("AZURE_", "AZD_", "AGENTOPS_", "OPENAI_", "OTEL_", "APPLICATIONINSIGHTS_", "APPINSIGHTS_")):
                monkeypatch.delenv(name)
        cli, azd = tmp_path / "native-cli", tmp_path / "native-azd"
        cli.mkdir()
        azd.mkdir()
        environment = dict(AZURE_TOKEN_CREDENTIALS="AzureCliCredential", AZURE_CONFIG_DIR=str(cli),
                           AZD_CONFIG_DIR=str(azd), OTEL_SDK_DISABLED="true")
        for key, value in dict(environment, HOME=str(tmp_path), GITHUB_ACTIONS="true",
                               GITHUB_REPOSITORY="example/local", GITHUB_RUN_ID="71", GITHUB_RUN_ATTEMPT="1",
                               GITHUB_REF="refs/heads/main", GITHUB_EVENT_NAME="push",
                               GITHUB_SHA=contract.repository_state(root)["commit"]).items():
            monkeypatch.setenv(key, value)
        context = tmp_path / "context.json"
        context.write_text(json.dumps({"schema": "threadlight-agentops-context/v1", "environment": environment}))
        context.chmod(0o600)
        observer = runtime._observer()
        now = datetime.now(timezone.utc)
        approval = {
            "schema": "threadlight-agentops-runtime-approval/v1", "approved": True,
            "telemetry_scope_approved": True, "operation": "eval", "root": "agent",
            "repository_commit": contract.repository_state(root)["commit"],
            "config_sha256": contract.sha256(contract.read_bytes(agent_root, "agentops.yaml")),
            "target_sha256": binding["target_sha256"], "environment_sha256": binding["environment_sha256"],
            "run_id_sha256": observer.run_identity("eval"),
            "context_sha256": observer.execution_context_hash(root, agent_root, dict(os.environ)),
            "not_before": now.isoformat(), "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "raw_artifacts": {"scope": "agent/.agentops", "retention_hours": 1},
        }
        private = agent_root / ".agentops/threadlight/approvals"
        private.mkdir(parents=True)
        (private / "eval.json").write_text(json.dumps(approval))
        with runtime.native_context(context):
            observer._preflight(root, contract.discover_opted_in_agents(root)[0], "eval", None)
        request = tmp_path / "request.json"
        key = contract.discover_opted_in_agents(root)[0]["agent_key"]
        request.write_text(json.dumps({"ci": runner.ci_context(root),
                                      "candidate": {"evaluation_targets": {key: binding["target_sha256"]}}}))
        monkeypatch.setenv("THREADLIGHT_RELEASE_REQUEST", str(request))
        monkeypatch.setenv("THREADLIGHT_AGENTOPS_CONTEXT", str(context))
        deploy_cli, deploy_azd = tmp_path / "deployment-cli", tmp_path / "deployment-azd"
        deploy_cli.mkdir()
        deploy_azd.mkdir()
        preserved = deploy_azd / "existing-context"
        preserved.write_text("Local isolation fixture; not a credential.\n")
        monkeypatch.setenv("AZURE_CONFIG_DIR", str(deploy_cli))
        monkeypatch.setenv("AZD_CONFIG_DIR", str(deploy_azd))
        monkeypatch.setenv("AZURE_CLIENT_ID", "unrelated-deployment-fixture")
        script = root / ".threadlight/skills/threadlight-cicd/scripts/release_agentops.py"
        process = subprocess.run([sys.executable, str(script), "--repo", str(root)], cwd=root,
                                 capture_output=True, text=True, timeout=120)
        assert process.returncode == (0 if quality_passes else 2), process.stdout + process.stderr
        assert preserved.read_text() == "Local isolation fixture; not a credential.\n"
        assert len(requests) == 1
        manifest = runner.load(root / "specs/evals-manifest.json")
        required = {"eval_scenarios_present", "eval_datasets_present", "dataset_shape_ok", "thresholds_declared",
                    "run_history_present", "latest_eval_run_fresh", "latest_pass_rate_ok"}
        gate = runner._gate()
        if not quality_passes:
            assert manifest["capabilities"]["latest_pass_rate_ok"]["status"] != "pass"
            with pytest.raises(gate.EvidenceGateError):
                gate.validate_release_manifest(
                    "evals", manifest, now=datetime.now(timezone.utc), not_before=now,
                    required_capabilities=sorted(required))
            return
        assert not manifest["must_fix"], manifest
        statuses = {key: manifest["capabilities"][key]["status"] for key in required}
        assert set(statuses.values()) == {"pass"}, statuses
        accepted = gate.validate_release_manifest(
            "evals", manifest, now=datetime.now(timezone.utc), not_before=now,
            required_capabilities=sorted(required))
        assert accepted["status"] == "pass"
        assert manifest["metrics"]["pass_rate"] is None
        assert manifest["metrics"]["latest_run"] == "evals/runs/release-agentops.json"
        assert contract.load_manifest(root)["agents"][0]["domains"]["evals"]["status"] == "verified"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
