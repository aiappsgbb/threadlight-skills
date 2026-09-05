#!/usr/bin/env python3
"""Isolated exact-pin ACS/OPA/MAF gate; skipped tests can never yield PASS.

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
import shutil
import subprocess
import sys
import tomllib
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
GATEWAY = ROOT / "skills/threadlight-govern/references/gateway"
CONTROL_PLANE = ROOT / "skills/threadlight-govern/references/control-plane"


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
    for name in (
        "test_bound_deny_zero_effects", "test_bound_allow_positive_control",
        "test_unbound_tool_never_evaluates_acs", "test_native_argument_transform",
        "test_durable_payload_free_receipt_precedes_effect_and_retries",
        "test_audit_disk_failure_denies_selected_tool",
        "test_post_tool_deny_discards_tainted_result",
        "test_model_cannot_forge_safe_evidence_or_approval",
        "test_nested_agents_require_independent_bundles",
    ):
        if name not in names:
            raise RuntimeError(f"required provider control did not run: {name}")
    for case in ("allow", "deny", "transform", "limit"):
        if f"test_native_buffered_stream[{case}]" not in names:
            raise RuntimeError("buffered output controls missing")
    from governance_ctk import BASE, PIN
    ctk = json.loads(PIN.read_text())
    vectors = sorted((BASE / "upstream/sdk/python/python/agent_hooks/ctk/vectors").glob("AH*.json"))
    declared = [json.loads(path.read_text()) for path in vectors
                if not set(json.loads(path.read_text()).get("capabilities", []))
                & set(ctk["undeclared_capabilities"])]
    if len(declared) != ctk["declared_vector_count"]:
        raise RuntimeError("CTK declared vector set changed")
    for vector in declared:
        if f"test_corrected_official_ctk[{vector['id']}]" not in names:
            raise RuntimeError(f"CTK vector missing: {vector['id']}")
    return len(cases)


def verify_gateway_junit(path):
    cases = ET.parse(path).findall(".//testcase")
    if not cases or any(case.find(tag) is not None
                        for case in cases for tag in ("skipped", "failure", "error")):
        raise RuntimeError("gateway controls missing, skipped, failed, or errored")
    names = {case.attrib.get("name") for case in cases}
    required = {
        "test_gateway_native_deny_receipt_zero_effects",
        "test_gateway_native_duplicate_same_outcome_one_effect",
        "test_gateway_native_actual_mcp_authenticated_protocol",
        "test_gateway_native_transform_and_post_deny",
        "test_gateway_native_concurrent_reservation_single_winner",
        "test_gateway_native_approval_consume_replay_and_fresh_positive",
        "test_gateway_native_transformed_approval_binds_actual_effect",
        "test_gateway_native_transport_wait_expiry_and_no_redirect",
        "test_gateway_native_post_transform_duplicate_uses_same_enforced_arguments",
    }
    if not required <= names:
        raise RuntimeError("required native gateway controls did not run")
    return len(cases)


def gateway_requirements():
    result = {"pytest==9.0.3", "setuptools==80.9.0"}
    for directory in (GATEWAY, CONTROL_PLANE):
        project = tomllib.loads((directory / "pyproject.toml").read_text())
        result.update(item for item in project["project"]["dependencies"]
                      if not item.startswith("threadlight-govern-"))
    return sorted(result)


def gateway_runtime(pins):
    """Separate service virtualenv: never modify the published MAF/ACS proof environment."""
    python = SCRATCH / "task9-linux-venv/bin/python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(python.parent.parent)
    wheelhouses = [SCRATCH / name for name in ("gateway-wheels", "wheels", "task8-wheelhouse")]
    links = [arg for path in wheelhouses if path.exists() for arg in ("--find-links", str(path))]
    required = gateway_requirements()
    probe = (
        "import importlib.metadata as m; "
        f"assert all(m.version(p.split('==')[0].split('[')[0]) == p.split('==')[1] for p in {required!r})")
    if subprocess.run([str(python), "-c", probe], cwd=ROOT).returncode:
        run([python, "-m", "pip", "install", "--quiet", "--no-index", *links, *required])
    # Read-only checkout compatible wheel builds: copy only portable source, never SDKs.
    staging = SCRATCH / f"gateway-build-{uuid.uuid4().hex}"
    for directory in (GATEWAY, CONTROL_PLANE):
        dest = staging / directory.relative_to(ROOT)
        dest.mkdir(parents=True, exist_ok=True)
        for source in (*directory.glob("*.py"), directory / "pyproject.toml"):
            shutil.copyfile(source, dest / source.name)
    for relative in (
        "skills/threadlight-govern/scripts/policy_bundle.py",
        "skills/threadlight-governed-actions/scripts/__init__.py",
        "skills/threadlight-governed-actions/scripts/canonical.py",
        "skills/_shared/governance-upstream-pin.json",
    ):
        dest = staging / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, dest)
    wheelhouse = staging / "wheels"
    try:
        run([python, "-m", "pip", "wheel", "--quiet", "--no-deps", "--no-build-isolation",
             "--wheel-dir", wheelhouse,
             staging / CONTROL_PLANE.relative_to(ROOT), staging / GATEWAY.relative_to(ROOT)])
        run([python, "-m", "pip", "install", "--quiet", "--no-deps", "--force-reinstall",
             *sorted(wheelhouse.glob("*.whl"))])
        run([python, "-m", "pip", "check"])
        # Compare every installed ACS/AGT/Hooks execution byte to the same published wheels.
        verification = """
import hashlib, importlib.metadata as m, json, pathlib, zipfile
scratch = pathlib.Path('.governance-validation')
pins = json.loads(pathlib.Path('skills/_shared/governance-upstream-pin.json').read_text())
names = {pins[key]['distribution'] for key in ('agt', 'acs', 'agent_hooks')}
records = [r for r in json.loads((scratch / 'wheel-provenance.json').read_text()) if r['distribution'] in names]
assert len(records) == 3
for record in records:
    artifact = scratch / 'wheels' / record['filename']
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == record['sha256']
    dist = m.distribution(record['distribution'])
    assert dist.version == record['version']
    with zipfile.ZipFile(artifact) as archive:
        for name in archive.namelist():
            if name.endswith(('.py', '.so')) and '.data/' not in name:
                assert dist.locate_file(name).read_bytes() == archive.read(name), name
"""
        run([python, "-c", verification])
        report = SCRATCH / f"gateway-{uuid.uuid4().hex}.xml"
        env = {**os.environ, "THREADLIGHT_GOVERNANCE_RUNTIME": "1",
               "ACS_OPA_PATH": str(SCRATCH / "opa-linux-amd64")}
        env.pop("PYTEST_ADDOPTS", None)
        run([python, "-m", "pytest", "skills/threadlight-govern/tests/test_gateway.py",
             "-q", f"--junitxml={report}", "-o", f"cache_dir={SCRATCH / 'pytest-cache'}"], env=env)
        count = verify_gateway_junit(report)
        run([python, "-c", verification])
        proof = {"tests_passed": count, "junit": report.name,
                 "scope": "native ACS/OPA + real MCP protocol, external HTTP/storage doubles; no live Azure"}
        (SCRATCH / "gateway-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
        return proof
    finally:
        shutil.rmtree(staging)


def deployment_runtime(pins):
    """Separate environment; generated contexts never depend on catalog package installation."""
    import importlib.util
    reference = ROOT / "skills/threadlight-deploy/references/governance"
    deps = tomllib.loads((reference / "pyproject-maf.toml").read_text())["project"]["dependencies"]
    required = sorted(set(requirements(pins) + gateway_requirements() + [
        item for item in deps if not item.startswith("threadlight-govern-")
    ] + ["github-copilot-sdk==1.0.1"]))
    wheelhouse = SCRATCH / "deployment-wheels"
    python = SCRATCH / "task10-linux-venv/bin/python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(python.parent.parent)
    probe = ("import importlib.metadata as m; "
             f"assert all(m.version(p.split('==')[0].split('[')[0]) == p.split('==')[1] for p in {required!r})")
    if subprocess.run([str(python), "-c", probe], cwd=ROOT).returncode:
        run([python, "-m", "pip", "install", "--quiet", "--no-index",
             "--find-links", wheelhouse, *required])
    spec = importlib.util.spec_from_file_location("deployment_generator", reference / "generate.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    staging = SCRATCH / f"deployment-packages-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        generator.vendor_control_plane(staging)
        generator.vendor_gateway(staging)
        run([python, "-m", "pip", "wheel", "--quiet", "--no-deps", "--no-build-isolation",
             "--wheel-dir", staging / "wheels", staging / "vendor/control-plane", staging / "vendor/gateway"])
        run([python, "-m", "pip", "install", "--quiet", "--no-deps", "--force-reinstall",
             *sorted((staging / "wheels").glob("*.whl"))])
        run([python, "-m", "pip", "check"])
        # Includes exact shared MAF/Hooks/ACS execution bytes, not just versions.
        records = json.loads((SCRATCH / "wheel-provenance.json").read_text())
        verification = """
import importlib.metadata as m, json, pathlib, zipfile
scratch = pathlib.Path('.governance-validation')
for record in json.loads((scratch / 'wheel-provenance.json').read_text()):
    dist = m.distribution(record['distribution'])
    assert dist.version == record['version']
    artifact = scratch / 'deployment-wheels' / record['filename']
    with zipfile.ZipFile(artifact) as archive:
        for name in archive.namelist():
            if name.endswith(('.py', '.so')) and '.data/' not in name:
                assert dist.locate_file(name).read_bytes() == archive.read(name), name
"""
        run([python, "-c", verification])
        report = SCRATCH / "deployment-tests.xml"
        env = {
            **os.environ, "THREADLIGHT_GOVERNANCE_RUNTIME": "1",
            "THREADLIGHT_GOVERNANCE_BICEP": str(SCRATCH / "deployment-bicep.json"),
            "ACS_OPA_PATH": str(SCRATCH / "opa-linux-amd64"), "OTEL_SDK_DISABLED": "true",
        }
        env.pop("PYTEST_ADDOPTS", None)
        run([python, "-m", "pytest", "skills/threadlight-deploy/tests/test_governance_wiring.py",
             "skills/threadlight-deploy/tests/test_azd_cli_contract.py",
             "-q", f"--junitxml={report}", "--basetemp", SCRATCH / "deployment-fixtures",
             "-o", "markers=governance_runtime: exact native runtime",
             "-o", f"cache_dir={SCRATCH / 'pytest-cache'}"], env=env)
        cases = ET.parse(report).findall(".//testcase")
        if not cases or any(case.find(tag) is not None for case in cases
                            for tag in ("skipped", "failure", "error")):
            raise RuntimeError("deployment tests missing, skipped, or failed")
        required_cases = {
            "test_generated_maf_native_constructor_host_and_failed_signature",
            "test_ghcp_native_host_and_actual_hook_schema",
            "test_ghcp_pre_mcp_bridge_refreshes_gateway_not_model_token",
            "test_generation_off_is_byte_for_byte_noop",
            "test_bicep_compiles_and_has_separate_scoped_service_identities",
        }
        if not required_cases <= {case.attrib["name"] for case in cases}:
            raise RuntimeError("required native generation probes did not run")
        run([python, "-c", verification])
        (SCRATCH / "deployment-proof.json").write_text(json.dumps({
            "tests_passed": len(cases), "junit": report.name,
            "packages": {r["distribution"]: r["version"] for r in records},
            "scope": "generated native hosts, installed portable wheels, HTTP relay, Bicep; no live Azure",
        }, indent=2) + "\n")
    finally:
        shutil.rmtree(staging)


def prepare_deployment(pins):
    """Host downloads retain working TLS settings; Linux container executes published wheels."""
    (SCRATCH / "deployment-proof.json").unlink(missing_ok=True)
    reference = ROOT / "skills/threadlight-deploy/references/governance"
    output = SCRATCH / "deployment-bicep.json"
    bicep = [shutil.which("bicep")] if shutil.which("bicep") else ["az", "bicep"]
    run([*bicep, "build", "--file", reference / "governance.bicep", "--outfile", output])
    deps = tomllib.loads((reference / "pyproject-maf.toml").read_text())["project"]["dependencies"]
    requested = sorted(set(requirements(pins) + gateway_requirements() + [
        item for item in deps if not item.startswith("threadlight-govern-")
    ] + ["github-copilot-sdk==1.0.1"]))
    wheelhouse = SCRATCH / "deployment-wheels"
    wheelhouse.mkdir(exist_ok=True)
    marker = wheelhouse / "requirements.json"
    if not marker.exists() or json.loads(marker.read_text()) != requested:
        run([sys.executable, "-m", "pip", "download", "--quiet", "--only-binary=:all:",
             "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux2014_x86_64",
             "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312",
             "--abi", "abi3", "--abi", "none", "--dest", wheelhouse, *requested])
        marker.write_text(json.dumps(requested))
    verify_wheels(wheelhouse, pins)
    opa = SCRATCH / "opa-linux-amd64"
    if not opa.exists():
        url = f"https://github.com/open-policy-agent/opa/releases/download/v{pins['opa']['version']}/opa_linux_amd64_static"
        with urllib.request.urlopen(url, timeout=120) as response:
            opa.write_bytes(response.read())
        opa.chmod(0o755)
    if hashlib.sha256(opa.read_bytes()).hexdigest() != pins["opa"]["linux_amd64_static_sha256"]:
        raise RuntimeError("OPA published checksum mismatch")
    run(["docker", "run", "--rm", "--platform", "linux/amd64",
         "-v", f"{ROOT}:/work:ro", "-v", f"{SCRATCH}:/work/.governance-validation",
         "-w", "/work", "-e", "TMPDIR=/work/.governance-validation/tmp",
         "-e", "PYTHONPYCACHEPREFIX=/work/.governance-validation/pycache",
         "-e", "PIP_CACHE_DIR=/work/.governance-validation/pip-cache",
         IMAGE, "python", "scripts/ci/run-governance-pin-tests.py", "--deployment-prepared"])
    fixtures = list((SCRATCH / "deployment-fixtures").glob("test_generated_maf_native*/external-pilot"))
    if len(fixtures) != 1:
        raise RuntimeError("generated external fixture missing")
    fixture = fixtures[0]
    run([*bicep, "build", "--file", fixture / "infra/main.bicep",
         "--outfile", SCRATCH / "deployment-main-bicep.json"])
    proof_dir = SCRATCH / "deployment-isolated-proof"
    proof_dir.mkdir(exist_ok=True)
    run(["docker", "run", "--rm", "--network", "none", "--platform", "linux/amd64",
         "-v", f"{fixture / 'src/agent'}:/app:ro",
         "-v", f"{SCRATCH / 'task10-linux-venv'}:/validation:ro",
         "-v", f"{proof_dir}:/proof", "-w", "/app",
         "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "OTEL_SDK_DISABLED=true",
         IMAGE, "/validation/bin/python", "-B", "-c",
         "from pathlib import Path; import py_compile; "
         "assert not Path('/work').exists(); "
         "[py_compile.compile(str(p), cfile='/proof/compiled.pyc', doraise=True) for p in Path('.').rglob('*.py')]; "
         "import container, runtime, govern_bundle.policy_bundle, govern_control_plane.client; "
         "from skills._shared.governance import validate_governance_contract; "
         "print('Generated agent imports and compiles without catalog filesystem or network')"])
    proof_path = SCRATCH / "deployment-proof.json"
    proof = json.loads(proof_path.read_text())
    proof.update(composed_bicep=True, isolated_fixture_import_and_compile=True)
    proof_path.write_text(json.dumps(proof, indent=2) + "\n")


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
    from governance_ctk import BASE
    results = BASE / "results"
    if results.exists():
        for path in results.glob("AH-CTK-*.json"):
            path.unlink()
    (BASE / "undeclared-capabilities.json").unlink(missing_ok=True)
    run([
        sys.executable, "-m", "pytest", "skills/threadlight-govern/tests/test_policy_bundle.py",
        "skills/threadlight-govern/tests/test_runtime_provider.py",
        "skills/threadlight-govern/tests/test_agent_hooks_ctk.py",
        "-p", "no:agent_hooks_ctk",
        "-m", "governance_runtime", "-q", f"--junitxml={report}",
        "-o", f"cache_dir={SCRATCH / 'pytest-cache'}",
    ], env=env)
    count = verify_junit(report)
    vector_results = [json.loads(path.read_text()) for path in sorted(results.glob("AH-CTK-*.json"))]
    if len(vector_results) != 47 or any(r["status"] != "pass" for r in vector_results):
        raise RuntimeError("declared CTK vectors missing or failed")
    optional = json.loads((BASE / "undeclared-capabilities.json").read_text())
    if len(optional) != 4 or any(r["status"] != "skip" or not r["detail"] for r in optional):
        raise RuntimeError("undeclared incremental capability report missing")
    # Recheck exact execution bytes after CTK activation, not just before pytest.
    for record in records:
        with zipfile.ZipFile(SCRATCH / "wheels" / record["filename"]) as archive:
            distribution = importlib.metadata.distribution(record["distribution"])
            for name in archive.namelist():
                if name.endswith((".py", ".so")) and ".data/" not in name:
                    if distribution.locate_file(name).read_bytes() != archive.read(name):
                        raise RuntimeError(f"runtime changed during conformance tests: {name}")
    gateway_proof = gateway_runtime(pins)
    proof = {
        "packages": installed, "opa_version": pins["opa"]["version"], "opa_sha256": expected,
        "runtime_tests_passed": count, "junit": report.name,
        "gateway": gateway_proof,
        "scope": "local native ACS/OPA + MAF enforcement; synthetic model/tools, no Azure calls",
        "ctk": {"declared_passed": len(vector_results), "declared_failed": 0,
                "tool_seam_host_error": "terminate", "undeclared": optional,
                "source_provenance": json.loads((BASE / "source-provenance.json").read_text()),
                "build_provenance": json.loads((BASE / "build-provenance.json").read_text())},
    }
    (SCRATCH / "runtime-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(f"Runtime controls: {count}; CTK: 47 passed, 0 failed; "
          "4 undeclared incremental-output vectors separately reported.")
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
    if sys.argv[1:] == ["--deployment"]:
        prepare_deployment(pins)
        return
    if sys.argv[1:] == ["--deployment-prepared"]:
        deployment_runtime(pins)
        return
    if sys.argv[1:] == ["--gateway-prepared"]:
        gateway_runtime(pins)
        return
    if sys.argv[1:] == ["--runtime"]:
        runtime(pins)
        return
    if sys.argv[1:] != ["--prepared"]:
        from governance_ctk import build
        build()
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
        gateway_wheels = SCRATCH / "gateway-wheels"
        gateway_wheels.mkdir(exist_ok=True)
        marker = gateway_wheels / "requirements.json"
        requested = gateway_requirements()
        if not marker.exists() or json.loads(marker.read_text()) != requested:
            run([
                sys.executable, "-m", "pip", "download", "--quiet", "--only-binary=:all:",
                "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux2014_x86_64",
                "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312",
                "--abi", "abi3", "--abi", "none", "--dest", gateway_wheels, *requested,
            ])
            marker.write_text(json.dumps(requested))
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
            "python", "scripts/ci/run-governance-pin-tests.py", "--prepared",
        ])
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
