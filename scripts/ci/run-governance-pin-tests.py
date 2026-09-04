#!/usr/bin/env python3
"""Isolated exact-pin ACS/OPA contract gate; skipped tests can never yield PASS.

The published ACS b0 wheel targets Linux amd64. On other hosts use Docker's
linux/amd64 platform, not a cached locally built wheel or an upgraded SDK.
All writable scratch, package caches and virtualenvs remain in the worktree.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.request
import uuid
import venv
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / ".governance-validation"
VENV = SCRATCH / "linux-venv"
PIN_FILE = ROOT / "skills/_shared/governance-upstream-pin.json"
IMAGE = "python:3.12-slim"


def requirements(pins):
    return [
        *(f"{pins[key]['distribution']}=={pins[key]['version']}"
          for key in ("agt", "acs", "agent_hooks")),
        *(f"{name}=={version}" for name, version in pins["maf"].items()),
    ]


def run(args, **kwargs):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **kwargs)


def verify_wheels(wheelhouse, pins):
    """A configured package mirror is transport, not package provenance."""
    records = []
    for requirement in requirements(pins):
        name, version = requirement.split("==")
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as response:
            published = json.load(response)
        matches = [
            item for item in published["urls"]
            if item["filename"].endswith(".whl") and (wheelhouse / item["filename"]).exists()
        ]
        if len(matches) != 1:
            raise RuntimeError(f"expected one published wheel for {requirement}")
        item = matches[0]
        digest = hashlib.sha256((wheelhouse / item["filename"]).read_bytes()).hexdigest()
        if digest != item["digests"]["sha256"]:
            raise RuntimeError(f"public wheel checksum mismatch: {requirement}")
        records.append({"distribution": name, "version": version,
                        "filename": item["filename"], "sha256": digest})
    (SCRATCH / "wheel-provenance.json").write_text(json.dumps(records, indent=2) + "\n")


def verify_junit(path):
    cases = ET.parse(path).findall(".//testcase")
    if not cases or any(case.find(tag) is not None
                        for case in cases for tag in ("skipped", "failure", "error")):
        raise RuntimeError("runtime tests missing, skipped, failed, or errored")
    names = [case.attrib.get("name", "") for case in cases]
    required = [
        "test_real_native_loader_accepts_generated_bundle",
        "test_missing_opa_is_not_a_valid_policy_decision",
        "test_digest_is_deterministic_and_metadata_is_unsigned",
        "test_invalid_native_manifest_not_published",
        "test_publication_race_is_no_replace",
    ]
    if not all(name in names for name in required):
        raise RuntimeError("required native loader/integrity cases did not run")
    for case in ("missing-evidence", "refund-allow", "large-refund",
                 "approved-refund", "post-tool-transform", "output-transform"):
        if not any(name.startswith(f"test_real_policy_decisions[{case}-") for name in names):
            raise RuntimeError(f"required real policy decision did not run: {case}")
    return len(cases)


def runtime(pins):
    if Path(sys.prefix).resolve() != VENV.resolve() or sys.prefix == sys.base_prefix:
        raise RuntimeError("runtime proof requires the isolated validation virtualenv")
    installed = {}
    for requirement in requirements(pins):
        name, expected = requirement.split("==")
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f"{name}: expected {expected}, installed {actual}")
        installed[name] = actual
    provenance = SCRATCH / "wheel-provenance.json"
    records = json.loads(provenance.read_text())
    if {r["distribution"]: r["version"] for r in records} != installed:
        raise RuntimeError("published wheel provenance must cover every exact runtime pin")
    for record in records:
        wheel_path = SCRATCH / "wheels" / record["filename"]
        if hashlib.sha256(wheel_path.read_bytes()).hexdigest() != record["sha256"]:
            raise RuntimeError("wheel artifact changed after provenance verification")
        distribution = importlib.metadata.distribution(record["distribution"])
        with zipfile.ZipFile(wheel_path) as archive:
            for name in archive.namelist():
                if name.endswith((".py", ".so")) and ".data/" not in name:
                    if distribution.locate_file(name).read_bytes() != archive.read(name):
                        raise RuntimeError(f"installed runtime differs from published wheel: {name}")
    wheel = importlib.metadata.distribution(pins["acs"]["distribution"]).read_text("WHEEL")
    if "manylinux" not in wheel or "x86_64" not in wheel:
        raise RuntimeError("ACS proof requires the published Linux amd64 wheel")
    run([sys.executable, "-m", "pip", "check"])
    opa = SCRATCH / "opa-linux-amd64"
    expected = pins["opa"]["linux_amd64_static_sha256"]
    if not opa.exists():
        url = (f"https://github.com/open-policy-agent/opa/releases/download/"
               f"v{pins['opa']['version']}/opa_linux_amd64_static")
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("OPA release checksum mismatch")
        opa.write_bytes(data)
        opa.chmod(0o755)
    if opa.is_symlink() or hashlib.sha256(opa.read_bytes()).hexdigest() != expected:
        raise RuntimeError("OPA binary does not match shared exact SHA256")
    info = subprocess.check_output([str(opa), "version"], text=True)
    if f"Version: {pins['opa']['version']}\n" not in info:
        raise RuntimeError("OPA version mismatch")
    report = SCRATCH / f"runtime-{uuid.uuid4().hex}.xml"
    env = {**os.environ, "THREADLIGHT_GOVERNANCE_RUNTIME": "1", "ACS_OPA_PATH": str(opa)}
    env.pop("PYTEST_ADDOPTS", None)
    run([
        sys.executable, "-m", "pytest", "skills/threadlight-govern/tests/test_policy_bundle.py",
        "-m", "governance_runtime", "-q", f"--junitxml={report}",
        "-o", f"cache_dir={SCRATCH / 'pytest-cache'}",
    ], env=env)
    count = verify_junit(report)
    proof = {
        "packages": installed, "opa_version": pins["opa"]["version"], "opa_sha256": expected,
        "runtime_tests_passed": count, "junit": report.name,
        "scope": "local native ACS bundle loading and OPA decisions; not deployment enforcement",
    }
    (SCRATCH / "runtime-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof, indent=2))
    print("GOVERNANCE_RUNTIME_CONTRACT=PASS")


def main():
    SCRATCH.mkdir(exist_ok=True)
    (SCRATCH / "runtime-proof.json").unlink(missing_ok=True)
    for child in ("tmp", "pip-cache"):
        (SCRATCH / child).mkdir(exist_ok=True)
    os.environ.update(
        TMPDIR=str(SCRATCH / "tmp"), PIP_CACHE_DIR=str(SCRATCH / "pip-cache"),
        PYTHONPYCACHEPREFIX=str(SCRATCH / "pycache"), PYTHONNOUSERSITE="1",
    )
    os.environ.pop("PYTHONPATH", None)
    pins = json.loads(PIN_FILE.read_text())
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        # Fetch published target wheels on the host; container networking may
        # not have the host's working TLS/proxy configuration. No source builds.
        wheelhouse = SCRATCH / "wheels"
        wheelhouse.mkdir(exist_ok=True)
        marker = wheelhouse / "requirements.json"
        requested = requirements(pins) + ["pytest", "jsonschema[format]"]
        if not marker.exists() or json.loads(marker.read_text()) != requested:
            run([
                sys.executable, "-m", "pip", "download", "--quiet", "--only-binary=:all:",
                "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux2014_x86_64",
                "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312",
                "--abi", "abi3", "--abi", "none", "--dest", wheelhouse, *requested,
            ])
            marker.write_text(json.dumps(requested))
        verify_wheels(wheelhouse, pins)
        opa = SCRATCH / "opa-linux-amd64"
        if not opa.exists():
            url = (f"https://github.com/open-policy-agent/opa/releases/download/"
                   f"v{pins['opa']['version']}/opa_linux_amd64_static")
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != pins["opa"]["linux_amd64_static_sha256"]:
                raise RuntimeError("OPA release checksum mismatch")
            opa.write_bytes(data)
            opa.chmod(0o755)
        # Read-only source plus a dedicated writable artifact mount.
        run([
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "--mount", f"type=bind,source={ROOT},target=/workspace,readonly",
            "--mount", f"type=bind,source={SCRATCH},target=/workspace/.governance-validation",
            "-w", "/workspace", IMAGE,
            "python", "scripts/ci/run-governance-pin-tests.py",
        ])
        return
    if sys.argv[1:] == ["--runtime"]:
        runtime(pins)
        return
    python = VENV / "bin/python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(VENV)
    probe = (
        "import importlib.metadata as m; import pytest, jsonschema, yaml; "
        "assert 'date-time' in jsonschema.FormatChecker().checkers; "
        f"pins={requirements(pins)!r}; "
        "assert all(m.version(p.split('==')[0]) == p.split('==')[1] for p in pins)"
    )
    result = subprocess.run([str(python), "-c", probe], cwd=ROOT)
    wheelhouse = SCRATCH / "wheels"
    wheelhouse.mkdir(exist_ok=True)
    marker = wheelhouse / "requirements.json"
    requested = requirements(pins) + ["pytest", "jsonschema[format]"]
    if not marker.exists() or json.loads(marker.read_text()) != requested:
        run([python, "-m", "pip", "download", "--quiet", "--only-binary=:all:",
             "--dest", wheelhouse, *requested])
        marker.write_text(json.dumps(requested))
    verify_wheels(wheelhouse, pins)
    if result.returncode:
        # Only after missing/mismatched dependencies; never install globally or build source.
        run([python, "-m", "pip", "install", "--quiet", "--only-binary=:all:",
             "--no-index", "--find-links", wheelhouse,
             *requirements(pins), "pytest", "jsonschema[format]"])
    run([python, __file__, "--runtime"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"GOVERNANCE_RUNTIME_CONTRACT=FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
