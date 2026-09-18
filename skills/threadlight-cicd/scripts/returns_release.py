#!/usr/bin/env python3
"""Selected returns/MCP application adapters for release_runner, not a release engine."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import release_runner

PROFILE = "returns-mcp/v1"
ACTORS = {"agent", "gateway", "backend", "control_plane", "human_reviewer"}
TOOLS = {"returns_get_case": "none", "returns_apply_decision": "returns-safe"}
POSTCHECKS = {"native_readiness", "signed_binding", "policy_runtime", "tool_inventory",
              "role_map", "cosmos_target", "outlook_authority", "central_audit"}
REFERENCE = "skills/threadlight-deploy/references/governance/"
SOURCE_FILES = {REFERENCE + name for name in (
    "returns_mcp_agent.py", "returns_mcp_backend.py", "package_returns_mcp.py", "returns-mcp-demo.md")}
OPS = {
    "validation": {"prepare", "observe"},
    "production": {"stop", "admission", "operation", "promote", "observe", "postcheck", "admit", "recover"},
}


def require(condition, message):
    if not condition:
        raise release_runner.ReleaseError(message)


def sha(value, prefix=""):
    return isinstance(value, str) and re.fullmatch(re.escape(prefix) + r"[a-f0-9]{64}", value)


def text(value):
    return (isinstance(value, str) and bool(value.strip()) and value == value.strip()
            and "REPLACE" not in value and "<" not in value
            and not any(ord(character) < 32 for character in value))


def check_behavior(value):
    require(isinstance(value, dict) and set(value) == {
        "source_package_sha256", "instructions_sha256", "model", "policy_code_sha256", "tools",
    }, "complete selected returns behavior required")
    require(all(sha(value[key]) for key in (
        "source_package_sha256", "instructions_sha256", "policy_code_sha256")), "behavior hashes required")
    model = value["model"]
    require(isinstance(model, dict) and set(model) == {"name", "version"}
            and all(text(item) for item in model.values())
            and model["version"].lower() != "latest", "explicit model name/version required")
    tools = value["tools"]
    require(isinstance(tools, dict) and set(tools) == set(TOOLS), "exact two-tool returns inventory required")
    for name, binding in TOOLS.items():
        tool = tools[name]
        require(isinstance(tool, dict) and set(tool) == {"policy_binding", "schema_sha256"}
                and tool["policy_binding"] == binding and sha(tool["schema_sha256"]),
                "selected write binding and unbound read must remain distinct")


def check_external(value):
    require(isinstance(value, dict) and set(value) == {
        "signing_key_id", "model_deployment", "connections", "services", "cosmos", "outlook", "role_map",
    }, "complete external application contract required")
    require(text(value["signing_key_id"]), "phase-approved versioned signing key required")
    key = urlsplit(value["signing_key_id"])
    require(key.scheme == "https" and key.hostname and not key.username and not key.password
            and not key.query and not key.fragment and re.fullmatch(r"/keys/[^/]+/[^/]+", key.path),
            "phase-approved versioned signing key required")
    model = value["model_deployment"]
    require(isinstance(model, dict) and set(model) == {"resource_id", "revision"}
            and all(text(item) for item in model.values()), "observed model deployment revision required")
    for name, keys in (("connections", {"model", "gateway", "outlook"}),
                       ("services", {"control_plane", "gateway", "backend"})):
        entries = value[name]
        require(isinstance(entries, dict) and set(entries) == keys, f"complete {name} required")
        for item in entries.values():
            expected = {"resource_id", "revision"} | ({"image_digest"} if name == "services" else set())
            require(isinstance(item, dict) and set(item) == expected
                    and all(text(v) for v in item.values()), f"observed {name} revision required")
            if name == "services":
                require(sha(item["image_digest"], "sha256:"), "immutable service image required")
    cosmos = value["cosmos"]
    require(isinstance(cosmos, dict) and set(cosmos) == {"account_id", "database", "containers"}
            and text(cosmos["account_id"]) and text(cosmos["database"]), "explicit backend Cosmos target required")
    containers = cosmos["containers"]
    require(isinstance(containers, dict) and set(containers) == {
        "cases", "read_audit", "central_audit", "operations",
    } and all(text(v) for v in containers.values()) and len(set(containers.values())) == 4,
            "distinct decision/read/central-audit/operation containers required")
    outlook = value["outlook"]
    require(isinstance(outlook, dict) and set(outlook) == {
        "workflow_id", "version", "definition_sha256", "connection_id", "recipient_sha256",
    } and all(text(v) for v in outlook.values())
            and sha(outlook["definition_sha256"]) and sha(outlook["recipient_sha256"]),
            "native Outlook workflow/connection/recipient binding required")
    roles = value["role_map"]
    require(isinstance(roles, dict) and set(roles) == ACTORS, "complete role map required")
    for actor, grants in roles.items():
        require(isinstance(grants, list) and grants and all(
            isinstance(grant, dict) and set(grant) == {"scope", "role"}
            and all(text(item) for item in grant.values()) for grant in grants),
            "explicit scoped role map required")
        require(len({release_runner.digest(grant) for grant in grants}) == len(grants),
                "duplicate role-map entries")
        if actor == "agent":
            require(all(grant["scope"] != cosmos["account_id"] and "/keys/" not in grant["scope"]
                        for grant in grants), "agent must not hold backend Cosmos or signing-key authority")


def configuration(root, path):
    config = release_runner.load(release_runner.local_path(root, path))
    require(set(config) == {"schema", "source_package", "behavior", "targets", "producers", "owners"}
            and config["schema"] == "threadlight-returns-release/v1", "invalid returns release configuration")
    check_behavior(config["behavior"])
    owners = config["owners"]
    require(isinstance(owners, dict) and set(owners) == {
        "application", "central_platform", "production_approver", "human_reviewer", "incident",
    } and all(text(item) for item in owners.values()), "explicit release/integration/recovery owners required")
    require(isinstance(config["targets"], dict) and set(config["targets"]) == set(OPS),
            "distinct validation and production targets required")
    for phase, names in OPS.items():
        target = config["targets"][phase]
        require(isinstance(target, dict) and set(target) == {"external", "operators"}
                and isinstance(target["operators"], dict) and set(target["operators"]) == names,
                f"real {phase} operator entrypoints required")
        check_external(target["external"])
        for argv in target["operators"].values():
            release_runner.adapter_inputs(root, argv)
    before, after = [config["targets"][phase]["external"] for phase in OPS]
    require(before["cosmos"]["account_id"] != after["cosmos"]["account_id"]
            and before["outlook"]["workflow_id"] != after["outlook"]["workflow_id"]
            and all(before["services"][name]["resource_id"] != after["services"][name]["resource_id"]
                    for name in before["services"]),
            "validation must not use production business, governance or human-approval targets")
    require(isinstance(config["producers"], dict) and set(config["producers"]) == {"evals", "redteam"},
            "real evaluation and red-team producers required")
    for domain, producer in config["producers"].items():
        require(isinstance(producer, dict) and set(producer) == {"execute", "raw_output"},
                "producer executable and raw output required")
        release_runner.adapter_inputs(root, producer["execute"])
        release_runner.local_path(root, producer["raw_output"])
    package = release_runner.local_path(root, config["source_package"])
    require(release_runner.file_digest(package) == config["behavior"]["source_package_sha256"],
            "returns source package hash mismatch")
    manifest = release_runner.load(package)
    require(manifest.get("schema") == "threadlight-returns-mcp-source/v1"
            and isinstance(manifest.get("files"), dict) and SOURCE_FILES <= set(manifest["files"]),
            "use the existing package_returns_mcp.py source closure")
    for name, expected in manifest["files"].items():
        require(sha(expected) and release_runner.file_digest(
            release_runner.local_path(package.parent, name)) == expected, "source package member mismatch")
    return config


def input_paths(root, path, config):
    paths = {path, config["source_package"]}
    package = Path(config["source_package"]).parent
    manifest = release_runner.load(root / config["source_package"])
    paths.update((package / name).as_posix() for name in manifest["files"])
    commands = [argv for target in config["targets"].values() for argv in target["operators"].values()]
    commands += [producer["execute"] for producer in config["producers"].values()]
    for argv in commands:
        paths.update(release_runner.adapter_inputs(root, argv))
        paths.update(item for item in argv if not Path(item).is_absolute()
                     and not item.startswith("-") and (root / item).is_file())
    return paths


def check_observation(config, phase, observation):
    contract = observation.get("application_contract")
    require(isinstance(contract, dict) and set(contract) == {
        "schema", "behavior", "external", "binding", "identities",
    } and contract["schema"] == "threadlight-returns-application/v1", "observed application contract required")
    check_behavior(contract["behavior"])
    check_external(contract["external"])
    require(contract["behavior"] == config["behavior"]
            and contract["external"] == config["targets"][phase]["external"],
            "observed model/policy/tools/connections/roles/Outlook/Cosmos contract drift")
    binding = contract["binding"]
    require(isinstance(binding, dict) and set(binding) == {
        "agent_id", "agent_version", "image_digest", "environment", "envelope_sha256",
        "policy_digest", "config_digest", "key_id", "principal", "client_id",
    }, "current signed binding observation required")
    require(binding["agent_id"] == observation["target_id"].rstrip("/").rsplit("/", 1)[-1]
            and all(binding[key] == observation[other] for key, other in (
        ("agent_version", "version"), ("image_digest", "image_digest"),
        ("environment", "environment"))), "binding does not match observed deployment")
    require(sha(binding["envelope_sha256"]) and sha(binding["policy_digest"], "sha256:")
            and sha(binding["config_digest"], "sha256:"), "binding content hashes required")
    key = urlsplit(binding["key_id"])
    require(binding["key_id"] == contract["external"]["signing_key_id"]
            and key.scheme == "https" and key.hostname and not key.username and not key.password
            and not key.query and not key.fragment and re.fullmatch(r"/keys/[^/]+/[^/]+", key.path),
            "full versioned signing key required")
    identities = contract["identities"]
    require(isinstance(identities, dict) and set(identities) == ACTORS
            and all(text(item) for item in identities.values())
            and len(set(identities.values())) == len(ACTORS)
            and identities["agent"] == binding["principal"] and text(binding["client_id"]),
            "independently observed separate agent/gateway/backend/control/human identities required")


def closed(context, call):
    result = call("admission", context)
    require(result.get("target_id") == context["target"]["target_id"]
            and result.get("state") == "closed", "production admission is not independently observed closed")


def promote(context, call):
    call("stop", context)
    closed(context, call)
    operation = call("operation", context)
    require(operation.get("operation_id") == context["operation_id"],
            "durable promotion operation mismatch; stop and reconcile")
    state = operation.get("state")
    require(state in {"absent", "prepared"}, "promotion outcome unknown; stop and reconcile before retry")
    if state == "absent":
        operation = call("promote", context)
    require(operation.get("operation_id") == context["operation_id"]
            and operation.get("state") == "prepared"
            and operation.get("image_digest") == context["candidate"]["image_digest"],
            "immutable promotion not prepared; stop and reconcile")
    closed(context, call)
    return operation


def check_postcheck(result, context):
    require(result.get("operation_id") == context["operation_id"]
            and result.get("target_id") == context["target"]["target_id"]
            and result.get("application_contract_sha256") == release_runner.digest(
                context["production_observation"]["application_contract"]),
            "postchecks must bind this operation and independently observed application contract")
    started = release_runner._gate()._parse_rfc3339_datetime(context.get("postcheck_started_at"))
    require(started is not None, "current postcheck execution window required")
    release_runner._gate()._release_stamp(result.get("observed_at"), datetime.now(timezone.utc),
                                         started, 86400)
    checks = result.get("checks")
    require(isinstance(checks, dict) and set(checks) == POSTCHECKS and all(
        isinstance(item, dict) and set(item) == {"status", "evidence_sha256"}
        and item["status"] == "pass" and sha(item["evidence_sha256"]) for item in checks.values()),
        "all real postdeployment checks and retained evidence required before admission")


def check_run(raw, request):
    require(text(raw.get("provider")) and text(raw.get("run_id")),
            "actual evaluator/scanner provider and run ID required")
    require(raw.get("release_binding") == {"ci": request["ci"], "candidate": request["candidate"]},
            "producer must execute against this observed candidate and CI attempt")
    start = release_runner._gate()._parse_rfc3339_datetime(raw.get("started_at"))
    end = release_runner._gate()._parse_rfc3339_datetime(raw.get("finished_at"))
    invoked = release_runner._gate()._parse_rfc3339_datetime(request["producer_started_at"])
    require(start is not None and end is not None and invoked is not None and invoked <= start <= end
            <= datetime.now(timezone.utc), "old, future or unexecuted producer output")


def operator(root, config, phase, context, plan):
    def call(name, request):
        def authorize():
            release_runner.verify_identity(plan, phase)
            require(release_runner.policy(root, request["policy_path"]) == plan
                    and release_runner.ci_context(root) == request["ci"]
                    and release_runner.inputs(root, plan, request["policy_path"]) == request["inputs"],
                    "operator authorization changed after credential/transport wait")
            started = release_runner._gate()._parse_rfc3339_datetime(request.get("started_at"))
            require(started is not None, "current operator authorization window required")
            for stamp in (request["started_at"], request.get("completed_at", request["started_at"])):
                release_runner._gate()._release_stamp(stamp, datetime.now(timezone.utc),
                                                     started, plan["max_age_seconds"])
        # Keep nested stdout/stderr separate from the outer adapter capture.
        result = release_runner.execute(
            root, config["targets"][phase]["operators"][name], plan["timeout_seconds"],
            request, f"returns-{phase}-{name}", authorize=authorize)
        return release_runner.parse_document(result)
    return call


def produce(root, config, domain, request, plan):
    producer = config["producers"][domain]
    raw_path = release_runner.local_path(root, producer["raw_output"])
    require(producer["raw_output"] in plan[domain]["outputs"], "declare real raw output in release policy")
    raw_path.unlink(missing_ok=True)
    request = dict(request, producer_started_at=datetime.now(timezone.utc).isoformat())
    release_runner.execute(root, producer["execute"], plan["timeout_seconds"],
                           request, f"returns-{domain}-native")
    raw = release_runner.load(raw_path)
    check_run(raw, request)
    assessor = release_runner.TOOL_ROOT / f"threadlight-{domain}/scripts/{domain}_check.py"
    argv = [sys.executable, str(assessor)]
    if domain == "evals":
        argv += ["--target", str(root), "--emit"]
    else:
        argv += ["--target", str(root), "--scan-result", str(raw_path), "--emit"]
    release_runner.execute(root, argv, plan["timeout_seconds"], request, f"returns-{domain}-assess")
    report = release_runner.load(root / release_runner.MANIFESTS[domain])
    selected = report.get("metrics", {}).get("latest_run") if domain == "evals" else report.get("scan_result")
    require(selected == producer["raw_output"], "canonical assessor must reference the executed raw run")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "observe", "promote", "evals", "redteam"))
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--policy", default="specs/release-policy.json")
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        config = configuration(root, args.configuration)
        plan = release_runner.policy(root, args.policy)
        request = release_runner.load(Path(os.environ["THREADLIGHT_RELEASE_REQUEST"]))
        require(request.get("ci") == release_runner.ci_context(root)
                and request.get("policy_sha256") == release_runner.digest(plan),
                "the current release runner request is required")
        phase = next((phase for phase in OPS if request.get("target") == plan[phase]), None)
        require(phase is not None, "request does not select an approved phase")
        release_runner.verify_identity(plan, phase)
        require(release_runner.inputs(root, plan, args.policy) == request["inputs"],
                "application inputs changed after credential wait")
        call = operator(root, config, phase, request, plan)
        if args.action in ("evals", "redteam"):
            require(phase == "validation", "producers must not execute business tests in production")
            produce(root, config, args.action, request, plan)
        elif args.action == "observe":
            result = call("observe", request)
            check_observation(config, phase, result)
            print(json.dumps(result, allow_nan=False))
        elif args.action == "prepare":
            require(phase == "validation", "prepare cannot modify production")
            call("prepare", request)
        else:
            require(phase == "production", "promotion requires production authority")
            promote(request, call)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Returns release blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
