#!/usr/bin/env python3
"""Materialize only real Task10 module copies; never deploy or invent credentials."""
import argparse
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

EXAMPLE = Path(__file__).resolve().parents[1]
CATALOG = EXAMPLE.parents[1]
GENERATOR = CATALOG / "skills/threadlight-deploy/references/governance/generate.py"


def materialize(output, configuration=None, *, probe=False):
    output = Path(output).absolute()
    if output.exists() or output.is_relative_to(EXAMPLE):
        raise ValueError("new_output_directory_outside_example_required")
    sys.path.insert(0, str(CATALOG))
    sys.path.insert(0, str(EXAMPLE / "src/agent"))
    settings = None
    if configuration:
        from deployment_config import DeploymentConfiguration
        settings = DeploymentConfiguration.model_validate_json(Path(configuration).read_bytes())
        if probe and (settings.environment != "preproduction"
                      or settings.subscription is None or settings.resource_group is None):
            raise ValueError("probe_requires_explicit_preproduction_deployment_scope")
    output.mkdir(parents=True)
    try:
        agent = output / "src/agent"
        shutil.copytree(EXAMPLE / "src/agent", agent,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(EXAMPLE / "specs/sample-data", agent / "sample-data")
        for name in ("agent.yaml", "azure.yaml"):
            shutil.copyfile(EXAMPLE / name, output / name)
        for name in ("AGENTS.md", "README.md"):
            shutil.copyfile(EXAMPLE / name, output / name)
        shutil.copytree(EXAMPLE / "specs", output / "specs")
        shutil.copytree(EXAMPLE / "governance", output / "governance")
        (output / "scripts").mkdir()
        shutil.copyfile(EXAMPLE / "scripts/local_probe.py", output / "scripts/local_probe.py")
        definition = yaml.safe_load((EXAMPLE / "agent.yaml").read_text())
        contract = {key: definition[key] for key in ("framework", "governance", "tools")}
        source_config = {"agent_service": "returns-triage"}
        if probe:
            contract["tools"].append({
                "id": "governance_probe_noop", "consequence": "read",
                "policy_binding": "returns-write-v1", "enforcement_path": "local-agent-hooks",
                "intervention_points": ["pre_tool_call"], "safe_principles": ["Scope"],
                "requires": ["durable-audit"]})
            source_config.update(environment="preproduction", probe_observability={
                "enabled": True, "configuration_file": "/mnt/governance-probe/config.json"})
            (output / "agent.yaml").write_text(yaml.safe_dump({**definition, "tools": contract["tools"]},
                                                             sort_keys=False))
        (output / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
        config_path = output / "source-configuration.json"
        config_path.write_text(json.dumps(source_config))
        subprocess.run([sys.executable, str(GENERATOR), "package-native", "--project", str(output),
                        "--contract", str(output / "contract.json"), "--configuration", str(config_path)],
                       check=True)
        bundle_api = importlib.import_module("skills.threadlight-govern.scripts.policy_bundle")
        bundle = bundle_api.build_bundle(
            source=agent / "governance/policy", destination=agent / "policy",
            policy_id="returns-write-v1", version="1")
        generator = importlib.import_module("skills.threadlight-deploy.references.governance.generate")
        generator.export_local_validation(output)
        if settings:
            from govern_control_plane.models import SignedBundle, parse
            generator = importlib.import_module("skills.threadlight-deploy.references.governance.generate")
            raw = Path(settings.signed_envelope).read_bytes()
            signed = parse(SignedBundle, raw)
            config = settings.model_dump()
            if settings.policy_digest != bundle.bundle_digest:
                raise ValueError("configuration_policy_digest_mismatch")
            generator.validate_policy(bundle, signed, config)
            generator.validate_bundle_contract(bundle, contract, config)
            config.update(contract=contract, audit_delivery="remote-ack")
            if probe:
                config["probe_observability"] = source_config["probe_observability"]
            (agent / "governance-config.json").write_text(json.dumps(config, indent=2) + "\n")
            (agent / "policy-envelope.json").write_bytes(raw)
        result = {"status": "source-packaged-unverified", "binding_status": "unverified",
                  "policy_digest": bundle.bundle_digest,
                  "signature": "runtime-verification-required" if settings else "not-configured",
                  "probe": "declared-unverified" if probe else "not-configured",
                  "images": "not-built", "deployment": "not-bound"}
        (output / "source-package.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    except BaseException:
        shutil.rmtree(output)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--probe", action="store_true", help="explicit preproduction reserved noop producer")
    args = parser.parse_args()
    print(json.dumps(materialize(args.output, args.configuration, probe=args.probe)))
