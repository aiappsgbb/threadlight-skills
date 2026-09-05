"""Exact installed-wheel observations for local validation, not image attestation."""
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import platform
import zipfile
from datetime import datetime, timezone


def observe(pins, wheelhouse, *, root):
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise ValueError("official-native-wheels-require-linux-amd64")
    expected = {pins[k]["distribution"]: pins[k]["version"] for k in ("agt", "acs", "agent_hooks")}
    expected.update(pins["maf"])
    records = []
    for name, version in expected.items():
        artifact = pins["wheels"][name]
        wheel = Path(wheelhouse) / artifact["filename"]
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != artifact["sha256"]:
            raise ValueError("official-wheel-integrity:" + name)
        dist = importlib.metadata.distribution(name)
        if dist.version != version:
            raise ValueError("installed-version-drift:" + name)
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.namelist():
                if member.endswith((".py", ".so")) and ".data/" not in member:
                    if dist.locate_file(member).read_bytes() != archive.read(member):
                        raise ValueError("installed-byte-drift:" + name + ":" + member)
        records.append({"distribution": name, "version": dist.version, **artifact})
    imports = {}
    modules = {
        "agent_framework": "agent-framework-core",
        "agent_framework.foundry": "agent-framework-foundry",
        "agent_framework_foundry_hosting": "agent-framework-foundry-hosting",
        "agent_primitives": "agent-governance-toolkit-core",
        "agent_hooks": "agent-hooks-sdk",
        "agent_control_specification": "agent-control-specification",
    }
    for name, distribution in modules.items():
        module = importlib.import_module(name)
        imported = Path(module.__file__).resolve()
        expected_file = importlib.metadata.distribution(distribution).locate_file(
            name.replace(".", "/") + "/__init__.py").resolve()
        if imported != expected_file:
            raise ValueError("import-shadowing:" + name)
        imports[name] = str(imported)
    opa = Path(root) / ".governance-validation/opa-linux-amd64"
    if hashlib.sha256(opa.read_bytes()).hexdigest() != pins["opa"]["linux_amd64_static_sha256"]:
        raise ValueError("opa-byte-drift")
    return {"schema": "native-installed-observation/v1", "execution_mode": "local",
            "phase": "pre-deploy", "platform": "linux-amd64",
            "python_version": platform.python_version(),
            "collected_at": datetime.now(timezone.utc).isoformat(), "packages": records,
            "imports": imports, "opa_sha256": pins["opa"]["linux_amd64_static_sha256"],
            "deployed_image": "not-verified"}
