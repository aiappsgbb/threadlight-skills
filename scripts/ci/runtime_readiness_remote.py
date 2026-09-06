"""Protected resume of an already-created SDK version; never an azd deployment."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

MODE = "resume-signed-bootstrap/v1"


def require_created(creation, attempt):
    from govern_control_plane.hosted_lifecycle import creation_state
    state = creation_state(creation, attempt)
    if state["state"] != "created":
        raise ValueError("explicit_prior_sdk_creation_required")
    return state


def validate_selection(config, creation):
    from govern_control_plane.hosted_lifecycle import validate
    validate(creation)
    target, package = config["expected_target"], config["package"]
    expected_protocol = {
        "microsoft-agent-framework": "responses", "github-copilot-sdk": "invocations",
    }.get(config["deployment"]["infrastructure"]["runtime"])
    if (creation["tenant_id"] != target["tenant"] or creation["subscription"] != target["subscription"]
            or creation["resource_group"] != target["resource_group"]
            or creation["project_id"] != config["probe_input"]["selection"]["project_resource_id"]
            or creation["agent_name"] != package["agent_id"]
            or creation["reference"] != package["remote_bootstrap"]["reference"]
            or creation["project_endpoint"] != package["remote_bootstrap"]["project_endpoint"]
            or creation["image"] != config["deployment"]["images"]["agent"]
            or creation["protocol"] != expected_protocol):
        raise ValueError("remote_bootstrap_selection_mismatch")


def load(config, base):
    from scripts.ci import runtime_readiness as driver
    import importlib
    generator = importlib.import_module("skills.threadlight-deploy.references.governance.generate")
    remote = config["remote_bootstrap"]
    required = {"mode", "creation", "attempt", "publisher", "policy_bundle", "policy_envelope"}
    if (not isinstance(remote, dict) or remote.get("mode") != MODE
            or not required <= remote.keys() or set(remote) - required - {"native_assets"}):
        raise ValueError("explicit_remote_bootstrap_resume_inputs_required")
    result = {"mode": MODE}
    for name, item in remote.items():
        if name == "mode":
            continue
        directory = name in ("policy_bundle", "native_assets")
        digest_key = "tree_digest" if directory else "sha256"
        if not isinstance(item, dict) or set(item) != {"path", digest_key}:
            raise ValueError("content_pinned_remote_inputs_required")
        path = driver.checked_path(base, item["path"], directory=directory)
        if directory:
            if any(file.is_symlink() for file in path.rglob("*")):
                raise ValueError("symlinked_remote_input")
            actual = generator.tree_digest(path)
        else:
            if path.stat().st_size > 65536:
                raise ValueError("remote_input_too_large")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != item[digest_key]:
            raise ValueError("remote_input_digest_mismatch")
        result[name] = path
    creation = driver.read(result["creation"])
    validate_selection(config, creation)
    require_created(creation, result["attempt"])
    from govern_control_plane.app import AzureConfiguration
    from govern_control_plane.models import canonical, parse
    publisher = parse(AzureConfiguration, canonical(driver.read(result["publisher"])))
    if publisher.tenant_id != config["expected_target"]["tenant"] or publisher.key_id != config["package"]["key_id"]:
        raise ValueError("remote_publisher_scope_mismatch")
    native = creation["protocol"] == "responses"
    if native != ("native_assets" in result):
        raise ValueError("remote_native_probe_assets_required")
    if native:
        from govern_control_plane.bootstrap_assets import read_directory
        read_directory(result["native_assets"])
    result["creation_values"] = creation
    return result


def deploy(project, config):
    """Resume only after operator service preparation; collect separately afterwards."""
    import importlib
    from scripts.ci import runtime_readiness as driver
    from govern_control_plane.models import SignedBundle, canonical, parse
    from govern_control_plane.bootstrap import SignedBootstrap
    from govern_control_plane.bootstrap_assets import digest
    project = Path(project)
    started_at = driver.utc()
    remote = config["remote_bootstrap"]
    creation = remote["creation_values"]
    validate_selection(config, creation)
    state = require_created(creation, remote["attempt"])
    generator = importlib.import_module("skills.threadlight-deploy.references.governance.generate")
    package = driver.read(project / ".threadlight/governance-package.json")
    agent, frozen = generator.frozen_configuration(project, package)
    provenance = driver.read(project / "governance/source-provenance.json")
    if (generator.tree_digest(agent) != creation["source_digest"]
            or provenance["source_commit"] != creation["source_commit"]
            or creation["image"] != config["agent_image"]["agent_image"]
            or state["version"] != config["deployment"]["bindings"]["agent_version"]):
        raise ValueError("created_image_source_or_version_mismatch")
    driver.account_matches(config["expected_target"])
    cli = driver.ROOT / "scripts/ci/hosted_bootstrap.py"
    common = ["--creation", remote["creation"], "--attempt", remote["attempt"], "--credential-mode", "azure-cli"]
    observed_path = project / ".threadlight/remote-bootstrap-observation.json"
    driver.run([sys.executable, cli, "observe", *common, "--observation-output", observed_path])
    observed = driver.read(observed_path)
    bindings = config["deployment"]["bindings"]
    if (observed["agent_version"] != bindings["agent_version"]
            or observed["principal"] != bindings["agent_principal"]
            or observed["client_id"] != bindings["agent_client_id"]
            or observed["image_digest"] != creation["image"].split("@")[1]):
        raise ValueError("observed_agent_binding_changed")
    signed = parse(SignedBundle, canonical(driver.read(remote["policy_envelope"])))
    driver.generate("agent-image", project, config, config["agent_image"])
    deployment = dict(config["deployment"])
    if creation["protocol"] == "invocations":
        stage = {"gateway_bundle": str(remote["policy_bundle"]), "signed_envelope": str(remote["policy_envelope"]),
                 "policy_digest": signed.envelope.content_digest, "agent_image": creation["image"]}
        driver.generate("stage-gateway", project, config, stage)
        # Input must pin the independently built/deployed gateway source, not
        # pretend that staging itself built or deployed that service.
        if generator.tree_digest(project / "src/govern-gateway") != deployment["gateway_source_digest"]:
            raise ValueError("deployed_gateway_source_mismatch")
        deployment["gateway_bundle"] = str(remote["policy_bundle"])
    else:
        deployment.update(probe_bundle=str(remote["policy_bundle"]),
                          probe_signed_envelope=str(remote["native_assets"] / "envelope.json"),
                          probe_runtime_configuration=driver.read(remote["native_assets"] / "config.json"))
    driver.generate("bind", project, config, deployment)
    output = project / ".threadlight/hosted-bootstrap.json"
    driver.run([sys.executable, cli, "publish", *common,
        "--frozen-config", agent / "governance-config.json",
        "--policy-envelope", remote["policy_envelope"], "--publisher-config", remote["publisher"],
        "--binding-output", output, "--expected-observation", observed_path,
        *(["--native-probe-assets", remote["native_assets"]] if "native_assets" in remote else []),
        "--lifetime-seconds", "600"])
    driver.run([sys.executable, cli, "wait", *common, "--binding-output", output,
                "--frozen-config", agent / "governance-config.json", "--expected-observation", observed_path])
    binding = parse(SignedBootstrap, canonical(driver.read(output)))
    if binding.binding.config_digest != digest(canonical(frozen)):
        raise ValueError("published_frozen_configuration_mismatch")
    record_completed_attempt(project, binding, started_at=started_at)


def record_completed_attempt(project, binding, *, started_at):
    from scripts.ci import runtime_readiness as driver
    from govern_control_plane.bootstrap import SignedBootstrap
    from govern_control_plane.bootstrap_assets import digest
    from govern_control_plane.models import canonical, parse
    binding = parse(SignedBootstrap, canonical(binding))
    driver.write(Path(project) / driver.ATTEMPT, {
        "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "started_at": started_at, "completed_at": driver.utc(), "bootstrap_reference": binding.binding.reference,
        "bootstrap_sha256": digest(canonical(binding)),
    })
