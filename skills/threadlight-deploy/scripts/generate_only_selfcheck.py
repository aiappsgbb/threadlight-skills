#!/usr/bin/env python3
"""Offline acceptance self-check for a ``generate_only`` repository.

Proves the generated repo is deploy-ready without a subscription, login or
network beyond public base-image pulls:

* ``azure-yaml-schema``          azure.yaml validates against the vendored azd schema
* ``hosted-agent-definition``    one inline Foundry hosted agent, no agent.yaml / infra/core
* ``foundry-package-incomplete`` skills/_shared/foundry_package.py exits 3 (never 2)
* ``secret-scan``                no keys, tokens, non-allowlisted GUIDs, emails or personal paths
* ``agent-unit-tests``           generated offline agent tests pass
* ``bicep-build``                ``bicep build infra/main.bicep`` (skipped when bicep is absent)
* ``docker-build``               local ``docker build`` of both images with an EMPTY temporary
                                 DOCKER_CONFIG (no stored credentials, image never pushed)

Usage::

    python generate_only_selfcheck.py --project <repo> --report <file.json>
        [--skip-docker] [--require-docker] [--skip-bicep] [--require-bicep]

Exit 0 when no check fails, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCHEMA = HERE.parent / "references" / "generate-only" / "schemas" / "azure.yaml.schema.json"
FOUNDRY_PACKAGE = HERE.parents[1] / "_shared" / "foundry_package.py"

# Well-known built-in Azure role definition ids used by the pilot Bicep/hook.
ALLOWED_GUIDS = {
    "53ca6127-db72-4b80-b1b0-d745d6d5456d",  # Azure AI User
    "7f951dda-4ed3-4680-a7ca-43fe172d538d",  # AcrPull
    "b93aa761-3e63-49ed-ac28-beffa264f7ac",  # Foundry/Cognitive Services role used by the pilot
}
SECRET_PATTERNS = {
    "guid": re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    "storage-key": re.compile(r"AccountKey=[A-Za-z0-9+/=]{8,}"),
    "openai-key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "connection-secret": re.compile(r"(?i)(SharedAccessKey|InstrumentationKey|client_secret)\s*[=:]\s*['\"]?[A-Za-z0-9+/=_\-]{16,}"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@(?!example\.(?:com|org)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "azd-env-dir": re.compile(r"(^|[\s\"'/])\.azure/"),
    "personal-path": re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/"),
}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".bicep", ".sh", ".txt",
                 ".lock", ".tmpl", ".env", ".cfg", ".ini", ".ps1", ""}


def check(name: str, status: str, detail: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _load_yaml(path: Path) -> Any:
    import yaml  # PyYAML; required for azure.yaml validation

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def check_schema(project: Path) -> dict[str, str]:
    try:
        import jsonschema
    except ImportError:
        return check("azure-yaml-schema", "fail", "pip install jsonschema pyyaml")
    try:
        doc = _load_yaml(project / "azure.yaml")
    except Exception as exc:  # noqa: BLE001
        return check("azure-yaml-schema", "fail", f"azure.yaml unreadable: {exc}")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    kwargs: dict[str, Any] = {}
    try:  # never fetch the external extension $refs: treat them as permissive
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT201909

        def _retrieve(uri: str) -> Resource:
            return Resource.from_contents({}, default_specification=DRAFT201909)

        kwargs["registry"] = Registry(retrieve=_retrieve)
    except ImportError:
        pass
    validator = jsonschema.Draft201909Validator(schema, **kwargs)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        return check("azure-yaml-schema", "fail", "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5]))
    return check("azure-yaml-schema", "pass", f"validated against {SCHEMA.name}")


def check_hosted(project: Path) -> dict[str, str]:
    problems = []
    try:
        doc = _load_yaml(project / "azure.yaml") or {}
    except Exception as exc:  # noqa: BLE001
        return check("hosted-agent-definition", "fail", str(exc))
    services = doc.get("services") or {}
    agents = {k: v for k, v in services.items() if isinstance(v, dict) and v.get("host") == "azure.ai.agent"}
    if len(agents) != 1:
        problems.append(f"expected exactly one azure.ai.agent service, found {len(agents)}")
    for key, svc in agents.items():
        cfg = svc.get("config") or {}
        if (cfg.get("kind") or svc.get("kind")) != "hosted":
            problems.append(f"{key}: kind must be hosted")
        if svc.get("project") != "src/agent":
            problems.append(f"{key}: project must be src/agent")
    for rel in ("agent.yaml", "src/agent/agent.yaml", "infra/core"):
        if (project / rel).exists():
            problems.append(f"{rel} must not exist (agent is defined inline in azure.yaml)")
    for rel in ("src/agent/container.py", "src/agent/Dockerfile", "src/agent/requirements.lock",
                "src/mcp/server.py", "src/mcp/Dockerfile", "infra/main.bicep"):
        if not (project / rel).is_file():
            problems.append(f"{rel} missing")
    for rel in ("src/agent/requirements.lock", "src/mcp/requirements.lock", "evals/requirements.lock"):
        path = project / rel
        if path.is_file():
            reqs = [l for l in path.read_text().splitlines() if l and not l.startswith((" ", "#", "\t"))]
            if not reqs or not all("==" in r for r in reqs) or "--hash=sha256:" not in path.read_text():
                problems.append(f"{rel}: every requirement must be == pinned and hashed")
    if problems:
        return check("hosted-agent-definition", "fail", "; ".join(problems))
    return check("hosted-agent-definition", "pass", f"inline hosted agent {', '.join(agents)}")


def check_foundry_package(project: Path) -> dict[str, str]:
    result = subprocess.run([sys.executable, str(FOUNDRY_PACKAGE), "--workspace", str(project)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode == 3:
        return check("foundry-package-incomplete", "pass", "INCOMPLETE (exit 3) until deploy evidence exists")
    return check("foundry-package-incomplete", "fail",
                 f"expected exit 3, got {result.returncode}: {(result.stdout + result.stderr)[-400:]}")


def _scan_lines(path: Path) -> list[str]:
    hits = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return hits
    is_lock = path.suffix == ".lock" or path.name.startswith("requirements")
    is_ignore_file = path.name in {".gitignore", ".dockerignore", ".funcignore"}
    for lineno, line in enumerate(text.splitlines(), 1):
        if is_lock and "--hash=" in line:
            continue
        for name, pattern in SECRET_PATTERNS.items():
            for m in pattern.finditer(line):
                if name == "guid" and m.group(0).lower() in ALLOWED_GUIDS:
                    continue
                # Built-in Azure role definition IDs are public constants.
                if name == "guid" and "roleDefinitions" in line:
                    continue
                if name == "azd-env-dir" and is_ignore_file:
                    continue
                hits.append(f"{path}:{lineno}: {name}")
    return hits


def check_secrets(project: Path) -> dict[str, str]:
    hits: list[str] = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for d in list(dirs):
            if d == ".azure":
                hits.append(f"{Path(root, d).relative_to(project)}: azd environment directory must not ship")
                dirs.remove(d)
        for name in files:
            path = Path(root, name)
            if name == ".env" or name.endswith(".pem") or name.endswith(".pfx"):
                hits.append(f"{path.relative_to(project)}: credential file must not ship")
                continue
            if path.suffix.lower() in TEXT_SUFFIXES or name in {"Dockerfile", ".dockerignore"}:
                hits.extend(h.replace(str(project) + os.sep, "") for h in _scan_lines(path))
    if hits:
        return check("secret-scan", "fail", "; ".join(hits[:20]))
    return check("secret-scan", "pass", "no secrets, subscription/tenant ids, emails or personal paths")


def check_agent_tests(project: Path) -> dict[str, str]:
    tests = project / "src" / "agent" / "tests"
    if not tests.is_dir():
        return check("agent-unit-tests", "fail", "src/agent/tests missing")
    try:
        import pytest  # noqa: F401
    except ImportError:
        return check("agent-unit-tests", "skipped", "pytest not installed")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tests)],
                            cwd=project, capture_output=True, text=True, timeout=300)
    status = "pass" if result.returncode == 0 else "fail"
    return check("agent-unit-tests", status, result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr[-300:])


def check_bicep(project: Path, skip: bool, require: bool) -> dict[str, str]:
    if skip:
        return check("bicep-build", "skipped", "--skip-bicep")
    bicep = shutil.which("bicep")
    if not bicep:
        return check("bicep-build", "fail" if require else "skipped", "bicep CLI not on PATH")
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run([bicep, "build", str(project / "infra" / "main.bicep"), "--outfile",
                                 str(Path(tmp) / "main.json")], capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        return check("bicep-build", "fail", result.stderr[-600:])
    return check("bicep-build", "pass", "infra/main.bicep compiles")


def check_docker(project: Path, skip: bool, require: bool) -> dict[str, str]:
    if skip:
        return check("docker-build", "skipped", "--skip-docker")
    docker = shutil.which("docker")
    if not docker:
        return check("docker-build", "fail" if require else "skipped", "docker CLI not on PATH")
    builds = [("agent", project / "src" / "agent", project / "src" / "agent" / "Dockerfile"),
              ("mcp", project, project / "src" / "mcp" / "Dockerfile")]
    details = []
    with tempfile.TemporaryDirectory() as config_dir:
        # Empty DOCKER_CONFIG: no credential helper, no stored registry auth.
        Path(config_dir, "config.json").write_text("{}", encoding="utf-8")
        env = {**os.environ, "DOCKER_CONFIG": config_dir}
        probe = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"], env=env,
                               capture_output=True, text=True, timeout=60)
        if probe.returncode != 0:
            return check("docker-build", "fail" if require else "skipped",
                         f"docker daemon unavailable: {probe.stderr.strip()[-200:]}")
        for name, context, dockerfile in builds:
            tag = f"threadlight-generate-only-{name}:selfcheck"
            result = subprocess.run([docker, "build", "-f", str(dockerfile), "-t", tag, str(context)],
                                    env=env, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                return check("docker-build", "fail", f"{name}: {(result.stdout + result.stderr)[-800:]}")
            details.append(f"{name} built (local only)")
    return check("docker-build", "pass", "; ".join(details))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline acceptance self-check for a generate_only repo.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--require-docker", action="store_true")
    parser.add_argument("--skip-bicep", action="store_true")
    parser.add_argument("--require-bicep", action="store_true")
    args = parser.parse_args(argv)
    project = args.project.resolve()
    checks = [
        check_schema(project),
        check_hosted(project),
        check_foundry_package(project),
        check_secrets(project),
        check_agent_tests(project),
        check_bicep(project, args.skip_bicep, args.require_bicep),
        check_docker(project, args.skip_docker, args.require_docker),
    ]
    failed = [c["name"] for c in checks if c["status"] == "fail"]
    report = {"schema": "threadlight-generate-only-selfcheck/v1", "project": project.name,
              "status": "fail" if failed else "pass", "failed": failed, "checks": checks}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
