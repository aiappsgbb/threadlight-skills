"""Immutable upstream CTK overlay, isolated from the installed execution SDK.

The corrected Python runner calls _core.ctk_assert with a new posture field.
Its published Rust helper predates that oracle. Only the runner's local _core
reference is adapted to an out-of-process test-only assertion executable.
agent_hooks._core, all execution APIs, and installed wheel files are untouched.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".governance-validation/ctk"
PIN = ROOT / "skills/_shared/governance-ctk-pin.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    pin = json.loads(PIN.read_text())
    BASE.mkdir(parents=True, exist_ok=True)
    archive = ROOT / ".governance-validation/ctk-upstream.tar.gz"
    if not archive.exists():
        url = f"https://codeload.github.com/{pin['repository']}/tar.gz/{pin['commit']}"
        with urllib.request.urlopen(url, timeout=120) as response:
            archive.write_bytes(response.read())
    if sha(archive) != pin["archive_sha256"]:
        raise RuntimeError("corrected CTK archive hash mismatch")
    upstream = BASE / "upstream"
    hashes = {}
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            parts = Path(member.name).parts[1:]
            if not parts or not member.isfile():
                continue
            relative = Path(*parts)
            # Preserve the official Rust workspace and tests; no Python runtime
            # outside agent_hooks.ctk is extracted, much less installed.
            if not (str(relative).startswith(("sdk/rust/", "sdk/python/python/agent_hooks/ctk/",
                                             "conformance/claims/maf/"))
                    or str(relative) == "LICENSE"):
                continue
            if ".." in parts or relative.is_absolute():
                raise RuntimeError("unsafe testkit archive path")
            data = source.extractfile(member).read()
            path = upstream / relative
            if path.exists() and path.read_bytes() != data:
                raise RuntimeError(f"testkit changed: {relative}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    harness = upstream / "conformance/claims/maf/harness.py"
    if sha(harness) != pin["maf_harness_sha256"]:
        raise RuntimeError("official harness hash mismatch")
    oracle = BASE / "oracle"
    oracle.mkdir(exist_ok=True)
    for relative in ("Cargo.toml", "Cargo.lock", "src/main.rs"):
        destination = oracle / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "scripts/ci/ctk-oracle" / relative, destination)
    proof = {**pin, "files": hashes}
    (BASE / "source-provenance.json").write_text(json.dumps(proof, indent=2) + "\n")
    return pin


def build():
    pin = prepare()
    scratch = ROOT / ".governance-validation"
    command = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "--mount", f"type=bind,source={ROOT},target=/workspace,readonly",
        "--mount", f"type=bind,source={scratch},target=/workspace/.governance-validation",
        "-w", "/workspace/.governance-validation/ctk/oracle",
        "-e", "CARGO_HOME=/workspace/.governance-validation/ctk/cargo-home",
        "-e", "CARGO_TARGET_DIR=/workspace/.governance-validation/ctk/target",
        "-e", "TMPDIR=/workspace/.governance-validation/tmp",
        pin["oracle_build_image"], "cargo", "build", "--release", "--quiet", "--locked",
    ]
    subprocess.run(command, check=True)
    proof = {
        "oracle_sha256": sha(BASE / "target/release/threadlight-ctk-oracle"),
        "archive_sha256": pin["archive_sha256"], "build_image": pin["oracle_build_image"],
        "bridge_files": {name: sha(BASE / "oracle" / name)
                         for name in ("Cargo.toml", "Cargo.lock", "src/main.rs")},
        "scope": "out-of-process CTK assertion helper ONLY; no runtime installation",
    }
    (BASE / "build-provenance.json").write_text(json.dumps(proof, indent=2) + "\n")


def load_module(name, path, *, package=False):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)] if package else None,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def activate():
    """Called inside the exact-pin test process before any CTK import."""
    pin = json.loads(PIN.read_text())
    proof = json.loads((BASE / "source-provenance.json").read_text())
    for key, value in pin.items():
        if proof[key] != value:
            raise RuntimeError("testkit pin/provenance mismatch")
    archive = ROOT / ".governance-validation/ctk-upstream.tar.gz"
    if sha(archive) != pin["archive_sha256"]:
        raise RuntimeError("CTK archive changed")
    verified = {}
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            relative = str(Path(*Path(member.name).parts[1:]))
            if relative in proof["files"]:
                data = source.extractfile(member).read()
                verified[relative] = hashlib.sha256(data).hexdigest()
                if (BASE / "upstream" / relative).read_bytes() != data:
                    raise RuntimeError(f"changed CTK source: {relative}")
    if verified != proof["files"]:
        raise RuntimeError("CTK source provenance does not match immutable archive")
    build = json.loads((BASE / "build-provenance.json").read_text())
    binary = BASE / "target/release/threadlight-ctk-oracle"
    if sha(binary) != build["oracle_sha256"]:
        raise RuntimeError("testkit assertion binary changed")
    if (build["archive_sha256"] != pin["archive_sha256"]
            or build["build_image"] != pin["oracle_build_image"]):
        raise RuntimeError("oracle build provenance mismatch")
    for name, expected in build["bridge_files"].items():
        if sha(ROOT / "scripts/ci/ctk-oracle" / name) != expected:
            raise RuntimeError("CTK bridge changed since build")
    import agent_hooks
    from agent_hooks import _core
    if any(name.startswith("agent_hooks.ctk") for name in sys.modules):
        raise RuntimeError("CTK was imported before provenance activation")
    ctk = BASE / "upstream/sdk/python/python/agent_hooks/ctk"
    agent_hooks.ctk = load_module("agent_hooks.ctk", ctk / "__init__.py", package=True)
    from agent_hooks.ctk import runner

    class Oracle:
        ctk_should_skip = staticmethod(_core.ctk_should_skip)

        @staticmethod
        def ctk_assert(vector, recorded, record):
            payload = json.dumps({
                "vector": json.loads(vector), "recorded": json.loads(recorded),
                "record": json.loads(record),
            })
            return subprocess.check_output([str(binary)], input=payload, text=True, timeout=30)

    runner._core = Oracle
    return load_module(
        "threadlight_official_maf_ctk", BASE / "upstream/conformance/claims/maf/harness.py",
    )


if __name__ == "__main__":
    prepare()
