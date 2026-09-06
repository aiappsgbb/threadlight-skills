#!/usr/bin/env python3
"""Export bounded, validated projections, never project files or producer logs.

The private run journal is outside the project and upload directory. A stage
removes its old outputs before invocation and journals only files it produced.
Hashes bind export to those bytes; they are not remote attestations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
MAX_BYTES = 2 * 1024 * 1024
OUTPUTS = {
    "predeploy": ("specs/governance-manifest.json", "tests/governed-actions-manifest.json",
                  "tests/governed-actions-apply-plan.json", "docs/governance/evidence-pack.md"),
    "deploy": (".threadlight/readiness-attempt.json",),
    "postdeploy": ("tests/postdeploy-manifest.json", ".threadlight/governance-live.json",
                   "specs/governance-manifest.json", "tests/runtime-readiness.json",
                   "tests/production-readiness-manifest.json", "specs/evals-manifest.json",
                   "specs/redteam-manifest.json", "docs/production-readiness-report.md"),
}
EXPORTS = {
    "specs/governance-manifest.json": "governance-summary.json",
    "tests/governed-actions-manifest.json": "governed-actions-summary.json",
    "tests/postdeploy-manifest.json": "postdeploy-summary.json",
    "tests/runtime-readiness.json": "runtime-readiness.json",
    ".threadlight/readiness-attempt.json": "deployment-attempt.json",
}
RESERVED = {
    *(p for paths in OUTPUTS.values() for p in paths),
    ".git", ".gitignore", ".github", ".azure", ".governance-tools", ".governance-validation",
    ".threadlight/ci-input.json", ".threadlight/ci-policy",
    ".threadlight/governance-package.json", ".threadlight/governance-deployment.json",
    ".threadlight/governance-probe.json",
    *(".threadlight/ci-" + c + ".json" for c in
      ("foundation", "generate", "agent-image", "stage-gateway", "bind")),
    "governance/source-provenance.json", "governance/change-plane.json",
    "governance/probe-contract.json", "governance/installed-packages.json",
    "specs/manifest.json", "specs/governance-contract.json", "specs/govern-manifest.json",
    "specs/governance-acceptances.json", "specs/SPEC.md",
    "azure.yaml", "azure-services.yaml", "agent.yaml", "infra", "AGENTS.md",
}


def require(condition):
    if not condition:
        raise ValueError("evidence boundary validation failed")


def relative_path(value):
    require(isinstance(value, str) and 0 < len(value) <= 512)
    require(re.fullmatch(r"[A-Za-z0-9_.\-/]+", value) is not None)
    path = Path(value)
    require(bool(path.parts) and not path.is_absolute() and value == path.as_posix()
            and all(p not in {".", "..", ".git"} for p in path.parts))
    return path


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def supplemental_path(value):
    path = relative_path(value)
    require(not any(overlaps(Path(value.casefold()), Path(p.casefold())) for p in RESERVED))
    return path


def contained(root, relative):
    relative = relative_path(relative)
    require(not root.is_symlink())
    path = root
    for part in relative.parts:
        path = path / part
        require(not path.is_symlink())
    require(path.resolve().is_relative_to(root.resolve()))
    return path


def read_bytes(root, relative):
    path = contained(root, relative)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_BYTES)
    # NOFOLLOW also refuses a final-component replacement after the path check.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES)
    return data


def parse(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("nonfinite evidence")

    value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    remaining = 50000

    def bound(node, depth=0):
        nonlocal remaining
        remaining -= 1
        require(depth <= 40 and remaining >= 0)
        if isinstance(node, str):
            require(len(node) <= 8192)
        elif isinstance(node, float):
            require(math.isfinite(node))
        elif isinstance(node, dict):
            for key, child in node.items():
                require(len(key) <= 256)
                bound(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                bound(child, depth + 1)
    bound(value)
    return value


def write(path, value):
    # Every destination is a fixed filename in a private, generated directory.
    require(not path.is_symlink())
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, allow_nan=False, indent=2)
        stream.write("\n")
    path.chmod(0o600)


def run_identity():
    values = {key: os.environ[key] for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")}
    require(all(re.fullmatch(r"[1-9][0-9]{0,19}", v) for v in values.values()))
    return values


def initialize(project):
    runner = Path(os.environ["RUNNER_TEMP"])
    require(runner.is_absolute() and runner.is_dir() and not runner.is_symlink())
    project = Path(project).absolute()
    directory = runner / ("threadlight-evidence-" + uuid4().hex)
    require(not overlaps(project.resolve(), directory.resolve()))
    directory.mkdir(mode=0o700)
    (directory / "upload").mkdir(mode=0o700)
    state = directory / "state.json"
    write(state, {"schema": "threadlight-evidence-journal/v1", "run": run_identity(),
                  "project": str(project), "stages": {}, "files": {}})
    return state


def journal(state, project):
    state = Path(state)
    runner = Path(os.environ["RUNNER_TEMP"]).resolve()
    require(state.name == "state.json"
            and re.fullmatch(r"threadlight-evidence-[a-f0-9]{32}", state.parent.name)
            and state.parent.parent.resolve() == runner and not state.parent.is_symlink())
    value = parse(read_bytes(state.parent, "state.json"))
    require(set(value) == {"schema", "run", "project", "stages", "files"}
            and value["schema"] == "threadlight-evidence-journal/v1"
            and value["run"] == run_identity() and value["project"] == str(Path(project).absolute())
            and not overlaps(Path(project).resolve(), state.parent.resolve())
            and isinstance(value["stages"], dict) and isinstance(value["files"], dict)
            and set(value["stages"]) <= OUTPUTS.keys() and set(value["files"]) <= EXPORTS.keys()
            and all(status in ("running", "success", "failure") for status in value["stages"].values()))
    return value


def begin_stage(state, project, stage):
    value = journal(state, project)
    require(stage in OUTPUTS)
    paths = OUTPUTS[stage]
    if stage == "deploy":
        paths += OUTPUTS["postdeploy"]
    for relative in paths:
        contained(project, relative).unlink(missing_ok=True)
        value["files"].pop(relative, None)
    value["stages"][stage] = "running"
    write(state, value)


def finish_stage(state, project, stage, succeeded):
    value = journal(state, project)
    require(value["stages"].get(stage) == "running")
    value["stages"][stage] = "success" if succeeded else "failure"
    for relative in OUTPUTS[stage]:
        if relative not in EXPORTS:
            continue
        try:
            digest = hashlib.sha256(read_bytes(project, relative)).hexdigest()
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            digest = "invalid"
        value["files"][relative] = {"stage": stage, "digest": digest}
    write(state, value)


def timestamp(value):
    require(isinstance(value, str) and len(value) <= 40)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None)
    return parsed


def count(value):
    require(type(value) is int and 0 <= value <= 1000000)
    return value


def attempt(value):
    require(isinstance(value, dict) and set(value) == {
        "run_id", "run_attempt", "started_at", "completed_at"})
    identity = run_identity()
    require(value["run_id"] == identity["GITHUB_RUN_ID"]
            and value["run_attempt"] == identity["GITHUB_RUN_ATTEMPT"])
    started = timestamp(value["started_at"])
    if value["completed_at"] is not None:
        require(started <= timestamp(value["completed_at"]) <= datetime.now(timezone.utc))
    return value


def recorded(project, journal_value, relative):
    receipt = journal_value["files"][relative]
    require(set(receipt) == {"stage", "digest"} and receipt["stage"] in OUTPUTS
            and relative in OUTPUTS[receipt["stage"]])
    data = read_bytes(project, relative)
    require(hashlib.sha256(data).hexdigest() == receipt["digest"])
    return parse(data)


def governance(value, project, journal_value):
    from skills._shared.governance import validate_governance_manifest
    validate_governance_manifest(value)
    if "collection_evidence" in value:
        deployment = attempt(recorded(project, journal_value, ".threadlight/readiness-attempt.json"))
        require(deployment["completed_at"] is not None)
        evidence = value["collection_evidence"]
        require(timestamp(deployment["completed_at"]) < timestamp(evidence["started_at"])
                <= timestamp(evidence["finished_at"]) <= datetime.now(timezone.utc))
        # These are read-only checks of the current frozen bindings, not Azure calls.
        from skills._shared.governance_readiness import current_context
        current = current_context(project)
        require(evidence["expected_target"] == current["expected_target"]
                and evidence["verified_policies"] == current["policy_bindings"]
                and evidence["configuration"]["declared"] == current["configuration"]
                and evidence["configuration"]["declared_file_digests"] == current["declared_file_digests"])
    else:
        require("offline_evidence" in value)
    return {"scope": "collection-not-readiness" if "collection_evidence" in value else "offline-inventory",
            "coverage": {key: count(number) for key, number in value["coverage"].items()},
            "gap_count": count(len(value["gaps"]))}


def validate_readiness(value):
    import jsonschema
    definitions = json.loads((ROOT / "skills/_shared/governance-manifest.schema.json").read_text())["definitions"]

    def field(name):
        return {"anyOf": [{"type": "null"}, *(
            {"$ref": f"#/definitions/{kind}/properties/{name}"}
            for kind in ("runtimeManifest", "offlineManifest", "collectedManifest"))]}

    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["status", "live", "reason"], "definitions": definitions,
        "properties": {
            "status": {"enum": ["pass", "must-fix", "not-verified"]},
            "live": {"type": "boolean"}, "reason": {"type": "string"},
            "coverage": field("coverage"), "policy_bundle": field("policy_bundle"),
            "gaps": {"anyOf": [{"$ref": f"#/definitions/{kind}/properties/gaps"}
                               for kind in ("runtimeManifest", "offlineManifest", "collectedManifest")]},
            "live_receipts": {"type": "array", "items": {"type": "string", "maxLength": 512}},
        },
    }
    jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)
    require(value["live"] or not value.get("live_receipts"))


def projection(relative, value, project, journal_value):
    require(isinstance(value, dict))
    if relative == "specs/governance-manifest.json":
        return governance(value, project, journal_value)
    if relative == "tests/governed-actions-manifest.json":
        import jsonschema
        base = ROOT / "skills/threadlight-governed-actions"
        schema = json.loads((base / "references/governed-actions-manifest.schema.json").read_text())
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)
        canonical = importlib.import_module("skills.threadlight-governed-actions.scripts.canonical")
        canonical.validate_payload_free_audit(value)
        require(value["phase"] == "pre-deploy")
        statuses = ("pass", "must-fix", "should-fix", "not-verified", "not-applicable")
        return {"scope": "local-assessment-not-deployment",
                "findings": {s: count(sum(f["status"] == s for f in value["findings"])) for s in statuses}}
    if relative == "tests/postdeploy-manifest.json":
        # The parent includes resource descriptions, errors and input snapshots.
        # Validate only this explicit projection; never serialize the parent.
        require(value.get("phase") == "post-deploy")
        timestamp(value["checked_at"])
        for key in ("gaps", "governance_gaps"):
            require(isinstance(value[key], list) and all(isinstance(g, str) for g in value[key]))
        return {**governance(value["governance_manifest"], project, journal_value),
                "resource_gap_count": count(len(value["gaps"])),
                "governance_gap_count": count(len(value["governance_gaps"]))}
    if relative == "tests/runtime-readiness.json":
        validate_readiness(value)
        if value["status"] == "pass" or value["live"]:
            from skills._shared.governance_readiness import assess
            governance(recorded(project, journal_value, "specs/governance-manifest.json"), project, journal_value)
            require(value["status"] == "pass" and value["live"] and assess(project) == value)
        return {"status": value["status"], "live": value["live"],
                "gap_count": count(len(value.get("gaps", [])))}
    if relative == ".threadlight/readiness-attempt.json":
        return attempt(value)
    raise ValueError("unrecognized artifact")


def export(state, project):
    value = journal(state, project)
    upload = contained(state.parent, "upload")
    require(upload.is_dir() and not list(upload.iterdir()))
    results = {}
    for relative, name in EXPORTS.items():
        if relative not in value["files"]:
            continue
        try:
            receipt = value["files"][relative]
            require(value["stages"].get(receipt["stage"]) in {"success", "failure"})
            summary = projection(relative, recorded(project, value, relative), project, value)
            write(upload / name, {"schema": "threadlight-readiness-evidence/v1",
                                  "producer_status": value["stages"][receipt["stage"]],
                                  "evidence": summary})
            results[name] = "exported"
        except Exception:
            # Validator errors may embed private JSON. Never persist/print them.
            results[name] = "invalid"
    ok = "invalid" not in results.values()
    write(upload / "export-status.json", {
        "schema": "threadlight-evidence-export/v1", "status": "success" if ok else "failure",
        "scope": "artifact-export-not-readiness", "run": run_identity(),
        "stages": value["stages"], "artifacts": results,
    })
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "export", "local"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    for field in ("native", "prepared"):
        parser.add_argument("--" + field, choices=("success", "failure", "cancelled", "skipped"))
    args = parser.parse_args()
    try:
        if args.command == "init":
            state = initialize(args.project)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                output.write(f"state={state}\n")
        elif args.command == "local":
            journal(args.state, args.project)
            require(args.native is not None and args.prepared is not None)
            upload = contained(args.state.parent, "upload")
            require(upload.is_dir() and not list(upload.iterdir()))
            write(upload / "local-contract-status.json", {
                "schema": "threadlight-local-contract-status/v1", "run": run_identity(),
                "scope": "workflow-outcomes-only-not-native-receipts-or-live-proof",
                "native": args.native, "prepared": args.prepared,
            })
        else:
            require(args.state is not None)
            ok = export(args.state, args.project)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                output.write(f"upload={args.state.parent / 'upload'}\n")
            return 0 if ok else 1
        if args.command == "local":
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                output.write(f"upload={upload}\n")
        return 0
    except Exception:
        print("::error::evidence export failed; no protected input or validator detail emitted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
