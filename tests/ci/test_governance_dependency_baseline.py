"""Security fixes apply to portable packages and the native validation closure."""
import importlib.util
from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.version import Version
import pytest


ROOT = Path(__file__).resolve().parents[2]
CONTROL = "skills/threadlight-govern/references/control-plane/pyproject.toml"
BUILD_PROJECTS = (
    CONTROL,
    "skills/threadlight-govern/references/gateway/pyproject.toml",
    "skills/threadlight-deploy/references/governance/pyproject-maf.toml",
    "skills/threadlight-safe-check/pyproject.toml",
    "skills/threadlight-safe-check/references/probe-fixture/pyproject.toml",
)


def assert_fixed_pin(requirements, name, minimum):
    requirement = next(Requirement(value) for value in requirements
                       if Requirement(value).name.casefold() == name.casefold())
    pins = list(requirement.specifier)
    assert len(pins) == 1 and pins[0].operator == "==", requirement
    version = Version(pins[0].version)
    assert not version.is_prerelease and version >= Version(minimum), requirement


@pytest.mark.parametrize("name,minimum", (
    ("PyJWT", "2.13.0"),
    ("cryptography", "49.0.0"),
    ("aiohttp", "3.14.3"),
))
def test_control_plane_pins_include_reviewed_security_fixes(name, minimum):
    project = tomllib.loads((ROOT / CONTROL).read_text())
    assert_fixed_pin(project["project"]["dependencies"], name, minimum)


@pytest.mark.parametrize("path", BUILD_PROJECTS)
def test_portable_builds_include_setuptools_security_fix(path):
    project = tomllib.loads((ROOT / path).read_text())
    assert_fixed_pin(project["build-system"]["requires"], "setuptools", "83.0.0")


def test_native_validation_uses_the_same_patched_build_dependencies():
    spec = importlib.util.spec_from_file_location(
        "reviewed_dependency_runner", ROOT / "scripts/ci/run-governance-pin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert_fixed_pin(runner.gateway_requirements(), "setuptools", "83.0.0")


def test_dependency_maintenance_does_not_claim_running_image_remediation():
    guide = (ROOT / CONTROL).with_name("README.md").read_text()
    assert "## Dependency maintenance" in guide
    assert "does not update running images" in guide


def test_native_crypto_compatibility_retains_the_unresolved_advisory():
    project = tomllib.loads((ROOT / CONTROL).read_text())
    crypto = next(Requirement(value) for value in project["project"]["dependencies"]
                  if Requirement(value).name == "cryptography")
    assert Version(next(iter(crypto.specifier)).version) < Version("50")
    guide = (ROOT / CONTROL).with_name("README.md").read_text()
    assert "GHSA-g6cj-pr64-35w5" in guide
    assert "remains open" in guide
