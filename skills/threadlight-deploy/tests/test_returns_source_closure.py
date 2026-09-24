"""The exported source closure must include its generator's real skill-read helper."""
import hashlib
import importlib.util
import json
from pathlib import Path
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
