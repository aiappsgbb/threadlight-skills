#!/usr/bin/env python3
"""Protected preproduction workflow driver; no synthetic or legacy success path.

Inputs are operator-reviewed source/configuration, signed envelopes, registered
identities and already published immutable images. Missing bootstrap/fixture
infrastructure is a blocker, not permission to create broad role assignments.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import re

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.ci import readiness_evidence

GENERATOR = ROOT / "skills/threadlight-deploy/references/governance/generate.py"
ATTEMPT = ".threadlight/readiness-attempt.json"
PROTECTED_PATTERNS = (
    ".threadlight/", ".azure/", ".governance-validation/", ".env", ".env.*",
    "__pycache__/", "*.pyc", "*.pem", "*.key", "*token*.json", "*token*.txt",
    "*secret*.json", "*secret*.txt", "*credential*.json", "*credential*.txt",
    "governance-config.json", "policy-envelope.json", "*configuration.json",
    "config.json", ".netrc", ".npmrc", ".pypirc", "token", "tokens", "secret",
    "secrets", "credentials", "*token*.yaml", "*token*.yml",
    "*secret*.yaml", "*secret*.yml", "*credential*.yaml", "*credential*.yml",
    "/infra/main.parameters.json",
    "/specs/governance-manifest.json", "/tests/*manifest.json",
    "/tests/governed-actions-apply-plan.json", "/docs/governance/evidence-pack.md",
)


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    path.chmod(0o600)


def utc():
    return datetime.now(timezone.utc).isoformat()


def checked_path(base, value, *, directory=False):
    if not isinstance(value, str) or not value:
        raise ValueError("protected configuration: concrete input path required")
    path = base / value
    if (path.is_symlink() or not path.resolve().is_relative_to(base.resolve())
            or any(p.is_symlink() for p in path.parents if p != base and base in p.parents)):
        raise ValueError("protected configuration: input path escapes protected directory")
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("protected configuration: required input file/directory missing")
    return path.resolve()


def validate_supplemental_destinations(config):
    paths = [readiness_evidence.supplemental_path(p) for p in config["probe_files"]]
    if any(readiness_evidence.overlaps(a, b)
           for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError("supplemental paths overlap")


def load_inputs(path):
    try:
        path = Path(path).resolve()
        config = read(path)
        required = {"schema", "environment", "expected_target", "source_project",
                    "policy_source", "package", "agent_image", "deployment",
                    "probe_input", "probe_files", "source_digests",
                    "azd_environment", "location"}
        if not isinstance(config, dict) or not required <= config.keys():
            raise ValueError("required fields")
        if config["schema"] != "threadlight-readiness-input/v1" or config["environment"] != "preproduction":
            raise ValueError("explicit preproduction only")
        from skills._shared.probe_evidence import require_selected_target
        target = config["expected_target"]
        if set(target) != {"tenant", "subscription", "resource_group"}:
            raise ValueError("independent expected parent required")
        require_selected_target(target, target)
        # UUIDs are required, not mutable account aliases or personal defaults.
        from uuid import UUID
        for key in ("tenant", "subscription"):
            if str(UUID(target[key])) != target[key]:
                raise ValueError("canonical scope required")
        if not target["resource_group"] or not config["azd_environment"] or not config["location"]:
            raise ValueError("explicit deployment selectors required")
        for key in ("package", "agent_image", "deployment", "probe_input", "probe_files", "source_digests"):
            if not isinstance(config[key], dict) or not config[key]:
                raise ValueError("concrete configuration required")
        base = path.parent
        config["source_project"] = checked_path(base, config["source_project"], directory=True)
        config["policy_source"] = checked_path(base, config["policy_source"], directory=True)
        config["package"] = dict(config["package"])
        config["package"]["signed_envelope"] = str(checked_path(base, config["package"]["signed_envelope"]))
        config["input_base"] = base
        config["input_digest"] = hashlib.sha256(path.read_bytes()).hexdigest()
        source = config["source_project"]
        from skills._shared.governance_selection import load_contract
        from skills._shared.governance_readiness import selected
        document = load_contract(source)
        if not selected(document):
            raise ValueError("selected bindings required; off is not a readiness proof")
        if config["package"].get("environment") != "preproduction":
            raise ValueError("package environment mismatch")
        if config["package"].get("probe_observability") != {
                "enabled": True, "configuration_file": "/mnt/governance-probe/config.json"}:
            raise ValueError("explicit registered probe configuration required")
        if config["package"].get("tenant_id") != target["tenant"]:
            raise ValueError("tenant mismatch")
        deployment = config["deployment"]
        if deployment["infrastructure"]["environment"] != "preproduction":
            raise ValueError("infrastructure environment mismatch")
        for key in ("images", "bindings", "observations"):
            if not deployment.get(key):
                raise ValueError("registered services and observed immutable images required")
        if config["probe_input"]["selection"]["subscription"] != target["subscription"]:
            raise ValueError("probe subscription mismatch")
        if config["probe_input"]["selection"]["resource_group"] != target["resource_group"]:
            raise ValueError("probe resource group mismatch")
        # Every protected supplemental input is content-pinned before copying.
        validate_supplemental_destinations(config)
        for destination, item in config["probe_files"].items():
            if Path(destination).is_absolute() or ".." in Path(destination).parts:
                raise ValueError("unsafe supplemental path")
            file = checked_path(base, item["path"])
            if hashlib.sha256(file.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("protected input digest mismatch")
        return config
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        raise ValueError("protected configuration invalid/incomplete; supply actual signed policy, "
                         "selected contract, registered fixture/services, scope, identities and images") from error


def run(args, *, cwd=None, env=None):
    return subprocess.run([str(arg) for arg in args], cwd=cwd or ROOT,
                          env=env, check=True)


def generate(command, project, config, configuration):
    input_file = project / f".threadlight/ci-{command}.json"
    write(input_file, configuration)
    run([sys.executable, GENERATOR, command, "--project", project,
         "--contract", project / "specs/governance-contract.json",
         "--configuration", input_file])


def local_git(project, *args, returncodes=(0,)):
    # No prompts, global configuration writes, hooks, signing, or remote traffic.
    env = {k: v for k, v in os.environ.items()
           if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"}}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(
        ["git", *(["--literal-pathspecs"] if args[0] == "add" else []),
         "-C", str(project), "-c", "core.hooksPath=/dev/null",
         "-c", "commit.gpgsign=false", "-c", "user.name=Threadlight local staging",
         "-c", "user.email=staging@localhost", *args],
        env=env, capture_output=True, text=True)
    if result.returncode not in returncodes:
        raise ValueError("local source snapshot Git operation failed")
    return result.stdout.strip()


def source_identity(source, digest):
    root = Path(local_git(source, "rev-parse", "--show-toplevel")).resolve()
    commit = local_git(source, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("checked-out source commit required")
    return root, {
        "scope": "local-generated-snapshot-not-deployment",
        "source_commit": commit, "source_project": source.resolve().relative_to(root).as_posix(),
        "source_dirty": bool(local_git(source, "status", "--porcelain", "--untracked-files=no")),
        "source_digest": digest,
    }, set(local_git(source, "ls-files", "-z").split("\0")) - {""}


def snapshot_generated_sources(project, source_root, provenance, approved, config):
    """Commit an explicit source allowlist, never protected runtime inputs.

    The local commit identifies the generated files, not the input commit and not
    a deployed image. The real source checkout is a local origin, with no fabricated
    GitHub protection/remote state. Supplemental inputs are excluded regardless of
    their filename, and all non-allowlisted files remain untracked.
    """
    import yaml
    agent = Path(yaml.safe_load((project / "azure.yaml").read_text())[
        "services"][config["package"]["agent_service"]]["project"])
    approved = set(approved)
    approved.update({".gitignore", "azure.yaml", "agent.yaml", ".github/CODEOWNERS",
                     ".github/workflows/native-local.yml", "governance/change-plane.json",
                     "governance/source-provenance.json", "infra/main.bicep",
                     "infra/governance.bicep", "infra/registry-pull.bicep"})
    # Exact namespaces owned by the generator, not a recursive `git add .`.
    namespaces = ("runtime", "govern_control_plane", "govern_bundle", "govern_canonical",
                  "govern_shared", "skills/_shared", "vendor")
    for base in (agent, Path("src/govern-control-plane"), Path("src/govern-gateway")):
        for name in ("container.py", "governance_host.py", "audit_delivery.py", "service_entry.py",
                     "pyproject.toml", "Dockerfile", ".dockerignore", "skills/__init__.py"):
            approved.add((base / name).as_posix())
        for namespace in namespaces:
            approved.update(p.relative_to(project).as_posix()
                            for p in (project / base / namespace).rglob("*")
                            if p.is_file() and p.suffix in {".py", ".toml", ".json"})
    # Bundled policy is reviewed source; configuration/envelopes are not.
    for base in (agent / "policy", Path("src/govern-gateway/policy")):
        approved.update(p.relative_to(project).as_posix() for p in (project / base).rglob("*")
                        if p.is_file() and p.suffix in {".rego", ".yaml", ".json"})
    exported = read(project / ".governance-tools/source-manifest.json")["files"]
    approved.update(".governance-tools/" + p for p in exported)
    approved.add(".governance-tools/source-manifest.json")
    write(project / "governance/source-provenance.json", provenance)
    extra = set(config["probe_files"])
    if any("\n" in p or "\r" in p or any(c in p for c in "*?[\\") for p in extra):
        raise ValueError("literal supplemental paths required")
    with (project / ".gitignore").open("a") as ignored:
        ignored.write("\n# Protected runtime inputs and local evidence are never source.\n")
        ignored.write("\n".join(PROTECTED_PATTERNS) + "\n")
        ignored.write("".join("/" + p + "\n" for p in sorted(extra)))
    local_git(project, "init", "--quiet", "--template=")
    local_git(project, "remote", "add", "origin", source_root.as_uri())
    candidates = sorted(p for p in approved - extra
                        if (project / p).is_file() and not (project / p).is_symlink())
    # Git applies both the preserved operator ignores and our protected paths.
    # No --force: ignored inputs can never enter this snapshot.
    for start in range(0, len(candidates), 100):
        batch = candidates[start:start + 100]
        ignored = local_git(project, "check-ignore", "--", *batch, returncodes=(0, 1))
        selected = sorted(set(batch) - set(ignored.splitlines()))
        if selected:
            local_git(project, "add", "--", *selected)
    required = {p.relative_to(project).as_posix() for p in (project / agent).rglob("*")
                if p.is_file() and p.suffix in {".py", ".rego", ".md"}}
    tracked = set(local_git(project, "ls-files", "-z").split("\0"))
    if not required <= tracked:
        raise ValueError("served source is ignored or outside the approved source allowlist")
    local_git(project, "commit", "--quiet", "-m",
              "Local generated source snapshot (not deployment)\n\n"
              f"Source-Commit: {provenance['source_commit']}")


def prepare(project, config):
    validate_supplemental_destinations(config)
    if project.exists():
        raise ValueError("fresh project directory required; no reuse of previous deployment proof")
    generator = importlib.import_module("skills.threadlight-deploy.references.governance.generate")
    expected_sources = config["source_digests"]
    for key in ("source_project", "policy_source"):
        if generator.tree_digest(config[key]) != expected_sources.get(key):
            raise ValueError("protected source digest mismatch")
        if any(p.is_symlink() for p in config[key].rglob("*")):
            raise ValueError("symlinked source input refused")
    source_root, provenance, approved = source_identity(
        config["source_project"], expected_sources["source_project"])
    shutil.copytree(config["source_project"], project,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache",
                                                 ".governance-validation", ".governance-tools"))
    # Evidence may be archived, never carried forward as current runtime proof.
    for relative in {p for paths in readiness_evidence.OUTPUTS.values() for p in paths}:
        (project / relative).unlink(missing_ok=True)
    bundle_api = importlib.import_module("skills.threadlight-govern.scripts.policy_bundle")
    destination = project / ".threadlight/ci-policy"
    destination.parent.mkdir(parents=True, exist_ok=True)
    package = dict(config["package"])
    bundle = bundle_api.build_bundle(source=config["policy_source"], destination=destination,
                                     policy_id=package["policy_id"], version=package["policy_version"])
    bundle_api.verify_bundle(bundle.root, expected_digest=package["policy_digest"])
    bundle_api.validate_native_manifest(bundle.root)
    package["bundle_path"] = str(bundle.root)
    generate("foundation", project, config, config["deployment"]["infrastructure"])
    generate("generate", project, config, package)
    # Copy the complete real local producer/CTK closure, never invented receipts.
    generator.export_local_validation(project)
    snapshot_generated_sources(project, source_root, provenance, approved, config)
    for destination, item in config["probe_files"].items():
        output = readiness_evidence.contained(project, destination)
        if output.is_symlink() or not output.resolve().is_relative_to(project.resolve()):
            raise ValueError("supplemental input escapes project")
        # Never overwrite generated code, frozen settings or policy metadata.
        if output.exists():
            if output.read_bytes() != checked_path(config["input_base"], item["path"]).read_bytes():
                raise ValueError("supplemental input conflicts with generated source")
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checked_path(config["input_base"], item["path"]), output)
            output.chmod(0o600)
    write(project / ".threadlight/governance-probe.json", config["probe_input"])
    write(project / ".threadlight/ci-input.json", {"digest": config["input_digest"]})


def predeploy(project, config):
    run([sys.executable, ROOT / "skills/threadlight-govern/scripts/govern_check.py",
         "--target", project, "--bundle", ".threadlight/ci-policy", "--emit"])
    # These exported commands build CTK/test-oracle sources and inspect actual
    # installed execution bytes. Local code proof is separate from hosted proof.
    tools = project / ".governance-tools"
    run([sys.executable, tools / "scripts/ci/run-governance-pin-tests.py"])
    run([sys.executable, tools / "scripts/ci/run-governance-pin-tests.py", "--prepare-local"])
    run([sys.executable, tools / "skills/threadlight-governed-actions/scripts/governed_actions.py",
         "--target", project, "--phase", "pre-deploy", "--emit", "--gate"])


def account_matches(target):
    result = subprocess.run(["az", "account", "show", "--subscription", target["subscription"], "-o", "json"],
                            check=True, capture_output=True, text=True)
    account = json.loads(result.stdout)
    if account.get("tenantId") != target["tenant"] or account.get("id") != target["subscription"]:
        raise ValueError("observed Azure parent does not match protected tenant/subscription")


def deploy(project, config):
    target = config["expected_target"]
    account_matches(target)
    generate("agent-image", project, config, config["agent_image"])
    if config.get("gateway_stage"):
        generate("stage-gateway", project, config, config["gateway_stage"])
    generator = importlib.import_module("skills.threadlight-deploy.references.governance.generate")
    # Images are already built/published by the approved build/bootstrap identity.
    # Binding cannot silently point them at newly edited source.
    agent, _ = generator.frozen_configuration(project, read(project / ".threadlight/governance-package.json"))
    for service, source in (("agent", agent), ("govern-control-plane", project / "src/govern-control-plane"),
                            ("govern-gateway", project / "src/govern-gateway")):
        if not source.is_dir() or generator.tree_digest(source) != config["source_digests"].get(service):
            raise ValueError("published image/source provenance missing or changed")
    generate("bind", project, config, config["deployment"])
    for relative in ("specs/governance-manifest.json", ".threadlight/governance-live.json",
                     "tests/postdeploy-manifest.json"):
        (project / relative).unlink(missing_ok=True)
    attempt = {"run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
               "started_at": utc(), "completed_at": None}
    write(project / ATTEMPT, attempt)
    env = {**os.environ, "AZURE_TENANT_ID": target["tenant"],
           "AZURE_SUBSCRIPTION_ID": target["subscription"],
           "AZURE_RESOURCE_GROUP": target["resource_group"], "AZURE_LOCATION": config["location"],
           "AZURE_ENV_NAME": config["azd_environment"]}
    run(["azd", "env", "new", config["azd_environment"], "--no-prompt"], cwd=project, env=env)
    # Bound ACA service phase deploys real pinned CP/gateway revisions via Bicep;
    # azd deploy is only for the generated, immutable agent definition.
    run(["azd", "provision", "--no-prompt"], cwd=project, env=env)
    run(["azd", "deploy", config["package"]["agent_service"], "--no-prompt"], cwd=project, env=env)
    attempt["completed_at"] = utc()
    write(project / ATTEMPT, attempt)


def attempt_start(project):
    try:
        attempt = read(project / ATTEMPT)
        if (attempt["run_id"] != os.environ["GITHUB_RUN_ID"]
                or attempt["run_attempt"] != os.environ["GITHUB_RUN_ATTEMPT"]
                or not attempt["completed_at"]):
            raise ValueError("not completed in this attempt")
        return attempt["completed_at"]
    except (OSError, ValueError, KeyError) as error:
        raise ValueError("current completed deployment attempt required") from error


def validate_collected(project, target, after):
    from skills._shared.governance import validate_governance_manifest
    from skills._shared.governance_readiness import assess
    try:
        manifest = read(project / "specs/governance-manifest.json")
        validate_governance_manifest(manifest)
        report = read(project / ".threadlight/governance-live.json")
        if report["governance_manifest"] != manifest:
            raise ValueError("collector/manifest mismatch")
        evidence = manifest["collection_evidence"]
        if datetime.fromisoformat(evidence["started_at"].replace("Z", "+00:00")) <= datetime.fromisoformat(after.replace("Z", "+00:00")):
            raise ValueError("collection predates current deployment")
        result = assess(project, required_target=target)
        write(project / "tests/runtime-readiness.json", result)
        if result["status"] != "pass" or not result["live"]:
            raise ValueError("binding evidence not verified; noop cannot certify business bindings")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("current governance-manifest/v1 live binding proof required; legacy green is invalid") from error


def publish_postdeploy(project):
    """Publish the parent's actual collector output, without another invocation."""
    from skills._shared.governance import validate_governance_manifest
    try:
        report = read(project / "tests/postdeploy-manifest.json")
        manifest = report["governance_manifest"]
        validate_governance_manifest(manifest)
        if not isinstance(manifest.get("collection_evidence"), dict):
            raise ValueError("live collection missing")
        write(project / ".threadlight/governance-live.json", report)
        write(project / "specs/governance-manifest.json", manifest)
    except (OSError, ValueError, KeyError, TypeError) as error:
        write(project / "tests/runtime-readiness.json", {
            "status": "not-verified", "live": False,
            "reason": "parent safe-check did not produce validated collector evidence",
        })
        raise ValueError("parent collector evidence missing or invalid") from error


def postdeploy(project, config):
    after = attempt_start(project)
    target = config["expected_target"]
    account_matches(target)
    # Use installed CLI/package outside the checkout; no accidental source imports.
    command = [sys.executable, "-I", "-m", "threadlight_safe_check.safe_check",
               "--phase", "post-deploy", "--subscription", target["subscription"],
               "--rg", target["resource_group"], "--out", "tests"]
    failure = None
    try:
        run(command, cwd=project)
    except subprocess.CalledProcessError as error:
        failure = error
    # Validate even on failure to leave an honest, payload-free diagnostic.
    publish_postdeploy(project)
    validate_collected(project, target, after)
    if failure:
        raise failure
    for skill, script in (("evals", "evals_check.py"), ("redteam", "redteam_check.py")):
        run([sys.executable, ROOT / f"skills/threadlight-{skill}/scripts/{script}",
             "--target", project, "--emit"])
    run([sys.executable, ROOT / "skills/threadlight-production-ready/scripts/production_ready.py",
         "--root", project, "--static", "--no-rights-probe", "--quiet",
         "--out", project / "tests/production-readiness-manifest.json",
         "--report", project / "docs/production-readiness-report.md"])
    run([sys.executable, ROOT / "skills/threadlight-production-ready/scripts/evidence_gate.py",
         "--root", project, "--mode", "readiness-proof"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["validate-inputs", "prepare", "predeploy", "deploy", "postdeploy"])
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    try:
        if os.environ.get("GOVERNANCE_SAFE_PROBE") != "true":
            raise ValueError("explicit preproduction safe-probe opt-in required")
        expected = os.environ.get("GOVERNANCE_CI_CONFIG_SHA256", "")
        if not expected or hashlib.sha256(args.configuration.read_bytes()).hexdigest() != expected:
            raise ValueError("protected configuration SHA256 required/mismatched")
        config = load_inputs(args.configuration)
        project = args.project.resolve()
        if args.stage not in {"validate-inputs", "prepare"}:
            if read(project / ".threadlight/ci-input.json") != {"digest": config["input_digest"]}:
                raise ValueError("protected configuration changed during workflow")
        if args.stage != "validate-inputs":
            # Only the workflow-generated journal is accepted, never a path in
            # the protected configuration. Direct API tests remain local-only.
            state = Path(os.environ["READINESS_EVIDENCE_STATE"])
            readiness_evidence.journal(state, project)
            if args.stage in readiness_evidence.OUTPUTS:
                readiness_evidence.begin_stage(state, project, args.stage)
                succeeded = False
                try:
                    globals()[args.stage](project, config)
                    succeeded = True
                finally:
                    readiness_evidence.finish_stage(state, project, args.stage, succeeded)
            else:
                globals()[args.stage](project, config)
        print(f"{args.stage}: completed; no whole-agent governance assertion")
        return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError, subprocess.SubprocessError) as error:
        # Never dump protected configurations, HTTP bodies, bearer tokens or args.
        if isinstance(error, ValueError):
            print(f"::error::{error}", file=sys.stderr)
        else:
            print(f"::error::{args.stage} failed; required tooling/configuration or live evidence unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
