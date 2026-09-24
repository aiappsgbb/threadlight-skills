"""The exported source closure must include its generator's real skill-read helper."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys


ROOT = Path(__file__).resolve().parents[3]
REFERENCE = Path("skills/threadlight-deploy/references/governance")
HELPER = Path("skills/threadlight-local-test/references/quickstart/threadlight_quickstart/skill_approval.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_exported_returns_generator_copies_complete_portable_confirmation_runtime(tmp_path):
    packager = load("confirmation_source_packager", ROOT / REFERENCE / "package_returns_mcp.py")
    package = packager.materialize(tmp_path / "source")
    generator = load("exported_confirmation_generator", package / REFERENCE / "generate.py")
    target = tmp_path / "native"
    target.mkdir()
    generator.copy_sources(target)
    assert (target / "skill_approval.py").read_bytes() == (ROOT / HELPER).read_bytes()
    assert (target / "govern_control_plane/confirmation.py").is_file()
    provenance = json.loads((package / "source-package.json").read_text())
    assert provenance["files"][HELPER.as_posix()] == hashlib.sha256((ROOT / HELPER).read_bytes()).hexdigest()


def test_public_source_export_is_readable_by_native_nonroot_uid_under_private_umask(tmp_path):
    private = tmp_path / "operator-private"
    private.mkdir(mode=0o700)
    secret = private / "operator-only.json"
    secret.write_text('{"fixture":"private operator configuration"}')
    secret.chmod(0o600)
    packager = load("permission_source_packager", ROOT / REFERENCE / "package_returns_mcp.py")
    previous = os.umask(0o077)
    try:
        package = packager.materialize(private / "public-source")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(package.stat().st_mode) == 0o755
    for path in package.rglob("*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            assert mode == 0o755, path.relative_to(package)
        else:
            assert mode in (0o644, 0o755), path.relative_to(package)
    assert stat.S_IMODE(private.stat().st_mode) == 0o700
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
