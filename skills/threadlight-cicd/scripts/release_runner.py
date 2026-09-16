#!/usr/bin/env python3
"""Execute approved release adapters and bind acceptance to this CI attempt."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

SCHEMA = "threadlight-release/v1"
MANIFESTS = {"evals": "specs/evals-manifest.json", "redteam": "specs/redteam-manifest.json",
             "mcp": "tests/mcp-sbom.json"}
TOOL_ROOT = Path(__file__).resolve().parents[2]


class ReleaseError(ValueError):
    pass


def parse_document(text: str) -> dict:
    def constant(value):
        raise ReleaseError(f"non-finite JSON value: {value}")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ReleaseError("duplicate JSON field")
            result[key] = value
        return result

    value = json.loads(text, parse_constant=constant, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ReleaseError("expected a JSON object")
    return value


def load(path: Path) -> dict:
    return parse_document(path.read_text(encoding="utf-8"))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_path(root: Path, value: str) -> Path:
    if (not isinstance(value, str) or not value or Path(value).is_absolute()
            or ".." in Path(value).parts or any(ord(ch) < 32 for ch in value)):
        raise ReleaseError("release paths must be relative to the project")
    path = root / value
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ReleaseError("release paths must not escape the project")
    return path


def _command(value) -> list[str]:
    if (not isinstance(value, list) or not value or len(value) > 64
            or any(not isinstance(item, str) or not item or "\x00" in item for item in value)):
        raise ReleaseError("an explicit executable argv is required; no shell command strings")
    return value


def policy(root: Path, path: str) -> dict:
    value = load(local_path(root, path))
    expected = {"schema", "validation", "production", "inputs", "evals", "redteam",
                "max_age_seconds", "timeout_seconds"}
    if set(value) != expected or value["schema"] != "threadlight-release-policy/v1":
        raise ReleaseError("invalid release policy schema or fields")
    for key, maximum in (("max_age_seconds", 86400), ("timeout_seconds", 14400)):
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ReleaseError(f"invalid {key}")
    for phase, action in (("validation", "prepare"), ("production", "promote")):
        target = value[phase]
        if not isinstance(target, dict) or set(target) != {
            "environment", "tenant_id", "subscription_id", "resource_group", "target_id", action, "observe"
        }:
            raise ReleaseError(f"invalid {phase} target")
        for key in ("environment", "tenant_id", "subscription_id", "resource_group", "target_id"):
            item = target[key]
            if (not isinstance(item, str) or not item.strip() or item != item.strip()
                    or any(ord(ch) < 32 for ch in item) or "REPLACE" in item or "<" in item):
                raise ReleaseError(f"{phase}.{key} must be explicitly configured")
        _command(target[action])
        _command(target["observe"])
    before, after = value["validation"], value["production"]
    if (before["environment"] == after["environment"] or before["target_id"] == after["target_id"]
            or (before["subscription_id"], before["resource_group"].lower()) ==
               (after["subscription_id"], after["resource_group"].lower())):
        raise ReleaseError("validation and production must have distinct environments and RG-scoped targets")
    inputs = value["inputs"]
    if (not isinstance(inputs, list) or not inputs or any(not isinstance(item, str) for item in inputs)
            or len(set(inputs)) != len(inputs)):
        raise ReleaseError("explicit source/dataset/config input paths are required")
    for item in inputs:
        if not local_path(root, item).is_file():
            raise ReleaseError(f"required release input missing: {item}")
    for domain in ("evals", "redteam"):
        config = value[domain]
        if not isinstance(config, dict) or set(config) != {"producer", "outputs", "acceptance"}:
            raise ReleaseError(f"invalid {domain} producer")
        _command(config["producer"])
        outputs = config["outputs"]
        if (not isinstance(outputs, list) or any(not isinstance(item, str) for item in outputs)
                or len(outputs) < 2 or len(set(outputs)) != len(outputs)
                or MANIFESTS[domain] not in outputs):
            raise ReleaseError(f"{domain} must declare its manifest and raw run outputs")
        for output in outputs:
            local_path(root, output)
            if output in inputs or output == path or output.startswith(".threadlight/"):
                raise ReleaseError("producer outputs cannot replace release inputs or tooling")
        _criteria(domain, config["acceptance"])
    if set(value["evals"]["outputs"]) & set(value["redteam"]["outputs"]):
        raise ReleaseError("evidence producers must own distinct outputs")
    return value


def _criteria(domain: str, criteria) -> None:
    allowed = ({"required_capabilities", "min_pass_rate"} if domain == "evals"
               else {"max_asr", "min_attacks"})
    if not isinstance(criteria, dict) or set(criteria) - allowed:
        raise ReleaseError("unsupported release acceptance override")
    for key in ("min_pass_rate", "max_asr"):
        if key in criteria and not _gate()._release_ratio(criteria[key]):
            raise ReleaseError(f"invalid release criterion: {key}")
    if "min_attacks" in criteria and (type(criteria["min_attacks"]) is not int or criteria["min_attacks"] < 1):
        raise ReleaseError("invalid minimum attack count")
    if "required_capabilities" in criteria:
        required = criteria["required_capabilities"]
        mandatory = {"eval_scenarios_present", "eval_datasets_present", "dataset_shape_ok",
                     "thresholds_declared", "run_history_present", "latest_eval_run_fresh",
                     "latest_pass_rate_ok"}
        if (not isinstance(required, list) or any(not isinstance(item, str) for item in required)
                or len(set(required)) != len(required) or not mandatory <= set(required)
                or set(required) - _gate()._EVALS_CAPABILITIES):
            raise ReleaseError("invalid required release capabilities")


def ci_context(root: Path) -> dict:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        names = ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
        provider = "github-actions"
        if os.environ.get("GITHUB_REF") != "refs/heads/main" or os.environ.get("GITHUB_EVENT_NAME") not in {
            "push", "workflow_dispatch"
        }:
            raise ReleaseError("release execution requires main, not PR or scheduled execution")
    elif os.environ.get("TF_BUILD", "").lower() == "true":
        names = ("BUILD_REPOSITORY_ID", "BUILD_SOURCEVERSION", "BUILD_BUILDID", "SYSTEM_STAGEATTEMPT")
        provider = "azure-devops"
        if os.environ.get("BUILD_SOURCEBRANCH") != "refs/heads/main" or os.environ.get("BUILD_REASON") in {
            "PullRequest", "Schedule"
        }:
            raise ReleaseError("release execution requires main, not PR or scheduled execution")
    else:
        raise ReleaseError("a recognized CI execution context is required")
    values = [os.environ.get(name, "") for name in names]
    if (not values[0] or not re.fullmatch(r"[a-f0-9]{40}", values[1])
            or any(not re.fullmatch(r"[1-9][0-9]*", item) for item in values[2:])):
        raise ReleaseError("incomplete source/run/attempt context")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()
    if head != values[1]:
        raise ReleaseError("checkout does not match CI source")
    return dict(provider=provider, repository=values[0], source_sha=head,
                run_id=values[2], attempt=values[3])


def inputs(root: Path, plan: dict, policy_path: str) -> dict:
    paths = set(plan["inputs"]) | {policy_path}
    for phase, action in (("validation", "prepare"), ("production", "promote")):
        for argv in (plan[phase][action], plan[phase]["observe"]):
            paths.update(item for item in argv
                         if not Path(item).is_absolute() and not item.startswith("-") and (root / item).is_file())
    for domain in ("evals", "redteam"):
        paths.update(item for item in plan[domain]["producer"]
                     if not Path(item).is_absolute() and not item.startswith("-") and (root / item).is_file())
    result = {name: file_digest(local_path(root, name)) for name in sorted(paths)}
    changed = subprocess.run(["git", "diff", "HEAD", "--name-only"], cwd=root, check=True,
                             capture_output=True, text=True).stdout.splitlines()
    allowed = {item for domain in ("evals", "redteam") for item in plan[domain]["outputs"]}
    allowed.add(MANIFESTS["mcp"])
    if set(changed) - allowed:
        raise ReleaseError("tracked source changed after checkout")
    for name in paths:
        subprocess.run(["git", "ls-files", "--error-unmatch", "--", name], cwd=root,
                       check=True, capture_output=True)
    return result


def execute(root: Path, argv: list[str], timeout: int, context: dict, label: str) -> str:
    directory = root / ".threadlight-release-private"
    directory.mkdir(mode=0o700, exist_ok=True)
    request = directory / "request.json"
    request.write_text(json.dumps(context, allow_nan=False), encoding="utf-8")
    request.chmod(0o600)
    with (directory / f"{label}.stdout").open("w+", encoding="utf-8") as output, (
        directory / f"{label}.stderr").open("w", encoding="utf-8") as error:
        env = {key: value for key, value in os.environ.items()
               if key not in {"GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_PATH", "GITHUB_STEP_SUMMARY"}}
        env["THREADLIGHT_RELEASE_REQUEST"] = str(request.resolve())
        with subprocess.Popen(argv, cwd=root, stdout=output, stderr=error, env=env,
                              start_new_session=True) as process:
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise ReleaseError(f"{label} timed out; outcome unknown, stop and reconcile") from error
        if code:
            raise ReleaseError(f"{label} failed with exit {code}; inspect protected runner logs")
        output.seek(0)
        text = output.read(2_000_001)
        if len(text) > 2_000_000:
            raise ReleaseError(f"{label} output exceeded the bounded capture size")
        return text


def verify_identity(plan: dict, phase: str) -> None:
    target = plan[phase]
    if os.environ.get("THREADLIGHT_RELEASE_ENVIRONMENT") != target["environment"]:
        raise ReleaseError("CI environment differs from the approved release target")
    result = subprocess.run(["az", "account", "show", "--output", "json"], check=True,
                            capture_output=True, text=True, timeout=30)
    account = json.loads(result.stdout)
    if account.get("tenantId") != target["tenant_id"] or account.get("id") != target["subscription_id"]:
        raise ReleaseError("authenticated tenant/subscription differs from release policy")


def observe(root: Path, plan: dict, phase: str, context: dict, started: datetime) -> dict:
    raw = execute(root, plan[phase]["observe"], plan["timeout_seconds"], context, f"{phase}-observe")
    observation = parse_document(raw)
    required = {
        "schema", "target_id", "environment", "source_sha", "image_digest", "version", "observed_at"
    }
    if not required <= set(observation) or set(observation) - required - {"evaluation_targets"}:
        raise ReleaseError("observer must return the exact deployed-image/version observation")
    if "evaluation_targets" in observation:
        targets = observation["evaluation_targets"]
        if (not isinstance(targets, dict) or not targets
                or any(not isinstance(key, str) or not key or not isinstance(value, str)
                       or not re.fullmatch(r"[a-f0-9]{64}", value) for key, value in targets.items())):
            raise ReleaseError("invalid observed native evaluation target mapping")
    if (observation["schema"] != "threadlight-deployment-observation/v1"
            or observation["target_id"] != plan[phase]["target_id"]
            or observation["environment"] != plan[phase]["environment"]
            or observation["source_sha"] != context["ci"]["source_sha"]
            or not isinstance(observation["image_digest"], str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", observation["image_digest"])
            or not isinstance(observation["version"], str) or not observation["version"]):
        raise ReleaseError("observed deployment does not match the approved source and target")
    _gate()._release_stamp(observation["observed_at"], datetime.now(timezone.utc),
                          started, plan["max_age_seconds"])
    return observation


def _gate():
    spec = importlib.util.spec_from_file_location(
        "release_canonical_gate", TOOL_ROOT / "threadlight-production-ready/scripts/evidence_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(root: Path, plan: dict, policy_path: str, receipt: Path) -> dict:
    context = {"schema": SCHEMA, "ci": ci_context(root), "policy_sha256": digest(plan),
               "inputs": inputs(root, plan, policy_path)}
    verify_identity(plan, "validation")
    receipt.unlink(missing_ok=True)
    started = datetime.now(timezone.utc)
    context["started_at"] = started.isoformat()
    context["target"] = plan["validation"]
    execute(root, plan["validation"]["prepare"], plan["timeout_seconds"], context, "prepare")
    context["candidate"] = observe(root, plan, "validation", context, started)
    accepted = {}
    for domain in ("evals", "redteam", "mcp"):
        config = plan.get(domain)
        output_paths = config["outputs"] if config else [MANIFESTS["mcp"]]
        for name in output_paths:
            path = local_path(root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink(missing_ok=True)
        run_start = datetime.now(timezone.utc)
        argv = config["producer"] if config else [
            sys.executable, str(TOOL_ROOT / "threadlight-production-ready/scripts/mcp_sbom.py"),
            "--root", ".", "--out", MANIFESTS["mcp"], "--check",
        ]
        execute(root, argv, plan["timeout_seconds"], context, domain)
        hashes = {name: file_digest(local_path(root, name)) for name in output_paths}
        report = load(local_path(root, MANIFESTS[domain]))
        criteria = config["acceptance"] if config else {}
        allowed = ({"required_capabilities", "min_pass_rate"} if domain == "evals"
                   else {"max_asr", "min_attacks"})
        if set(criteria) - allowed:
            raise ReleaseError("unsupported release acceptance override")
        summary = _gate().validate_release_manifest(
            domain, report, now=datetime.now(timezone.utc), not_before=run_start,
            max_age_seconds=plan["max_age_seconds"], **criteria)
        if config:
            raw_path = (report.get("metrics", {}).get("latest_run") if domain == "evals"
                        else report.get("scan_result"))
            if raw_path not in hashes or raw_path == MANIFESTS[domain]:
                raise ReleaseError("assessor does not reference this producer's raw run output")
            raw_run = load(local_path(root, raw_path))
            _gate()._release_stamp(raw_run.get("finished_at"), datetime.now(timezone.utc),
                                  run_start, plan["max_age_seconds"])
            if raw_run.get("release_binding") != {"ci": context["ci"], "candidate": context["candidate"]}:
                raise ReleaseError("raw evidence is not bound to this CI attempt and observed candidate")
        accepted[domain] = {"acceptance": summary, "outputs": hashes}
        if inputs(root, plan, policy_path) != context["inputs"]:
            raise ReleaseError("release inputs changed during validation")
    final = observe(root, plan, "validation", context, started)
    if any(final[key] != context["candidate"][key] for key in final if key != "observed_at"):
        raise ReleaseError("candidate changed while evidence was collected")
    context.pop("target")
    context.update(accepted=accepted, completed_at=datetime.now(timezone.utc).isoformat(),
                   status="validated-candidate", production=plan["production"]["target_id"])
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(context, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return context


def promote(root: Path, plan: dict, policy_path: str, receipt: Path, expected_sha256: str) -> dict:
    raw = receipt.read_bytes()
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256) or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ReleaseError("release receipt differs from the validation job's recorded output")
    context = parse_document(raw.decode("utf-8"))
    if (context.get("schema") != SCHEMA or context.get("status") != "validated-candidate"
            or context.get("ci") != ci_context(root) or context.get("policy_sha256") != digest(plan)
            or context.get("inputs") != inputs(root, plan, policy_path)
            or context.get("production") != plan["production"]["target_id"]
            or set(context.get("accepted", {})) != set(MANIFESTS)):
        raise ReleaseError("release receipt is not bound to this source, policy, target and CI attempt")
    _gate()._release_stamp(context.get("completed_at"), datetime.now(timezone.utc),
                          datetime.fromisoformat(context["started_at"]), plan["max_age_seconds"])
    for domain, result in context["accepted"].items():
        if result.get("acceptance", {}).get("domain") != domain or result["acceptance"].get("status") != "pass":
            raise ReleaseError("a required release domain did not pass")
    verify_identity(plan, "production")
    context["target"] = plan["production"]
    context["operation_id"] = digest({"ci": context["ci"], "candidate": context["candidate"],
                                      "production": context["production"]})
    state = root / ".threadlight-release-private" / f"promotion-{context['operation_id']}.json"
    state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with state.open("x", encoding="utf-8") as stream:
        json.dump({"state": "outcome-unknown", "operation_id": context["operation_id"]}, stream)
    started = datetime.now(timezone.utc)
    execute(root, plan["production"]["promote"], plan["timeout_seconds"], context, "promote")
    observed = observe(root, plan, "production", context, started)
    if observed["image_digest"] != context["candidate"]["image_digest"]:
        raise ReleaseError("production image differs from the validated candidate; stop and reconcile")
    result = {"schema": SCHEMA, "status": "production-observed", "ci": context["ci"],
              "operation_id": context["operation_id"],
              "deployment": observed, "candidate_receipt_sha256": expected_sha256}
    state.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", "validate", "promote"))
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--policy", default="specs/release-policy.json")
    parser.add_argument("--receipt", default=".threadlight-release/candidate.json")
    parser.add_argument("--receipt-sha256", help="validation job output, not a value read from the artifact")
    args = parser.parse_args(argv)
    try:
        root = args.repo.resolve()
        plan = policy(root, args.policy)
        if args.phase == "preflight":
            ci_context(root)
            inputs(root, plan, args.policy)
        else:
            receipt = local_path(root, args.receipt)
            if args.phase == "validate":
                result = validate(root, plan, args.policy, receipt)
                checksum = file_digest(receipt)
                if os.environ.get("GITHUB_OUTPUT"):
                    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
                        stream.write(f"receipt_sha256={checksum}\n")
                if os.environ.get("TF_BUILD", "").lower() == "true":
                    print(f"##vso[task.setvariable variable=receiptSha256;isOutput=true]{checksum}")
            else:
                if not args.receipt_sha256:
                    raise ReleaseError("promotion requires the validation job's receipt SHA-256")
                result = promote(root, plan, args.policy, receipt, args.receipt_sha256)
            print(json.dumps({"status": result["status"]}))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Release blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
