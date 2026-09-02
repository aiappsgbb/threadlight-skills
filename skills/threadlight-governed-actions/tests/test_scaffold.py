"""Tests for Task 11: the bounded, explicit, double-opt-in MAF governance
scaffold (:mod:`scaffold`) and its narrow CLI wiring in
:mod:`governed_actions`.

Every test in this file either drives ``scaffold.scaffold`` directly or
drives it exactly as a customer would, through
``governed_actions.main([...])`` -- never through a private/internal
helper a real caller could not reach.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Sequence

import pytest
import yaml

import contracts
import governed_actions
import render
import scaffold

#: The exact, fixed set of relative destination paths a successful
#: scaffold call ever writes -- mirrors :data:`scaffold.SCAFFOLD_FILES`'s
#: keys exactly, kept as a separate, independently-typed literal here so
#: a test asserting against it can never be satisfied by accidentally
#: importing and echoing back the very mapping under test.
EXPECTED_SCAFFOLD = {
    "src/governance/agent_hooks_interceptor.py",
    "policies/governed-actions.policy.yaml",
    "tests/governance/approval-binding-fixture.json",
    "tests/governance/test_governed_actions_contract.py",
    ".github/workflows/governed-actions.yml",
}


# ---------------------------------------------------------------------------
# Local helpers (deliberately self-contained; no cross-test-file imports,
# matching this test suite's existing convention).
# ---------------------------------------------------------------------------


def _run_git_command(args: Sequence[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_governed_actions_target(tmp_path: Path) -> Path:
    """A minimal, real git repository satisfying
    ``inputs.resolve_inputs``'s design-phase prerequisites: a committed
    ``specs/SPEC.md``. Used only by the one test that scaffolds into a
    target and then runs a real design-phase assessment against it.
    """
    root = tmp_path / "target"
    root.mkdir()
    _run_git_command(["init", "-q"], root)
    _run_git_command(["config", "user.email", "governed-actions-tests@example.com"], root)
    _run_git_command(["config", "user.name", "Governed Actions Tests"], root)
    _run_git_command(["remote", "add", "origin", "git@github.com:acme/widget.git"], root)
    (root / "specs").mkdir()
    (root / "specs" / "SPEC.md").write_text(
        "# Spec\n\n## 8. Actions\n\nNo required actions declared.\n", encoding="utf-8"
    )
    _run_git_command(["add", "-A"], root)
    _run_git_command(["commit", "-q", "-m", "initial commit"], root)
    return root


def _relative_files(root: Path) -> set:
    """Every regular file under *root* (``.git`` excluded), as
    root-relative POSIX-style strings -- used both as "what did scaffold
    write" and as "did anything at all get written".
    """
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _content_snapshot(root: Path) -> Dict[str, bytes]:
    """A ``relative-path -> bytes`` map for every regular file under
    *root* (``.git`` excluded), used to prove a refused/failed call
    changed not one byte of anything already present.
    """
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _scaffolded_options(root: Path) -> contracts.AssessmentOptions:
    return contracts.AssessmentOptions(root=root, phase="design", now="1970-01-01T00:00:00Z")


# ---------------------------------------------------------------------------
# Double opt-in
# ---------------------------------------------------------------------------


def test_scaffold_requires_both_opt_ins_via_cli(tmp_path):
    for argv in (
        ["--target", str(tmp_path), "--scaffold", "maf"],
        ["--target", str(tmp_path), "--confirm-scaffold"],
    ):
        exit_status = governed_actions.main(argv)
        assert exit_status == 2
        assert _relative_files(tmp_path) == set()


@pytest.mark.parametrize(
    "scaffold_kind,confirm_scaffold",
    [
        (None, False),
        ("maf", False),
        (None, True),
        ("not-a-real-kind", True),
    ],
)
def test_scaffold_function_refuses_missing_or_partial_opt_in(
    tmp_path, scaffold_kind, confirm_scaffold
):
    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(tmp_path, scaffold_kind, confirm_scaffold)
    assert _relative_files(tmp_path) == set()


# ---------------------------------------------------------------------------
# Successful scaffold: exact fixed set, no policy invention
# ---------------------------------------------------------------------------


def test_scaffold_writes_exact_fixed_set_without_policy_invention(tmp_path):
    root = _init_governed_actions_target(tmp_path)
    before = _relative_files(root) - {"specs/SPEC.md"}
    assert before == set()  # sanity: only the fixture's own SPEC.md exists yet

    exit_status = governed_actions.main(
        ["--target", str(root), "--scaffold", "maf", "--confirm-scaffold"]
    )
    assert exit_status == 0

    written = _relative_files(root) - {"specs/SPEC.md"}
    assert written == EXPECTED_SCAFFOLD

    policy_text = (root / "policies" / "governed-actions.policy.yaml").read_text(
        encoding="utf-8"
    )
    policy = yaml.safe_load(policy_text)
    assert set(policy.keys()) == {
        "policy_schema_version",
        "default_decision",
        "rules",
        "approvers",
        "authorization",
    }
    assert policy["rules"] == []
    assert policy["approvers"] == []
    assert policy["authorization"] == {"customer_owned": True}
    assert policy["default_decision"] == "deny"
    assert isinstance(policy["policy_schema_version"], str) and policy["policy_schema_version"]

    manifest = render.build_manifest(governed_actions.assess(_scaffolded_options(root)))
    assert manifest["summary"]["verdict"] != "governed"


def test_scaffold_return_value_lists_the_five_absolute_destinations(tmp_path):
    written = scaffold.scaffold(tmp_path, "maf", True)
    assert isinstance(written, tuple)
    assert len(written) == 5
    assert {path.relative_to(tmp_path).as_posix() for path in written} == EXPECTED_SCAFFOLD
    assert all(path.is_absolute() for path in written)
    assert all(path.is_file() for path in written)


# ---------------------------------------------------------------------------
# Idempotence / no-overwrite
# ---------------------------------------------------------------------------


def test_second_scaffold_run_returns_2_and_changes_no_bytes(tmp_path):
    governed_actions.main(["--target", str(tmp_path), "--scaffold", "maf", "--confirm-scaffold"])
    before = _content_snapshot(tmp_path)

    exit_status = governed_actions.main(
        ["--target", str(tmp_path), "--scaffold", "maf", "--confirm-scaffold"]
    )

    assert exit_status == 2
    assert _content_snapshot(tmp_path) == before


def test_scaffold_refuses_when_a_destination_already_exists(tmp_path):
    (tmp_path / "policies").mkdir()
    (tmp_path / "policies" / "governed-actions.policy.yaml").write_text(
        "already-here: true\n", encoding="utf-8"
    )
    before = _content_snapshot(tmp_path)

    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(tmp_path, "maf", True)

    assert _content_snapshot(tmp_path) == before
    assert _relative_files(tmp_path) == {"policies/governed-actions.policy.yaml"}


def test_scaffold_refuses_when_a_destination_is_a_dangling_symlink(tmp_path):
    (tmp_path / "policies").mkdir()
    os.symlink(
        tmp_path / "nowhere",
        tmp_path / "policies" / "governed-actions.policy.yaml",
    )

    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(tmp_path, "maf", True)

    # Every destination other than the pre-planted dangling symlink must
    # remain entirely absent (checked via lstat, not `Path.exists()`,
    # since `exists()` follows symlinks and would report the dangling
    # one itself as "absent" too).
    for relative in scaffold.SCAFFOLD_FILES:
        dest = tmp_path / relative
        if relative == "policies/governed-actions.policy.yaml":
            assert dest.is_symlink()
        else:
            with pytest.raises(FileNotFoundError):
                dest.lstat()


# ---------------------------------------------------------------------------
# Symlink and root refusal
# ---------------------------------------------------------------------------


def test_scaffold_refuses_symlinked_destination_parent(tmp_path):
    real_dir = tmp_path / "elsewhere"
    real_dir.mkdir()
    (tmp_path / "src").symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(tmp_path, "maf", True)

    assert _relative_files(real_dir) == set()
    assert _relative_files(tmp_path) == set()


def test_scaffold_refuses_symlinked_root(tmp_path):
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    symlinked_root = tmp_path / "symlinked-root"
    symlinked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(symlinked_root, "maf", True)

    assert _relative_files(real_root) == set()


def test_scaffold_refuses_filesystem_root():
    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(Path(Path(__file__).anchor), "maf", True)


def test_scaffold_refuses_its_own_package_root():
    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(scaffold._PACKAGE_ROOT, "maf", True)


def test_scaffold_refuses_a_root_nested_inside_its_own_package_root():
    nested = scaffold._PACKAGE_ROOT / "scripts"
    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(nested, "maf", True)


def test_scaffold_refuses_missing_root(tmp_path):
    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(tmp_path / "does-not-exist", "maf", True)


# ---------------------------------------------------------------------------
# Preflight-before-any-write / atomic all-or-nothing placement
# ---------------------------------------------------------------------------


def test_scaffold_preflights_all_destinations_before_any_write(tmp_path, monkeypatch):
    # Plant a conflicting destination for the *last* SCAFFOLD_FILES entry
    # only. If preflight genuinely runs for every destination before any
    # write begins, none of the other four (unconflicted) destinations
    # should ever be created either.
    last_relative = list(scaffold.SCAFFOLD_FILES)[-1]
    conflicting = tmp_path / last_relative
    conflicting.parent.mkdir(parents=True)
    conflicting.write_bytes(b"pre-existing")

    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(tmp_path, "maf", True)

    written = _relative_files(tmp_path)
    assert written == {last_relative}


def test_scaffold_rolls_back_every_placed_file_on_a_late_placement_failure(
    tmp_path, monkeypatch
):
    real_link = os.link
    destinations = list(scaffold.SCAFFOLD_FILES)
    fail_after = destinations[2]

    def _flaky_link(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if os.path.basename(dst) == os.path.basename(fail_after):
            raise OSError("simulated placement failure")
        return real_link(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(scaffold.os, "link", _flaky_link)

    with pytest.raises(scaffold.ScaffoldRefusedError):
        scaffold.scaffold(tmp_path, "maf", True)

    assert _relative_files(tmp_path) == set()
    # No leftover staging directory either.
    assert not any(
        entry.name.startswith(scaffold._STAGING_DIR_PREFIX) for entry in tmp_path.iterdir()
    )


# ---------------------------------------------------------------------------
# Normal lifecycle phase path never invokes scaffold
# ---------------------------------------------------------------------------


def test_normal_phase_path_never_invokes_scaffold(tmp_path, monkeypatch):
    root = _init_governed_actions_target(tmp_path)

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("scaffold.scaffold must never be called on the normal phase path")

    monkeypatch.setattr(governed_actions.scaffold, "scaffold", _forbidden)

    exit_status = governed_actions.main(["--target", str(root), "--phase", "design"])
    assert exit_status == 0


def test_missing_phase_and_no_scaffold_flags_still_returns_2(tmp_path):
    exit_status = governed_actions.main(["--target", str(tmp_path)])
    assert exit_status == 2
    assert _relative_files(tmp_path) == set()


# ---------------------------------------------------------------------------
# Template content: policy
# ---------------------------------------------------------------------------


def test_policy_template_is_syntactically_valid_yaml_and_deny_by_default():
    text = (scaffold._TEMPLATES_DIR / "governed-actions.policy.yaml.tmpl").read_text(
        encoding="utf-8"
    )
    policy = yaml.safe_load(text)
    assert policy["rules"] == []
    assert policy["approvers"] == []
    assert policy["authorization"] == {"customer_owned": True}
    assert policy["default_decision"] == "deny"


# ---------------------------------------------------------------------------
# Template content: no invented identities/thresholds anywhere
# ---------------------------------------------------------------------------

_GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


@pytest.mark.parametrize("template_name", list(scaffold.SCAFFOLD_FILES.values()))
def test_templates_contain_no_guid_or_email_identities(template_name):
    text = (scaffold._TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    assert not _GUID_RE.search(text), f"{template_name} contains what looks like a real GUID/tenant id"
    assert not _EMAIL_RE.search(text), f"{template_name} contains what looks like a real email address"


def test_approval_fixture_uses_only_deterministic_synthetic_identifiers(tmp_path):
    written = scaffold.scaffold(tmp_path, "maf", True)
    fixture_path = next(path for path in written if path.name == "approval-binding-fixture.json")

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    for key in ("action_id", "request_id", "approver_id"):
        assert fixture[key].startswith("synthetic-")


# ---------------------------------------------------------------------------
# Template content: workflow (pinned actions, read-only, no deploy permission)
# ---------------------------------------------------------------------------


def test_workflow_template_pins_exact_action_shas_and_grants_no_deploy_permission():
    text = (scaffold._TEMPLATES_DIR / "governed-actions.yml.tmpl").read_text(encoding="utf-8")
    document = yaml.safe_load(text)

    assert document["permissions"] == {"contents": "read"}
    for job in document["jobs"].values():
        assert job["permissions"] == {"contents": "read"}
        uses = [step["uses"] for step in job["steps"] if "uses" in step]
        assert "actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c" in uses
        assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in uses
        # No step actually performs (or could perform) a deploy: no cloud
        # login action, and no broadened permission (id-token or
        # anything beyond the single read-only `contents` key already
        # asserted above) that a deploy step would require.
        assert not any("login" in step.get("uses", "").lower() for step in job["steps"])

    lowered = text.lower()
    assert "id-token" not in lowered
    assert "azure/login" not in lowered
    assert "aws-actions" not in lowered


def test_workflow_template_runs_ctk_and_application_probe():
    text = (scaffold._TEMPLATES_DIR / "governed-actions.yml.tmpl").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    assert any(re.match(r"^(?:python3?\s+-m\s+)?ctk\b", line, re.IGNORECASE) for line in lines)
    assert any(
        re.match(r"^(?:python3?\s+-m\s+)?probes\b", line, re.IGNORECASE)
        and "application-probe" in line.lower()
        for line in lines
    )


# ---------------------------------------------------------------------------
# Template content: interceptor denies when no customer rule matches
# ---------------------------------------------------------------------------


def test_scaffolded_interceptor_denies_when_no_customer_rule_matches(tmp_path):
    scaffold.scaffold(tmp_path, "maf", True)
    sys.path.insert(0, str(tmp_path / "src" / "governance"))
    try:
        import importlib

        interceptor = importlib.import_module("agent_hooks_interceptor")
        decision = interceptor.evaluate("some-consequential-action", project_root=tmp_path)
        assert decision["decision"] == "deny"
        assert decision["rule_id"] is None

        pre_call_decision = interceptor.pre_tool_call(
            "some-consequential-action", project_root=tmp_path
        )
        assert pre_call_decision["decision"] == "deny"
    finally:
        sys.path.remove(str(tmp_path / "src" / "governance"))
        sys.modules.pop("agent_hooks_interceptor", None)


def test_scaffolded_contract_test_passes_against_the_scaffolded_fixtures(tmp_path):
    written = scaffold.scaffold(tmp_path, "maf", True)
    contract_test = next(
        path for path in written if path.name == "test_governed_actions_contract.py"
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(contract_test), "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


# ---------------------------------------------------------------------------
# Scaffold does not, by itself, make the project "governed"
# ---------------------------------------------------------------------------


def test_scaffold_alone_does_not_make_the_project_governed(tmp_path):
    root = _init_governed_actions_target(tmp_path)
    scaffold.scaffold(root, "maf", True)

    manifest = render.build_manifest(governed_actions.assess(_scaffolded_options(root)))
    assert manifest["summary"]["verdict"] != "governed"
    must_fix_or_not_verified = [
        finding
        for finding in manifest["findings"]
        if finding["status"] in ("must-fix", "not-verified")
    ]
    assert must_fix_or_not_verified, (
        "a freshly scaffolded project must still surface must-fix/not-verified "
        "findings -- scaffolding alone must never clear them"
    )
