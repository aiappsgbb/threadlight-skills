#!/usr/bin/env python3
"""Single packaged native executor: explicit owner approval, no login or PKI.

Recorded identity/context is local configuration provenance, not Azure identity
attestation. Native commands and source diagnostics must still succeed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from importlib.metadata import PackageNotFoundError, version as package_version
import os
from pathlib import Path, PurePosixPath
import sys

_spec = importlib.util.spec_from_file_location(
    "_threadlight_observer_contract", Path(__file__).resolve().parents[2] / "_shared/agentops.py")
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)


def require(value, code):
    if not value:
        raise contract.AgentOpsValidationError(code)


def run_identity(operation):
    if os.environ.get("GITHUB_RUN_ID"):
        parts = ["github", os.environ.get("GITHUB_REPOSITORY"), os.environ["GITHUB_RUN_ID"],
                 os.environ.get("GITHUB_RUN_ATTEMPT", "1")]
    elif os.environ.get("BUILD_BUILDID"):
        parts = ["azure-devops", os.environ.get("SYSTEM_TEAMPROJECTID"), os.environ["BUILD_BUILDID"],
                 os.environ.get("SYSTEM_JOBATTEMPT", "1")]
    else:
        parts = ["local", os.environ.get("THREADLIGHT_AGENTOPS_RUN_ID")]
    require(all(isinstance(part, str) and 0 < len(part) <= 256 for part in parts),
            "current-run-identity-required")
    return contract.canonical_hash(parts + [operation])


def execution_context_hash(repo, root, environment):
    """Bind environment, dotenv, dataset and baseline bytes without publishing values."""
    files = {}
    candidates = [(root, "agentops.yaml"), (root, ".agentops/agent.yaml"),
                  (root, ".agentops/.env"), (root, ".env"), (repo, ".azure/config.json")]
    policy_path = ".threadlight/agentops-binding.json"
    policy = contract.read_json(root, policy_path, limit=65536)
    for role, relative in policy.get("artifact_paths", {}).items():
        require(role in {"dataset", "baseline", "workflow"}, "invalid-approved-artifact-paths")
        candidates.append((repo if role == "workflow" else root, relative))
    azure = contract.safe_path(repo, ".azure", exists=False)
    if azure.exists():
        require(azure.is_dir(), "unsafe-native-context")
        with os.scandir(azure) as entries:
            for index, entry in enumerate(entries):
                require(index < 64 and not entry.is_symlink(), "unsafe-native-context")
                if entry.is_dir(follow_symlinks=False):
                    candidates.append((repo, f".azure/{entry.name}/.env"))
    for base, relative in candidates:
        path = contract.safe_path(base, relative, exists=False)
        if path.exists():
            files[path.relative_to(repo).as_posix()] = contract.sha256(
                contract.read_bytes(base, relative, limit=contract.MAX_ARTIFACT_BYTES))
    values = {key: value for key, value in environment.items()
              if key.startswith(("AZURE_", "AZD_", "AGENTOPS_", "OPENAI_", "OTEL_",
                                 "APPLICATIONINSIGHTS_", "APPINSIGHTS_"))}
    require(len(values) <= 256 and all(len(value) <= 32768 for value in values.values()),
            "native-context-too-large")
    return contract.canonical_hash({"files": files, "environment": values})


def _native_executable(selected):
    native = Path(selected) if selected else Path(sys.executable).parent / "agentops"
    require(native.is_absolute() and native.is_file() and not native.is_symlink()
            and native.name in {"agentops", "agentops.exe"}
            and native.parent.absolute() == Path(sys.executable).parent.absolute()
            and os.access(native, os.X_OK), "native-runtime-prerequisites-missing")
    return native


def _preflight(repo, identity, operation, agentops_bin, *, verify_previous=True):
    root = repo if identity["root"] == "." else contract.safe_path(repo, identity["root"], exists=False)
    policy = contract.parse_json(contract._committed_bytes(repo,
        str(PurePosixPath(identity["root"]) / ".threadlight/agentops-binding.json")))
    require(isinstance(policy, dict) and policy.get("schema") == "threadlight-agentops-binding/v1",
            "committed-binding-policy-required")
    require(type(policy.get("require_signature", False)) is bool
            and isinstance(policy.get("artifact_paths", {}), dict)
            and all(isinstance(policy.get(key), str) and contract._HASH.fullmatch(policy[key])
                    for key in ("target_sha256", "environment_sha256")), "invalid-binding-policy")
    require(policy.get("require_signature") is not True, "existing-signature-route-selected")
    raw = contract.read_bytes(root, f".agentops/threadlight/approvals/{operation}.json", limit=65536)
    approval = contract.parse_json(raw)
    require(isinstance(approval, dict) and approval.get("schema") == "threadlight-agentops-runtime-approval/v1"
            and approval.get("approved") is True and approval.get("telemetry_scope_approved") is True,
            "scoped-owner-approval-required")
    environment = dict(os.environ)
    selector = environment.get("AZURE_TOKEN_CREDENTIALS")
    require(selector in {"AzureCliCredential", "EnvironmentCredential", "WorkloadIdentityCredential"},
            "explicit-credential-selector-required")
    directories = {}
    for variable in ("AZURE_CONFIG_DIR", "AZD_CONFIG_DIR"):
        value = environment.get(variable, "")
        directory = Path(value)
        require(directory.is_absolute() and directory.is_dir() and not directory.is_symlink(),
                "existing-isolated-credential-context-required")
        directories[variable] = directory
    require(directories["AZURE_CONFIG_DIR"] != directories["AZD_CONFIG_DIR"],
            "distinct-credential-contexts-required")
    require(not any(directories["AZD_CONFIG_DIR"].iterdir()), "credential-empty-azd-context-required")
    if selector != "AzureCliCredential":
        require(not any(directories["AZURE_CONFIG_DIR"].iterdir()), "credential-empty-cli-context-required")
    require(not environment.get("AGENTOPS_AGENT"), "unreviewed-native-target-override")
    state = contract.repository_state(repo)
    require(state["commit"] and not state["dirty"], "clean-reviewed-source-required")
    expected = {"operation": operation, "repository_commit": state["commit"], "root": identity["root"],
        "config_sha256": contract.sha256(contract.read_bytes(root, "agentops.yaml", limit=65536)),
        "target_sha256": policy.get("target_sha256"), "environment_sha256": policy.get("environment_sha256"),
        "run_id_sha256": run_identity(operation), "context_sha256": execution_context_hash(repo, root, environment)}
    require(all(approval.get(key) == value for key, value in expected.items()), "owner-approval-binding-mismatch")
    now = datetime.now(timezone.utc)
    start, end = contract._timestamp(approval.get("not_before")), contract._timestamp(approval.get("expires_at"))
    require(start <= now < end and end - start <= timedelta(hours=24), "owner-approval-expired")
    retention = approval.get("raw_artifacts")
    require(isinstance(retention, dict) and retention.get("scope") == str(PurePosixPath(identity["root"]) / ".agentops")
            and type(retention.get("retention_hours")) is int and 1 <= retention["retention_hours"] <= 720,
            "explicit-private-retention-required")
    try:
        require(package_version("agentops-accelerator") == contract.NATIVE_VERSION,
                "native-version-pin-required")
        installed = package_version("azure-identity").split(".")
        require(len(installed) == 3 and installed[:2] == ["1", "25"] and installed[2].isdigit()
                and int(installed[2]) >= 3, "approved-azure-identity-version-required")
    except PackageNotFoundError:
        raise contract.AgentOpsValidationError("native-runtime-prerequisites-missing") from None
    native = _native_executable(agentops_bin)
    paths = dict(policy.get("artifact_paths", {}))
    require(set(paths) <= {"dataset", "baseline", "workflow"}, "invalid-approved-artifact-paths")
    if operation == "eval":
        require(isinstance(paths.get("dataset"), str), "approved-dataset-path-required")
        paths.update(analysis=f".agentops/threadlight/runs/{expected['run_id_sha256']}/analysis.json",
                     latest=".agentops/results/latest/results.json")
    else:
        require((root / ".agentops/agent.yaml").is_file(), "explicit-doctor-config-required")
        if verify_previous:
            contract.verify_receipt(repo, root, now=now, hours=8760)
        paths.update(evidence=".agentops/release/latest/evidence.json", history=".agentops/agent/history.jsonl")
    if policy.get("baseline_sha256"):
        require("baseline" in paths, "explicit-bound-baseline-required")
    if (root / ".agentops/baseline/results.json").exists():
        require("baseline" in paths, "explicit-bound-baseline-required")
    if "baseline" in paths:
        baseline = contract.read_json(root, paths["baseline"])
        dataset_path = paths.get("dataset")
        require(isinstance(dataset_path, str)
                and contract.sha256(contract.read_bytes(root, paths["baseline"])) == policy.get("baseline_sha256")
                and contract.canonical_hash(baseline.get("target")) == policy.get("baseline_target_sha256")
                and contract.sha256(contract.read_bytes(root, dataset_path)) == policy.get("baseline_dataset_sha256"),
                "explicit-bound-baseline-required")
        if operation == "eval":
            require(contract._fresh(baseline.get("finished_at"), now, 24), "fresh-bound-baseline-required")
    severity = approval.get("doctor_severity", "critical")
    require(severity in {"critical", "warning", "info"}, "invalid-doctor-severity")
    for role, relative in paths.items():
        contract.safe_path(repo if role == "workflow" else root, relative,
                           exists=role in {"dataset", "baseline", "workflow"})
    return {"identity": identity, "root": root, "operation": operation, "native": native,
        "approval_sha256": contract.sha256(raw), "run_id_sha256": expected["run_id_sha256"],
        "paths": paths, "expires_at": end, "environment": environment, "doctor_severity": severity}


def _write_private(root, relative, value):
    path = contract.safe_path(root, relative, exists=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise contract.AgentOpsValidationError("current-run-approval-already-used") from None


def _native_environment(environment=None):
    result = dict(os.environ if environment is None else environment)
    for key in ("GITHUB_STEP_SUMMARY", "BASH_ENV", "PYTHONPATH", "PYTHONHOME"):
        result.pop(key, None)
    result["PYTHONNOUSERSITE"] = "1"
    return result


def observe(repo, *, operation, agentops_bin=None, run_command=None):
    """Execute approved commands, then return metadata-only normalized evidence."""
    require(operation in {"eval", "doctor"}, "unsupported-native-operation")
    repo = Path(repo).resolve()
    identities = contract.discover_opted_in_agents(repo)
    plans = [_preflight(repo, identity, operation, agentops_bin) for identity in identities]
    execute = run_command or contract.bounded_command
    old_umask = os.umask(0o077)
    try:
        for plan in plans:
            _write_private(plan["root"], f".agentops/threadlight/used-{plan['run_id_sha256']}.json",
                           plan["approval_sha256"].encode())
            token = contract.begin_observation(repo, plan["root"], operation=operation,
                run_id_sha256=plan["run_id_sha256"], approval_sha256=plan["approval_sha256"],
                artifact_paths=plan["paths"])

            def invoke(arguments, phase):
                checked = _preflight(repo, plan["identity"], operation, agentops_bin)
                require(checked["approval_sha256"] == plan["approval_sha256"], "owner-approval-changed")
                remaining = (plan["expires_at"] - datetime.now(timezone.utc)).total_seconds()
                require(remaining > 0, "owner-approval-expired")
                environment = _native_environment(plan["environment"])
                code, stdout, stderr = execute([str(plan["native"]), *arguments], cwd=plan["root"],
                    timeout=min(1200, remaining), max_bytes=1024 * 1024, env=environment)
                for label, data in (("stdout", stdout), ("stderr", stderr)):
                    _write_private(plan["root"],
                        f".agentops/threadlight/runs/{plan['run_id_sha256']}/{phase}.{label}.log", data)
                checked = _preflight(repo, plan["identity"], operation, agentops_bin,
                                     verify_previous=False)
                require(checked["approval_sha256"] == plan["approval_sha256"], "owner-approval-changed")
                return code, stdout

            try:
                if operation == "eval":
                    code, stdout = invoke(["eval", "analyze", "--dir", ".", "--format", "json"], "analysis")
                    require(code == 0, "native-analysis-failed")
                    analysis = contract.parse_json(stdout)
                    contract._version(analysis)
                    require(analysis.get("config_status") == "ready" and analysis.get("dataset_status") == "ready"
                            and analysis.get("requires_copilot_adaptation") is False, "native-analysis-not-ready")
                    _write_private(plan["root"], plan["paths"]["analysis"], stdout)
                    arguments = ["eval", "run", "--config", "agentops.yaml"]
                    if plan["paths"].get("baseline"):
                        arguments += ["--baseline", "./" + plan["paths"]["baseline"]]
                else:
                    arguments = ["doctor", "--workspace", ".", "--config", ".agentops/agent.yaml",
                                 "--evidence-pack", "--severity-fail", plan["doctor_severity"]]
                code, _ = invoke(arguments, operation)
                require(code in {0, 2}, "native-operation-failed")
                contract.finish_observation(token, exit_code=code)
            except BaseException:
                contract.cancel_observation(token)
                raise
        return contract.assess_repository(repo)
    finally:
        os.umask(old_umask)
