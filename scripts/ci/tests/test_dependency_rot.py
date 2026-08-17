"""Tests for the dependency-rot watchdog.

The whole point of this watchdog is to fire when a pin stops resolving. So the
tests must prove it fires — and, just as importantly, that it stays quiet when
nothing is wrong, because a watchdog that cries wolf gets muted and then it
protects nothing.

Version lookups are injected, so every case here runs offline and
deterministically. The live PyPI call is exercised by the scheduled workflow,
not by CI on every PR.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "check_dependency_rot.py"
QUICKSTART = (
    REPO_ROOT / "skills" / "threadlight-local-test" / "references" / "quickstart" / "pyproject.toml"
)


def _load():
    spec = importlib.util.spec_from_file_location("check_dependency_rot", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_dependency_rot"] = module
    spec.loader.exec_module(module)
    return module


mod = _load()
Requirement = mod.Requirement


def lookup_from(mapping: dict[str, list[str]]):
    def _lookup(name: str) -> list[str]:
        return mapping[name]

    return _lookup


def severities(report) -> dict[str, str]:
    return {f.name: f.severity for f in report.findings}


# -- hard rot: the failure that actually happened ----------------------------

def test_specifier_matching_nothing_is_hard_rot() -> None:
    """The exact shape of the break that went unnoticed for six weeks."""
    report = mod.evaluate(
        [Requirement("agent-framework~=1.9.0")],
        lookup_from({"agent-framework": ["1.13.0", "1.14.0"]}),
    )
    assert severities(report) == {"agent-framework": "hard"}
    assert report.hard[0].latest == "1.14.0"


def test_package_with_no_stable_releases_is_hard_rot() -> None:
    report = mod.evaluate(
        [Requirement("ghost-pkg~=1.0")],
        lookup_from({"ghost-pkg": []}),
    )
    assert severities(report) == {"ghost-pkg": "hard"}


def test_package_with_only_prereleases_is_hard_rot() -> None:
    """A dep that only ever shipped betas cannot satisfy a stable pin."""
    report = mod.evaluate(
        [Requirement("beta-only~=1.0")],
        lookup_from({"beta-only": ["1.0.0b1", "1.0.0rc2", "1.0.0.dev3"]}),
    )
    assert severities(report) == {"beta-only": "hard"}


# -- headroom: resolves today, one yank from breaking ------------------------

def test_single_matching_version_is_flagged_as_fragile() -> None:
    report = mod.evaluate(
        [Requirement("azure-identity~=1.21.0")],
        lookup_from({"azure-identity": ["1.20.0", "1.21.0", "1.25.3"]}),
    )
    assert severities(report) == {"azure-identity": "headroom"}


def test_headroom_takes_precedence_over_drift() -> None:
    """A single-match pin that is also far behind is reported as fragile.

    Fragility is the more urgent fact: drift is a planned upgrade, a yank is an
    outage.
    """
    report = mod.evaluate(
        [Requirement("thing~=1.0.0")],
        lookup_from({"thing": ["1.0.0", "9.0.0"]}),
    )
    assert severities(report) == {"thing": "headroom"}


# -- drift: still fine, but we are testing a version nobody runs -------------

def test_far_behind_latest_is_drift() -> None:
    versions = ["1.40.0", "1.40.1", "1.40.2"] + [f"1.{n}.0" for n in range(41, 62)]
    report = mod.evaluate(
        [Requirement("streamlit~=1.40.0")],
        lookup_from({"streamlit": versions}),
    )
    assert severities(report) == {"streamlit": "drift"}
    assert "minor releases behind" in report.drift[0].detail


def test_major_bump_is_described_as_major_not_as_101_minors() -> None:
    versions = ["8.3.0", "8.3.1", "8.3.5", "9.0.0", "9.1.1"]
    report = mod.evaluate(
        [Requirement("pytest~=8.3.0")],
        lookup_from({"pytest": versions}),
    )
    assert report.drift[0].detail.endswith("(a major release behind)")


def test_drift_threshold_is_configurable() -> None:
    versions = ["1.0.0", "1.0.1", "1.3.0"]
    tight = mod.evaluate(
        [Requirement("thing~=1.0.0")], lookup_from({"thing": versions}), max_minor_drift=1
    )
    loose = mod.evaluate(
        [Requirement("thing~=1.0.0")], lookup_from({"thing": versions}), max_minor_drift=10
    )
    assert severities(tight) == {"thing": "drift"}
    assert severities(loose) == {"thing": "ok"}


# -- quiet when nothing is wrong --------------------------------------------

def test_healthy_pin_is_silent() -> None:
    report = mod.evaluate(
        [Requirement("thing~=1.2.0")],
        lookup_from({"thing": ["1.2.0", "1.2.1", "1.2.2", "1.3.0"]}),
    )
    assert severities(report) == {"thing": "ok"}
    assert not report.hard and not report.headroom and not report.drift


def test_prereleases_never_count_as_the_latest_version() -> None:
    """A 2.0.0b1 upstream must not make a healthy 1.x pin look a major behind."""
    report = mod.evaluate(
        [Requirement("thing~=1.2.0")],
        lookup_from({"thing": ["1.2.0", "1.2.1", "1.2.2", "2.0.0b1"]}),
    )
    assert severities(report) == {"thing": "ok"}


def test_unparseable_versions_are_skipped_not_fatal() -> None:
    report = mod.evaluate(
        [Requirement("thing~=1.2.0")],
        lookup_from({"thing": ["1.2.0", "1.2.1", "not-a-version", "???"]}),
    )
    assert severities(report) == {"thing": "ok"}


def test_lookup_failure_is_surfaced_but_not_counted_as_rot() -> None:
    """A PyPI outage must not be reported as a broken dependency."""

    def boom(name: str) -> list[str]:
        raise TimeoutError("pypi unreachable")

    report = mod.evaluate([Requirement("thing~=1.2.0")], boom)
    assert not report.findings
    assert len(report.errors) == 1
    assert "pypi unreachable" in report.errors[0]


# -- requirement parsing -----------------------------------------------------

def test_extras_are_included_in_the_scan() -> None:
    """`quickstart[aoai]` is what the E2E workflow and the workshop install.

    An extra that stops resolving breaks both, even when the base install is
    perfectly healthy — so extras must be scanned, not just `dependencies`.
    """
    names = {r.name for r in mod.parse_requirements(QUICKSTART)}
    assert "pytest" in names, "pytest only appears in the [test] extra"
    assert "agent-framework" in names


def test_duplicate_requirements_are_deduplicated() -> None:
    """The quickstart repeats agent-framework across several extras."""
    reqs = mod.parse_requirements(QUICKSTART)
    keys = [f"{r.name}{r.specifier}" for r in reqs]
    assert len(keys) == len(set(keys))


def test_real_quickstart_requirements_are_parseable() -> None:
    reqs = mod.parse_requirements(QUICKSTART)
    assert reqs, "quickstart declares no dependencies — layout changed?"
    for req in reqs:
        assert str(req.specifier), f"{req.name} is unpinned"


# -- exit codes --------------------------------------------------------------

def _soft_rot_lookup(name: str) -> list[str]:
    """Give every real quickstart pin two matching versions plus a far-future one.

    Two matches keeps it out of the 'fragile' bucket, and the distant major
    puts it squarely in 'drift' — so main() sees soft rot and nothing else.
    Derived from each requirement's own pin so this keeps working if the
    quickstart bumps a version.
    """
    for req in mod.parse_requirements(QUICKSTART):
        if req.name != name:
            continue
        base = str(req.specifier).lstrip("~=<>!= ")
        parts = (base.split(".") + ["0", "0"])[:3]
        major, minor = parts[0], parts[1]
        return [f"{major}.{minor}.0", f"{major}.{minor}.1", "99.0.0"]
    return ["99.0.0"]

def test_hard_rot_fails_the_run(monkeypatch, capsys) -> None:
    monkeypatch.setattr(mod, "fetch_pypi_versions", lambda name: ["99.0.0"])
    assert mod.main([]) == 1


def test_soft_rot_alone_does_not_fail_by_default(monkeypatch) -> None:
    """Drift is a planning signal. Failing on it would train people to ignore it."""
    monkeypatch.setattr(mod, "fetch_pypi_versions", _soft_rot_lookup)
    assert mod.main([]) == 0


def test_fail_on_drift_opts_into_strictness(monkeypatch) -> None:
    monkeypatch.setattr(mod, "fetch_pypi_versions", _soft_rot_lookup)
    assert mod.main(["--fail-on-drift"]) == 1


def test_missing_pyproject_is_an_error() -> None:
    assert mod.main(["--pyproject", "/nonexistent/pyproject.toml"]) == 1
