"""Tests for threadlight-governed-actions' static GitHub Copilot change-plane
assessment (Task 7).

Exercises ``ghcp.assess_workflow`` (single-workflow static control statuses:
PR-only deploy gating, SHA pinning, OIDC/WIF login, explicit least-privilege
permissions, and CI CTK/application-probe/eval presence) and
``ghcp.assess_change_plane`` (repository-wide aggregation of those signals
into the six GHCP-001..GHCP-006 findings, plus the always-``False``
``ghcp_internal_loop_intercepted`` control).

This plane is entirely separate from runtime mediation (Tasks 3-6): it
governs the GitHub Copilot coding-agent's PR/CI/deployment supply chain --
who can change and ship code -- never GitHub Copilot's own internal
reasoning or tool-calling loop, which this assessor never claims to
intercept.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_ghcp.py -q
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import ghcp
from ghcp import ChangePlaneResult, assess_change_plane, assess_workflow


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURES_DIR


# ---------------------------------------------------------------------------
# Workflow-authoring helpers (kept local to this test module -- ghcp.py has
# no writer/scaffold responsibility of its own).
# ---------------------------------------------------------------------------


def pinned_oidc_workflow(
    tmp_path: Path,
    *,
    action_sha: str,
    triggers: str = "pull_request:",
    extra_permissions: str = "",
) -> Path:
    """Write a minimal workflow that pins every action and logs in via OIDC.

    Used as the clean baseline for the positive ``sha_pins``/``oidc_wif``
    recognition test, and as a base other tests mutate one field at a time
    (a 39-character ref, a stripped ``id-token`` permission, and so on).
    """
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflow_dir / "ci.yml"
    workflow_path.write_text(
        textwrap.dedent(
            f"""\
            name: CI
            on:
              {triggers}
            permissions:
              contents: read
              id-token: write
              {extra_permissions}
            jobs:
              deploy:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@{action_sha}
                  - uses: azure/login@{action_sha}
                    with:
                      client-id: ${{{{ secrets.AZURE_CLIENT_ID }}}}
                      tenant-id: ${{{{ secrets.AZURE_TENANT_ID }}}}
                      subscription-id: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    return workflow_path


def _write_clean_repo(tmp_path: Path) -> Path:
    """Build a repo-wide fixture that is clean on every GHCP control except
    live-only branch-protection/required-check enforcement, which static
    analysis can never confirm on its own (see the invariant test below).
    """
    root = tmp_path / "clean-repo"
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        textwrap.dedent(
            """\
            name: CI
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    (root / "CODEOWNERS").write_text(
        "\n".join(
            [
                "src/governance/** @octo-org/governance",
                "policies/** @octo-org/governance",
                "tests/** @octo-org/governance",
                ".github/workflows/governed-actions.yml @octo-org/governance",
                "tests/governed-actions-manifest.json @octo-org/governance",
                "tests/governed-actions-apply-plan.json @octo-org/governance",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# Step 1 (plan-specified): the unprotected fixture emits every GHCP finding.
# ---------------------------------------------------------------------------


def test_unprotected_change_plane_emits_all_required_findings(fixture_root):
    result = assess_change_plane(
        fixture_root / "unprotected-ghcp", live_github=None, live_azure=None
    )
    assert {f.finding_id for f in result.findings} == {
        "GHCP-001", "GHCP-002", "GHCP-003",
        "GHCP-004", "GHCP-005", "GHCP-006",
    }
    assert result.controls["ghcp_internal_loop_intercepted"] is False


def test_unprotected_findings_are_all_must_fix(fixture_root):
    result = assess_change_plane(
        fixture_root / "unprotected-ghcp", live_github=None, live_azure=None
    )
    assert all(finding.status == "must-fix" for finding in result.findings)
    assert all(finding.plane == "change" for finding in result.findings)


def test_change_plane_result_is_frozen(fixture_root):
    result = assess_change_plane(
        fixture_root / "unprotected-ghcp", live_github=None, live_azure=None
    )
    assert isinstance(result, ChangePlaneResult)
    with pytest.raises(Exception):
        result.findings = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Step 1 (plan-specified): full SHA + OIDC recognition.
# ---------------------------------------------------------------------------


def test_full_sha_and_oidc_are_recognized(tmp_path):
    workflow = pinned_oidc_workflow(
        tmp_path, action_sha="0ad4c47a9e566829e19b6099ee3458ac923f5d3c"
    )
    result = assess_workflow(workflow)
    assert result.sha_pins == "pass"
    assert result.oidc_wif == "pass"


# ---------------------------------------------------------------------------
# SHA pin exactness: 40 lowercase hex characters, no more, no less, for both
# first-party and third-party actions alike; local `./` actions are exempt.
# ---------------------------------------------------------------------------


def test_39_character_sha_is_rejected(tmp_path):
    workflow = pinned_oidc_workflow(
        tmp_path, action_sha="0ad4c47a9e566829e19b6099ee3458ac923f5d"
    )
    result = assess_workflow(workflow)
    assert result.sha_pins == "must-fix"


def test_40_character_uppercase_sha_is_rejected(tmp_path):
    workflow = pinned_oidc_workflow(
        tmp_path, action_sha="0AD4C47A9E566829E19B6099EE3458AC923F5D3"
    )
    result = assess_workflow(workflow)
    assert result.sha_pins == "must-fix"


def test_floating_tag_is_rejected_for_both_first_and_third_party_actions(
    tmp_path,
):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "ci.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: CI
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                  - uses: octo-org/internal-build-action@v1
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.sha_pins == "must-fix"


def test_local_action_reference_is_exempt_from_sha_pinning(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (tmp_path / ".github" / "actions" / "local").mkdir(parents=True)
    workflow_path = workflow_dir / "ci.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: CI
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: ./.github/actions/local
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.sha_pins == "pass"


# ---------------------------------------------------------------------------
# pull_request_target with an untrusted checkout is a change-plane bypass.
# ---------------------------------------------------------------------------


def test_pull_request_target_with_untrusted_checkout_is_must_fix(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "label.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Label
            on:
              pull_request_target:
            permissions:
              contents: read
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      ref: ${{ github.event.pull_request.head.sha }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_without_untrusted_checkout_is_not_flagged(
    tmp_path,
):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "label.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Label
            on:
              pull_request_target:
            permissions:
              contents: read
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "pass"


def test_direct_push_deploy_is_must_fix(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              push:
                branches: [main]
            permissions:
              contents: read
            jobs:
              deploy:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


# ---------------------------------------------------------------------------
# Static evidence can never, by itself, prove live branch protection.
# ---------------------------------------------------------------------------


def test_static_evidence_never_proves_live_branch_protection(tmp_path):
    root = _write_clean_repo(tmp_path)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert {f.finding_id for f in result.findings} == {"GHCP-002"}
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_live_github_evidence_can_confirm_branch_protection(tmp_path):
    root = _write_clean_repo(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": True,
                "required_status_checks": ["test"],
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert result.findings == ()


def test_ghcp_internal_loop_intercepted_is_always_false(tmp_path):
    root = _write_clean_repo(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": True,
                "required_status_checks": ["test"],
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert result.controls["ghcp_internal_loop_intercepted"] is False
