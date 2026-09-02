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

import json
import subprocess
import textwrap
from pathlib import Path
from typing import Sequence

import pytest

import ghcp
from ghcp import ChangePlaneResult, Finding, assess_change_plane, assess_workflow


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "references" / "finding-catalog.json"
)


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


_STRUCTURED_REQUIRED_REVIEWS = {
    "require_code_owner_reviews": True,
    "required_approving_review_count": 1,
}


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
                # Rule 6 requires every job's own explicit permissions --
                # a sibling job cannot silently inherit the workflow-level
                # default, so this job repeats it explicitly even though
                # there is only one job in this file.
                permissions:
                  contents: read
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
    # Exactly the 40-character reference below with its last character
    # dropped -- a true 39-character lowercase-hex string, not merely "one
    # character shorter than something else".
    action_sha = "0ad4c47a9e566829e19b6099ee3458ac923f5d3c"
    assert len(action_sha) == 40
    truncated_sha = action_sha[:-1]
    assert len(truncated_sha) == 39
    workflow = pinned_oidc_workflow(tmp_path, action_sha=truncated_sha)
    result = assess_workflow(workflow)
    assert result.sha_pins == "must-fix"


def test_40_character_uppercase_sha_is_rejected(tmp_path):
    # The same 40-character reference, upper-cased -- a true 40-character
    # string, just not lowercase, so length alone never explains rejection.
    action_sha = "0ad4c47a9e566829e19b6099ee3458ac923f5d3c"
    uppercase_sha = action_sha.upper()
    assert len(uppercase_sha) == 40
    workflow = pinned_oidc_workflow(tmp_path, action_sha=uppercase_sha)
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


# ---------------------------------------------------------------------------
# Final blocker 1: every untrusted-checkout ref shape actually used in the
# wild -- not only the full `github.event.pull_request.head` object path --
# must be recognized: the `github.head_ref` shorthand context variable, the
# `.ref`/`.sha` fields individually, and a literal `refs/pull/...` ref.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref_expression",
    [
        "${{ github.event.pull_request.head.ref }}",
        "${{ github.event.pull_request.head.sha }}",
        "${{ github.head_ref }}",
        "refs/pull/${{ github.event.pull_request.number }}/merge",
        "refs/pull/123/head",
    ],
)
def test_pull_request_target_untrusted_ref_variants_are_must_fix(
    tmp_path, ref_expression
):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "label.yml"
    workflow_path.write_text(
        textwrap.dedent(
            f"""\
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
                      ref: {ref_expression}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


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
# Final blocker 2: a workflow that deploys via a raw `az`/`azd` CLI command
# in a `run:` step -- not a marketplace deploy action -- must still be
# classified as a deploy workflow (and so still gated by rule 1), even when
# the job that runs it is named something entirely unrelated to "deploy".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "deploy_command",
    [
        "az webapp deploy --resource-group rg --name app --src-path app.zip",
        "az containerapp update --name app --resource-group rg --image img",
        "az functionapp deployment source config-zip -g rg -n app --src app.zip",
        "azd deploy",
        "azd up --no-prompt",
    ],
)
def test_direct_push_with_raw_cli_deploy_command_is_must_fix_regardless_of_job_name(
    tmp_path, deploy_command
):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "release.yml"
    workflow_path.write_text(
        textwrap.dedent(
            f"""\
            name: Release
            on:
              push:
                branches: [main]
            permissions:
              contents: read
              id-token: write
            jobs:
              ship-it:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{{{ secrets.AZURE_CLIENT_ID }}}}
                      tenant-id: ${{{{ secrets.AZURE_TENANT_ID }}}}
                      subscription-id: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}
                  - name: Ship
                    run: {deploy_command}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.is_deploy is True
    assert result.pr_gate == "must-fix"


def test_az_group_create_alone_is_not_treated_as_a_deploy(tmp_path):
    """A bare `az group create` (creating an empty resource group, not
    deploying anything into it) must not itself trigger the deploy-run-
    command classification -- only an actual deployment-shaped command
    (`az deployment group create`, `az webapp deploy`, ...) should."""
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "provision.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Provision
            on:
              push:
                branches: [main]
            permissions:
              contents: read
            jobs:
              setup:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Create resource group
                    run: az group create --name rg --location eastus
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.is_deploy is False


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
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["test"],
                "enforce_admins": True,
                "allow_force_pushes": False,
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
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["test"],
                "enforce_admins": True,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert result.controls["ghcp_internal_loop_intercepted"] is False



# ---------------------------------------------------------------------------
# Spec-gap fix 1: a relative `root` must never double-prefix workflow paths
# when hashing the workflow set (previously only exercised with absolute
# `tmp_path`/`fixture_root` arguments, which never reproduced the bug).
# ---------------------------------------------------------------------------


def test_relative_root_does_not_double_prefix_workflow_hash(tmp_path, monkeypatch):
    absolute_root = _write_clean_repo(tmp_path)
    absolute_result = assess_change_plane(absolute_root, live_github=None, live_azure=None)

    monkeypatch.chdir(tmp_path)
    relative_result = assess_change_plane(
        Path(absolute_root.name), live_github=None, live_azure=None
    )

    assert relative_result.controls == absolute_result.controls
    assert [f.finding_id for f in relative_result.findings] == [
        f.finding_id for f in absolute_result.findings
    ]


# ---------------------------------------------------------------------------
# Spec-gap fix 2: an explicit bypass command (commit-message CI-skip marker,
# forced push, administrative merge override) is rejected even when the
# workflow otherwise looks PR-gated -- checked only against real `run:`/
# `if:` text, never a step name or a benign word that merely contains the
# same letters.
# ---------------------------------------------------------------------------


def _workflow_with_run_command(tmp_path: Path, run_command: str, *, triggers: str = "pull_request:") -> Path:
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
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run
                    run: |
                      {run_command}
            """
        ),
        encoding="utf-8",
    )
    return workflow_path


@pytest.mark.parametrize(
    "run_command",
    [
        "echo '[skip ci]' && git commit -m 'wip [skip ci]'",
        "git push --force origin main",
        "git push -f origin main",
        "gh pr merge 42 --admin",
        "echo bypass required check",
        "echo admin-merge requested",
    ],
)
def test_bypass_command_in_run_text_is_must_fix(tmp_path, run_command):
    workflow = _workflow_with_run_command(tmp_path, run_command)
    result = assess_workflow(workflow)
    assert result.pr_gate == "must-fix"


@pytest.mark.parametrize(
    "run_command",
    [
        "python -m ctk run-workforce-report",
        "echo 'law enforcement demo'",
        "echo reinforce the pipeline",
        "echo 'administrative task, not a merge'",
    ],
)
def test_benign_words_never_false_positive_as_bypass_commands(tmp_path, run_command):
    workflow = _workflow_with_run_command(tmp_path, run_command)
    result = assess_workflow(workflow)
    assert result.pr_gate == "pass"


def test_bypass_command_in_step_name_only_is_not_flagged(tmp_path):
    """A step merely *named* with a bypass-sounding phrase, whose actual
    `run:` command never contains one, is not evidence of a real bypass."""
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
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Never actually bypass required checks
                    run: echo ok
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "pass"


# ---------------------------------------------------------------------------
# Spec-gap fix 3: a live required-status-check list that does not actually
# name the workflow's own gating job stays not-verified, never an inferred
# `pass` -- static CI presence and live required-check enforcement are
# distinct claims.
# ---------------------------------------------------------------------------


def test_required_status_checks_naming_unrelated_job_stays_not_verified(tmp_path):
    root = _write_clean_repo(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["some-unrelated-check"],
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert {f.finding_id for f in result.findings} == {"GHCP-002"}
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_required_status_checks_naming_gating_job_confirms_pass(tmp_path):
    root = _write_clean_repo(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["test"],
                "enforce_admins": True,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert result.findings == ()


# ---------------------------------------------------------------------------
# Final blocker 4a: a live "pass" must be semantically bound to genuine
# protection -- exempting admins, still allowing force pushes, or granting
# a bypass allowance each individually defeat what "protected" is supposed
# to mean, and must each keep the branch-protection confirmation from ever
# resolving to a `pass`.
# ---------------------------------------------------------------------------


def test_admins_exempt_from_branch_protection_stays_not_verified(tmp_path):
    root = _write_clean_repo(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["test"],
                "enforce_admins": False,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_force_pushes_allowed_keeps_branch_protection_not_verified(tmp_path):
    root = _write_clean_repo(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["test"],
                "enforce_admins": True,
                "allow_force_pushes": True,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_review_bypass_allowance_keeps_branch_protection_not_verified(tmp_path):
    root = _write_clean_repo(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": {
                    "bypass_pull_request_allowances": {"users": ["octocat"]},
                },
                "required_status_checks": ["test"],
                "enforce_admins": True,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_enforce_admins_enabled_mapping_shape_still_confirms_pass(tmp_path):
    """`enforce_admins` can be reported as a bare bool or a
    ``{"enabled": bool}`` mapping (as GitHub's own API actually does) --
    both shapes must be honored identically."""
    root = _write_clean_repo(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["test"],
                "enforce_admins": {"enabled": True},
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert result.findings == ()


# ---------------------------------------------------------------------------
# Final blocker 4b: environment protection is confirmed "as available" --
# a deploy job's own declared GitHub Environment must be reported protected
# whenever live evidence covers it, but the complete absence of any
# environment declaration (or of live environment evidence at all) must
# never block an otherwise-genuine pass on its own.
# ---------------------------------------------------------------------------


def _write_clean_repo_with_deploy_environment(tmp_path: Path, environment: str) -> Path:
    root = tmp_path / "env-protected-repo"
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
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    (workflow_dir / "deploy.yml").write_text(
        textwrap.dedent(
            f"""\
            name: Deploy
            on:
              workflow_dispatch:
            permissions:
              contents: read
              id-token: write
            jobs:
              deploy:
                runs-on: ubuntu-latest
                environment: {environment}
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{{{ secrets.AZURE_CLIENT_ID }}}}
                      tenant-id: ${{{{ secrets.AZURE_TENANT_ID }}}}
                      subscription-id: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}
                  - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
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


_STRONG_BRANCH_PROTECTION = {
    "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
    "required_status_checks": ["test"],
    "enforce_admins": True,
    "allow_force_pushes": False,
}


def test_unprotected_declared_environment_keeps_codeowners_not_verified(tmp_path):
    root = _write_clean_repo_with_deploy_environment(tmp_path, "production")
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
        "environments": {"production": {"protected": False}},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_protected_declared_environment_confirms_pass(tmp_path):
    root = _write_clean_repo_with_deploy_environment(tmp_path, "production")
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
        "environments": {"production": {"protected": True}},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" not in {f.finding_id for f in result.findings}


def test_missing_environment_evidence_never_blocks_pass_on_its_own(tmp_path):
    """A deploy job that declares an ``environment:`` but live evidence
    supplies no ``environments`` mapping at all must not be blocked by
    that absence alone -- environment protection is confirmed only "as
    available"."""
    root = _write_clean_repo_with_deploy_environment(tmp_path, "production")
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" not in {f.finding_id for f in result.findings}


# ---------------------------------------------------------------------------
# Final blocker 4c: identity separation's live confirmation is bound to
# these workflows' own declared identity references, resolved through a
# `live_azure["identity_principal_ids"]` mapping -- not merely counting
# unrelated role assignments elsewhere in the tenant.
# ---------------------------------------------------------------------------


def _repo_with_separate_deploy_and_test_identities(tmp_path: Path) -> Path:
    root = tmp_path / "identity-repo"
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
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{ secrets.TEST_CLIENT_ID }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    (workflow_dir / "deploy.yml").write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              workflow_dispatch:
            permissions:
              contents: read
              id-token: write
            jobs:
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{ secrets.DEPLOY_CLIENT_ID }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    return root


def test_identity_separation_stays_not_verified_without_live_mapping(tmp_path):
    root = _repo_with_separate_deploy_and_test_identities(tmp_path)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-006")
    assert finding.status == "not-verified"
    assert finding.reason_code == "identity-separation-not-verified-statically"


def test_identity_separation_confirms_pass_when_live_mapping_proves_disjoint(tmp_path):
    root = _repo_with_separate_deploy_and_test_identities(tmp_path)
    live_azure = {
        "identity_principal_ids": {
            "${{ secrets.TEST_CLIENT_ID }}": "11111111-1111-1111-1111-111111111111",
            "${{ secrets.DEPLOY_CLIENT_ID }}": "22222222-2222-2222-2222-222222222222",
        }
    }
    result = assess_change_plane(root, live_github=None, live_azure=live_azure)
    assert "GHCP-006" not in {f.finding_id for f in result.findings}


def test_identity_separation_must_fix_when_live_mapping_proves_shared_principal(
    tmp_path,
):
    """Static files declare no *literal* overlapping reference (the
    secrets expressions differ), but live evidence resolves both to the
    very same underlying Azure principal -- that is a real, live-proven
    shared identity, and must become a `must-fix`, not merely stay
    not-verified."""
    root = _repo_with_separate_deploy_and_test_identities(tmp_path)
    live_azure = {
        "identity_principal_ids": {
            "${{ secrets.TEST_CLIENT_ID }}": "11111111-1111-1111-1111-111111111111",
            "${{ secrets.DEPLOY_CLIENT_ID }}": "11111111-1111-1111-1111-111111111111",
        }
    }
    result = assess_change_plane(root, live_github=None, live_azure=live_azure)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-006")
    assert finding.status == "must-fix"
    assert finding.reason_code == "live-confirmed-shared-identity"


def test_identity_separation_stays_not_verified_when_a_ref_is_unmapped(tmp_path):
    root = _repo_with_separate_deploy_and_test_identities(tmp_path)
    live_azure = {
        "identity_principal_ids": {
            "${{ secrets.TEST_CLIENT_ID }}": "11111111-1111-1111-1111-111111111111",
            # DEPLOY_CLIENT_ID left unmapped -- insufficient live evidence.
        }
    }
    result = assess_change_plane(root, live_github=None, live_azure=live_azure)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-006")
    assert finding.status == "not-verified"


# ---------------------------------------------------------------------------
# Spec-gap fix 4: eval-suite discovery requires the exact runner directory
# path as a whole token in a `run:` command -- never a bare substring match
# on the word "evals" anywhere in the document.
# ---------------------------------------------------------------------------


def _repo_with_eval_suite(
    tmp_path: Path, *, eval_relative_dir: str, ci_extra_run: str = ""
) -> Path:
    root = tmp_path / "eval-repo"
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        textwrap.dedent(
            f"""\
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
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
                      {ci_extra_run}
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
    eval_dir = root / eval_relative_dir
    eval_dir.mkdir(parents=True)
    (eval_dir / "test_eval.py").write_text("def test_eval(): pass\n", encoding="utf-8")
    return root


def test_eval_suite_without_exact_runner_reference_is_must_fix(tmp_path):
    root = _repo_with_eval_suite(tmp_path, eval_relative_dir="evals")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert "GHCP-003" in {f.finding_id for f in result.findings}
    finding = next(f for f in result.findings if f.finding_id == "GHCP-003")
    assert finding.reason_code == "missing-eval-suite-runner"


def test_bare_word_evals_substring_never_satisfies_exact_runner_check(tmp_path):
    """A run command that merely contains the letters "evals" as part of a
    longer token (never the exact directory path) must not satisfy rule 3's
    eval-suite-runner requirement."""
    root = _repo_with_eval_suite(
        tmp_path, eval_relative_dir="evals", ci_extra_run="python evals_helper.py"
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert "GHCP-003" in {f.finding_id for f in result.findings}


def test_top_level_eval_suite_with_exact_runner_reference_passes(tmp_path):
    root = _repo_with_eval_suite(
        tmp_path, eval_relative_dir="evals", ci_extra_run="pytest evals"
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert "GHCP-003" not in {f.finding_id for f in result.findings}


def test_nested_eval_suite_requires_full_path_not_just_leaf_name(tmp_path):
    root = _repo_with_eval_suite(
        tmp_path, eval_relative_dir="src/evals", ci_extra_run="pytest evals"
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    # "evals" alone never satisfies a nested "src/evals" eval-suite
    # directory -- the exact repo-relative path is required.
    assert "GHCP-003" in {f.finding_id for f in result.findings}


def test_nested_eval_suite_with_exact_path_reference_passes(tmp_path):
    root = _repo_with_eval_suite(
        tmp_path, eval_relative_dir="src/evals", ci_extra_run="pytest src/evals"
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert "GHCP-003" not in {f.finding_id for f in result.findings}


# ---------------------------------------------------------------------------
# Spec-gap fix 5: least privilege rejects write-all in spirit, not just the
# literal string -- too many individually declared write scopes, or
# `actions: write` at all, is functionally equivalent to `write-all`.
# ---------------------------------------------------------------------------


def _workflow_with_permissions(tmp_path: Path, permissions_yaml: str) -> Path:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflow_dir / "ci.yml"
    workflow_path.write_text(
        textwrap.dedent(
            f"""\
            name: CI
            on:
              pull_request:
            permissions:
              {permissions_yaml}
            jobs:
              build:
                runs-on: ubuntu-latest
                # Rule 6 requires this job's own explicit permissions
                # independently of the workflow-level value under test
                # above -- a trivially safe, fixed grant here so each
                # test below exercises only the workflow-level value.
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    return workflow_path


def test_actions_write_permission_is_must_fix_even_alone(tmp_path):
    workflow = _workflow_with_permissions(tmp_path, "actions: write")
    result = assess_workflow(workflow)
    assert result.permissions == "must-fix"


def test_more_than_two_write_scopes_is_must_fix(tmp_path):
    workflow = _workflow_with_permissions(
        tmp_path,
        "contents: write\n              packages: write\n              issues: write",
    )
    result = assess_workflow(workflow)
    assert result.permissions == "must-fix"


def test_two_write_scopes_stays_within_least_privilege(tmp_path):
    workflow = _workflow_with_permissions(
        tmp_path, "contents: write\n              packages: write"
    )
    result = assess_workflow(workflow)
    assert result.permissions == "pass"


def test_id_token_write_never_counts_as_a_risk_scope(tmp_path):
    workflow = _workflow_with_permissions(
        tmp_path, "contents: read\n              id-token: write"
    )
    result = assess_workflow(workflow)
    assert result.permissions == "pass"


# ---------------------------------------------------------------------------
# Spec-gap fix 6: CTK/application-probe presence is judged only from actual
# `run:` command text -- a step *name* (or a YAML comment) mentioning "CTK"
# or "application probe" is never evidence that a step actually runs one.
# ---------------------------------------------------------------------------


def test_ctk_mentioned_only_in_step_name_is_must_fix(tmp_path):
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
              test:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK and application probe
                    run: echo "nothing real runs here"
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.ci_probes == "must-fix"


def test_ctk_mentioned_only_in_comment_is_must_fix(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "ci.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            # This workflow intentionally omits CTK and application-probe runs.
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
                  - name: Build
                    run: echo build
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.ci_probes == "must-fix"


def test_ctk_and_probe_in_actual_run_command_is_pass(tmp_path):
    workflow = pinned_oidc_workflow(
        tmp_path, action_sha="0ad4c47a9e566829e19b6099ee3458ac923f5d3c"
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "pass"


# ---------------------------------------------------------------------------
# Spec-gap fix 7: evidence never fabricates `repository`/`source_commit` --
# a real git checkout resolves genuine values; a non-git directory omits
# the evidence entry entirely rather than filling in a placeholder.
# ---------------------------------------------------------------------------


def test_non_git_root_yields_no_fabricated_evidence(tmp_path):
    root = _write_clean_repo(tmp_path)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.evidence == ()


def test_fixture_without_its_own_git_checkout_yields_no_evidence():
    """`unprotected-ghcp` has no `.git` of its own. Provenance evidence
    must never be attributed to whatever ancestor repository happens to
    contain the fixture on disk (this development repo itself) -- only a
    target that is genuinely its own git checkout root can ever produce
    evidence."""
    root = FIXTURES_DIR / "unprotected-ghcp"
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.evidence == ()


def _init_git_repo(root: Path) -> str:
    """Turn ``root`` into a real, standalone git repository with a single
    commit of its current contents, returning that commit's full SHA."""
    run = lambda *args: subprocess.run(  # noqa: E731 - local test helper
        args, cwd=root, capture_output=True, text=True, check=True
    )
    run("git", "init", "--quiet")
    run("git", "config", "user.email", "ghcp-test@example.com")
    run("git", "config", "user.name", "GHCP Test")
    run(
        "git",
        "remote",
        "add",
        "origin",
        "https://github.com/octo-org/octo-repo.git",
    )
    run("git", "add", "-A")
    run("git", "commit", "--quiet", "-m", "initial commit")
    return run("git", "rev-parse", "HEAD").stdout.strip()


def test_genuine_standalone_git_checkout_resolves_real_repository_and_commit(
    tmp_path,
):
    """A target directory that *is* its own git checkout root -- unlike
    the `unprotected-ghcp` fixture above -- still resolves genuine,
    non-fabricated `repository`/`source_commit` evidence."""
    root = _write_clean_repo(tmp_path)
    actual_head = _init_git_repo(root)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert len(result.evidence) == 1
    workflow_evidence = result.evidence[0]
    assert workflow_evidence.source_commit == actual_head
    assert workflow_evidence.source_commit != "0" * 40
    assert "/" in workflow_evidence.repository
    assert workflow_evidence.repository != root.resolve().name


# ---------------------------------------------------------------------------
# Spec-gap fix 8: GHCP-001..006 catalog entries are reconciled to the
# approved fixed taxonomy, and every finding `assess_change_plane` actually
# emits for those ids stays consistent with the catalog's plane and
# gating severity -- the two sources must never silently drift apart.
# ---------------------------------------------------------------------------

_EXPECTED_GHCP_CATALOG_SUMMARIES = {
    "GHCP-001": "Protected branch accepts agent changes outside pull requests.",
    "GHCP-002": (
        "CODEOWNERS, ruleset/branch protection, or required-check coverage "
        "is missing or unavailable."
    ),
    "GHCP-003": "Required CI omits CTK, application probes, or relevant evals.",
    "GHCP-004": "Action SHA floats or workflow permission is excessive.",
    "GHCP-005": "Azure deployment uses a long-lived secret instead of OIDC/WIF.",
    "GHCP-006": (
        "Build/test/deploy identities are shared, over-broad, or not evidenced."
    ),
}

# The catalog's `severity` can only ever be a single "must-fix"/"should-fix"
# string (see `tests/test_contracts.py`), but the approved master taxonomy
# allows GHCP-002/GHCP-005/GHCP-006 to resolve to a softer `not-verified`
# status when only local evidence is available (an Azure deploy with no
# visible `azure/login` step is never itself proof of OIDC/WIF, so GHCP-005
# must be able to surface `not-verified` too); every other GHCP id may only
# ever emit `must-fix`.
_ALLOWED_GHCP_STATUSES = {
    "GHCP-001": {"must-fix"},
    "GHCP-002": {"must-fix", "not-verified"},
    "GHCP-003": {"must-fix"},
    "GHCP-004": {"must-fix"},
    "GHCP-005": {"must-fix", "not-verified"},
    "GHCP-006": {"must-fix", "not-verified"},
}


def _load_ghcp_catalog_entries():
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {
        entry["finding_id"]: entry
        for entry in catalog["findings"]
        if entry["finding_id"].startswith("GHCP-")
    }


def test_ghcp_catalog_entries_match_approved_taxonomy():
    entries = _load_ghcp_catalog_entries()
    assert set(entries) == set(_EXPECTED_GHCP_CATALOG_SUMMARIES)
    for finding_id, expected_summary in _EXPECTED_GHCP_CATALOG_SUMMARIES.items():
        entry = entries[finding_id]
        assert entry["plane"] == "change"
        assert entry["severity"] == "must-fix"
        assert entry["gate"] is True
        assert entry["summary"] == expected_summary


def test_ghcp_findings_never_drift_from_catalog_plane_and_status(fixture_root):
    catalog_entries = _load_ghcp_catalog_entries()
    result = assess_change_plane(
        fixture_root / "unprotected-ghcp", live_github=None, live_azure=None
    )
    ghcp_findings = [f for f in result.findings if f.finding_id.startswith("GHCP-")]
    assert ghcp_findings  # sanity: the unprotected fixture emits every GHCP id
    for finding in ghcp_findings:
        catalog_entry = catalog_entries[finding.finding_id]
        assert finding.plane == catalog_entry["plane"]
        assert finding.status in _ALLOWED_GHCP_STATUSES[finding.finding_id]


# ---------------------------------------------------------------------------
# Final spec-gap fix 1: the SHA boundary tests above now use a *true*
# 39-character lowercase string and a *true* 40-character uppercase string
# (previously off-by-one and off-by-one respectively) -- covered by the
# corrected `test_39_character_sha_is_rejected` /
# `test_40_character_uppercase_sha_is_rejected` earlier in this file.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Final spec-gap fix 2: rule 5's secret-credential scan covers every Azure
# deployment action (`azure/webapps-deploy`'s `publish-profile`, `azure/arm-
# deploy`'s `creds`, ...), not only `azure/login` -- a deploy action can
# authenticate directly with its own secret input without any `azure/login`
# step ever appearing in the workflow at all.
# ---------------------------------------------------------------------------


def test_publish_profile_on_webapps_deploy_without_login_step_is_must_fix(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Deploy to Azure Web App
                    uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      app-name: my-app
                      publish-profile: ${{ secrets.AZURE_PUBLISH_PROFILE }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif == "must-fix"


def test_creds_on_arm_deploy_without_login_step_is_must_fix(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Deploy ARM template
                    uses: azure/arm-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      creds: ${{ secrets.AZURE_CREDENTIALS }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif == "must-fix"


def test_deploy_action_without_secret_input_and_no_login_step_stays_not_verified(
    tmp_path,
):
    """A deploy action step with no secret-shaped input at all (relying on
    a preceding job's already-federated credentials, or on the runner's own
    environment) and no `azure/login` step anywhere is still a genuine
    Azure deployment with no visible evidence of *how* it authenticates --
    that absence of a secret is not itself proof of OIDC/WIF, so rule 5
    can never infer a `pass` here; it stays `not-verified`."""
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Deploy to Azure Web App
                    uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      app-name: my-app
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif == "not-verified"


# ---------------------------------------------------------------------------
# Final spec-gap fix 3: explicit least-privilege permissions are required at
# the workflow level *and* independently on every job -- a sibling job's own
# explicit declaration is never evidence for a job that omits its own and
# silently inherits the workflow-level default.
# ---------------------------------------------------------------------------


def test_sibling_job_without_own_permissions_is_must_fix_even_if_another_job_is_explicit(
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
              explicit:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              implicit:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.permissions == "must-fix"


def test_every_job_explicit_but_workflow_level_missing_is_must_fix(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "ci.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: CI
            on:
              pull_request:
            jobs:
              build:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.permissions == "must-fix"


def test_workflow_and_every_job_explicit_least_privilege_is_pass(tmp_path):
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
              one:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              two:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  packages: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.permissions == "pass"


# ---------------------------------------------------------------------------
# Final spec-gap fix 4: CODEOWNERS coverage understands a repository-wide
# catch-all glob, a broader ancestor directory glob subsuming a narrower
# required one, and a cosmetic leading-slash root anchor -- never only
# literal string equality -- while still requiring every governance
# surface to actually be covered by *some* declared pattern.
# ---------------------------------------------------------------------------


def _repo_with_codeowners(tmp_path: Path, codeowners_lines: Sequence[str]) -> Path:
    """A minimal repo that is clean on every GHCP control except CODEOWNERS
    coverage, whose declared patterns this helper's caller controls."""
    root = tmp_path / "codeowners-repo"
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
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    (root / "CODEOWNERS").write_text(
        "\n".join(list(codeowners_lines) + [""]), encoding="utf-8"
    )
    return root


def _codeowners_finding(result: ChangePlaneResult) -> Finding:
    return next(f for f in result.findings if f.finding_id == "GHCP-002")


def test_codeowners_leading_slash_patterns_still_satisfy_coverage(tmp_path):
    root = _repo_with_codeowners(
        tmp_path,
        [
            "/src/governance/** @octo-org/governance",
            "/policies/** @octo-org/governance",
            "/tests/** @octo-org/governance",
            "/.github/workflows/governed-actions.yml @octo-org/governance",
            "/tests/governed-actions-manifest.json @octo-org/governance",
            "/tests/governed-actions-apply-plan.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    # Coverage is fully satisfied, so absent live evidence the control can
    # only land on "not-verified" -- it must never be "must-fix" simply
    # because every declared line happens to carry a leading slash.
    assert _codeowners_finding(result).status == "not-verified"


def test_codeowners_catch_all_glob_satisfies_every_required_surface(tmp_path):
    root = _repo_with_codeowners(tmp_path, ["* @octo-org/governance"])
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "not-verified"


def test_codeowners_broader_ancestor_glob_subsumes_nested_file_requirements(tmp_path):
    """A single `tests/**` line subsumes both explicit governed-actions
    JSON-file requirements underneath it, and a single `src/**` line
    subsumes the narrower `src/governance/**` requirement -- neither needs
    its own redundant, separately declared line."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "not-verified"


def test_codeowners_single_level_glob_does_not_satisfy_recursive_requirement(tmp_path):
    """`tests/*` only reaches one path segment deep; it must never be
    treated as covering the fully recursive `tests/**` requirement."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/* @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert "GHCP-002" in {f.finding_id for f in result.findings}


def test_codeowners_unrelated_patterns_still_flagged_missing(tmp_path):
    """Recognizing glob coverage must never become so lenient that an
    unrelated CODEOWNERS still silently passes -- every governance surface
    must still be covered by some declared pattern."""
    root = _repo_with_codeowners(tmp_path, ["docs/** @octo-org/docs"])
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert "GHCP-002" in {f.finding_id for f in result.findings}
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"


# ---------------------------------------------------------------------------
# Quality-fix 1: rule 5's secret-credential scan also covers `run:` commands
# and `env:` blocks directly -- a raw `az login --password`/
# `--service-principal-secret` shell command, a raw `publish-profile` deploy
# invocation, or a secret-shaped `AZURE_`/`ARM_` env var name -- never just
# an action's declared `with:` inputs. Every check matches names/flags only,
# never a credential's actual value.
# ---------------------------------------------------------------------------


def test_raw_az_login_password_flag_in_run_command_is_must_fix(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              deploy:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Azure login
                    run: |
                      az login --service-principal --username $ARM_CLIENT_ID --password ${{ secrets.ARM_CLIENT_SECRET }} --tenant $ARM_TENANT_ID
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif == "must-fix"


def test_secret_shaped_env_var_name_alone_is_must_fix_without_login_step(tmp_path):
    """A secret-shaped Azure/ARM env var name is rejected even with no
    `azure/login` step or deploy action anywhere in the workflow -- the
    secret itself is the change-plane risk."""
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              deploy:
                runs-on: ubuntu-latest
                env:
                  ARM_CLIENT_SECRET: ${{ secrets.ARM_CLIENT_SECRET }}
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - run: echo deploying
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif == "must-fix"


def test_raw_publish_profile_run_command_without_deploy_action_is_must_fix(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              deploy:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - run: az webapp deploy --publish-profile-file profile.publishsettings
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif == "must-fix"


def test_secret_env_var_detection_never_leaks_the_actual_secret_value(tmp_path):
    """The scan flags a secret-shaped env var *name*; the literal secret
    value written into the workflow file must never surface anywhere in
    the finding text this module produces."""
    root = tmp_path / "leak-check-repo"
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "deploy.yml").write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              deploy:
                runs-on: ubuntu-latest
                env:
                  AZURE_CLIENT_SECRET: totally-real-hunter2-secret-value
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - run: echo deploying
            """
        ),
        encoding="utf-8",
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-005")
    assert "totally-real-hunter2-secret-value" not in finding.details
    assert "totally-real-hunter2-secret-value" not in " ".join(finding.affected_paths)


# ---------------------------------------------------------------------------
# Final blocker: `az login`'s exact short-flag password form
# (`--service-principal ... -p SECRET`) is just as much a long-lived
# secret login as the long `--password`/`--service-principal-secret`
# flags, and must be detected the same way -- plus a handful of
# additional bare canonical secret env var names beyond the existing
# `AZURE_`/`ARM_`-prefixed set, without ever matching an unrelated flag
# or env var name that merely resembles one.
# ---------------------------------------------------------------------------


def test_az_login_short_password_flag_in_run_command_is_must_fix(tmp_path):
    workflow_path = _write_workflow(
        tmp_path,
        "deploy.yml",
        """\
        name: Deploy
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          deploy:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - name: Azure login
                run: |
                  az login --service-principal -u $ARM_CLIENT_ID -p ${{ secrets.ARM_CLIENT_SECRET }} --tenant $ARM_TENANT_ID
        """,
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif == "must-fix"


def test_az_login_unrelated_dash_p_prefixed_flag_is_not_flagged(tmp_path):
    """`--profile`/`--param`-style long flags that merely contain the
    substring "-p" must never be misread as the short password flag."""
    workflow_path = _write_workflow(
        tmp_path,
        "deploy.yml",
        """\
        name: Deploy
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          deploy:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - name: Azure login
                run: |
                  az login --service-principal -u $ARM_CLIENT_ID --profile prod --param extra
        """,
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif != "must-fix"


def test_az_login_short_flag_belonging_to_a_different_chained_command_is_not_flagged(
    tmp_path,
):
    """A `-p` flag on a *different*, `&&`-chained command sharing the same
    `run:` line as an unrelated `az login --identity` call must not be
    misattributed to `az login` as its own secret flag."""
    workflow_path = _write_workflow(
        tmp_path,
        "deploy.yml",
        """\
        name: Deploy
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          deploy:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - name: Azure login
                run: |
                  az login --identity && curl -o out.txt -p extra-flag
        """,
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif != "must-fix"


@pytest.mark.parametrize(
    "env_var_name",
    ["SP_PASSWORD", "CLIENT_SECRET", "SERVICE_PRINCIPAL_SECRET"],
)
def test_broadened_secret_env_var_name_alone_is_must_fix(tmp_path, env_var_name):
    root = tmp_path / f"broadened-secret-{env_var_name.lower()}"
    _write_workflow(
        root,
        "deploy.yml",
        f"""\
        name: Deploy
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          deploy:
            runs-on: ubuntu-latest
            env:
              {env_var_name}: ${{{{ secrets.{env_var_name} }}}}
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - run: echo deploying
        """,
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-005")
    assert finding.status == "must-fix"


def test_broadened_secret_env_var_name_never_leaks_the_actual_value(tmp_path):
    root = tmp_path / "broadened-secret-leak-check"
    _write_workflow(
        root,
        "deploy.yml",
        """\
        name: Deploy
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          deploy:
            runs-on: ubuntu-latest
            env:
              SP_PASSWORD: totally-real-hunter2-secret-value
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - run: echo deploying
        """,
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-005")
    assert "totally-real-hunter2-secret-value" not in finding.details
    assert "totally-real-hunter2-secret-value" not in " ".join(finding.affected_paths)


def test_unrelated_env_var_name_merely_containing_client_secret_substring_not_flagged(
    tmp_path,
):
    """A prefixed name that is not itself one of the exact canonical
    secret names (bare, or `AZURE_`/`ARM_`-prefixed) must not be flagged
    -- this scan only ever recognizes the precise canonical shapes, never
    a loose substring match that would sweep in unrelated identifiers."""
    root = tmp_path / "unrelated-client-secret-substring"
    _write_workflow(
        root,
        "deploy.yml",
        """\
        name: Deploy
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          deploy:
            runs-on: ubuntu-latest
            env:
              MY_CLIENT_SECRETARY_CONTACT: not-a-secret-at-all
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - run: echo deploying
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "deploy.yml")
    assert result.oidc_wif != "must-fix"


# ---------------------------------------------------------------------------
# Quality-fix 2: rule 3 requires that *at least one* pull_request-triggered
# workflow runs the full required CI (CTK, application probe, and an eval
# suite's exact runner command if one exists) -- not that every single
# PR-triggered workflow does. A benign PR workflow (lint, docs preview, ...)
# alongside a real gating workflow must never fail this check on its own.
# ---------------------------------------------------------------------------


def _write_workflow(root: Path, filename: str, body: str) -> Path:
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    path = workflow_dir / filename
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_benign_pr_workflow_alongside_gating_workflow_still_passes(tmp_path):
    root = tmp_path / "quantifier-repo"
    _write_workflow(
        root,
        "lint.yml",
        """\
        name: Lint
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          lint:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - run: echo lint only, no ctk or probe here
        """,
    )
    _write_workflow(
        root,
        "ci.yml",
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
              - name: Run CTK and application probes
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
        """,
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.controls["ghcp_ci_probes"] == "pass"
    assert "GHCP-003" not in {f.finding_id for f in result.findings}


def test_every_pr_workflow_missing_ctk_or_probe_is_still_must_fix(tmp_path):
    root = tmp_path / "quantifier-all-fail-repo"
    _write_workflow(
        root,
        "lint.yml",
        """\
        name: Lint
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          lint:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - run: echo lint only
        """,
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-003")
    assert finding.reason_code == "missing-ctk-or-application-probe"


def test_eval_suite_runner_required_from_some_ctk_probe_passing_workflow(tmp_path):
    """Even when a PR workflow's `ci_probes` already passes, rule 3 still
    requires *some* CTK/probe-passing PR workflow to also reference the
    repo's eval suite's exact directory -- a passing CTK/probe workflow
    that ignores the eval suite entirely does not satisfy rule 3 on its
    own."""
    root = tmp_path / "quantifier-eval-repo"
    _write_workflow(
        root,
        "ci.yml",
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
              - name: Run CTK and application probes
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
        """,
    )
    eval_dir = root / "evals"
    eval_dir.mkdir(parents=True)
    (eval_dir / "test_eval.py").write_text("def test_eval(): pass\n", encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-003")
    assert finding.reason_code == "missing-eval-suite-runner"


def test_one_workflow_covering_ctk_probe_and_eval_runner_passes_with_benign_sibling(
    tmp_path,
):
    root = tmp_path / "quantifier-eval-pass-repo"
    _write_workflow(
        root,
        "lint.yml",
        """\
        name: Lint
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          lint:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - run: echo lint only, no ctk or probe here
        """,
    )
    _write_workflow(
        root,
        "ci.yml",
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
              - name: Run CTK, application probes, and evals
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
                  pytest evals
        """,
    )
    eval_dir = root / "evals"
    eval_dir.mkdir(parents=True)
    (eval_dir / "test_eval.py").write_text("def test_eval(): pass\n", encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.controls["ghcp_ci_probes"] == "pass"
    assert "GHCP-003" not in {f.finding_id for f in result.findings}


# ---------------------------------------------------------------------------
# Quality-fix 3: a CODEOWNERS pattern with no real owner token confers no
# ownership coverage; `docs/CODEOWNERS` is recognized alongside the root and
# `.github/` locations GitHub itself supports.
# ---------------------------------------------------------------------------


def test_codeowners_pattern_without_owner_token_grants_no_coverage(tmp_path):
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/**",  # no owner token at all
            "policies/**",
            "tests/**",
            ".github/workflows/governed-actions.yml",
            "tests/governed-actions-manifest.json",
            "tests/governed-actions-apply-plan.json",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"
    assert finding.reason_code == "codeowners-incomplete-coverage"


# ---------------------------------------------------------------------------
# Final blocker 3: CODEOWNERS resolution is last-match-wins in file order,
# exactly like GitHub's own -- a later line covering the same path (even an
# ownerless one that explicitly disowns it) must override an earlier one,
# never the reverse.
# ---------------------------------------------------------------------------


def test_codeowners_later_owned_line_overrides_earlier_ownerless_line(tmp_path):
    """An earlier ownerless (disowning) line for a broad ancestor glob must
    not defeat a *later*, more specific line that does declare a real
    owner for the same path -- last match wins, so the later owned line
    is what actually governs."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/**",  # ownerless: would disown everything under it
            "src/governance/** @octo-org/governance",  # later: re-owns it
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    # Coverage itself is satisfied by the later, re-owning line; the only
    # remaining GHCP-002 gap is live branch-protection confirmation, which
    # static evidence alone can never supply.
    assert finding.status == "not-verified"
    assert finding.reason_code != "codeowners-incomplete-coverage"


def test_codeowners_later_ownerless_line_disowns_earlier_owned_line(tmp_path):
    """The reverse of the case above: a later ownerless line covering a
    path that an earlier line *did* assign a real owner to must actually
    take effect -- an explicit, later "no one owns this" declaration is
    real CODEOWNERS semantics, not a no-op, and must still surface as
    missing coverage for that requirement."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",  # owned...
            "src/governance/**",  # ...then explicitly disowned later
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"
    assert finding.reason_code == "codeowners-incomplete-coverage"
    assert "src/governance/**" in finding.details


def test_docs_codeowners_location_is_a_recognized_ownership_file(tmp_path):
    root = tmp_path / "docs-codeowners-repo"
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
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "CODEOWNERS").write_text(
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
    result = assess_change_plane(root, live_github=None, live_azure=None)
    # Static coverage is fully satisfied via `docs/CODEOWNERS` -- absent
    # live branch-protection evidence, the finding can only ever land on
    # "not-verified", never the "must-fix" a wrongly-unrecognized
    # ownership file location would produce.
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"
    assert finding.reason_code == "branch-protection-not-verified-statically"


# ---------------------------------------------------------------------------
# Quality-fix 4: rule 1's direct-push finding exempts a push trigger
# restricted to tags only (`on.push.tags`/`tags-ignore` with no
# `branches`/`branches-ignore` filter) -- a tag push can never itself
# deliver an unreviewed change to a protected branch. Any `branches` filter
# (with or without a `tags` filter alongside it) keeps the finding.
# ---------------------------------------------------------------------------


def _push_deploy_workflow(tmp_path: Path, on_push_lines: Sequence[str]) -> Path:
    """``on_push_lines`` are already fully indented ``push:`` sub-mapping
    lines (e.g. ``"    tags:"``, ``"      - 'v*'"``), written verbatim
    beneath ``on:\\n  push:`` -- built with plain concatenation rather
    than a shared ``textwrap.dedent`` block, since mixing differently-
    indented literal and templated text under one shared dedent silently
    breaks the common-prefix calculation.
    """
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflow_dir / "deploy.yml"
    text = "\n".join(
        [
            "name: Deploy",
            "on:",
            "  push:",
            *on_push_lines,
            "permissions:",
            "  contents: read",
            "jobs:",
            "  deploy:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c",
            "",
        ]
    )
    workflow_path.write_text(text, encoding="utf-8")
    return workflow_path


def test_push_trigger_restricted_to_tags_only_is_exempt(tmp_path):
    workflow = _push_deploy_workflow(tmp_path, ["    tags:", "      - 'v*'"])
    result = assess_workflow(workflow)
    assert result.pr_gate == "pass"


def test_push_trigger_with_branches_filter_is_still_flagged(tmp_path):
    workflow = _push_deploy_workflow(tmp_path, ["    branches:", "      - main"])
    result = assess_workflow(workflow)
    assert result.pr_gate == "must-fix"


def test_push_trigger_with_both_tags_and_branches_is_still_flagged(tmp_path):
    workflow = _push_deploy_workflow(
        tmp_path,
        ["    tags:", "      - 'v*'", "    branches:", "      - main"],
    )
    result = assess_workflow(workflow)
    assert result.pr_gate == "must-fix"


def test_bare_push_trigger_with_no_filters_is_still_flagged(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on: push
            permissions:
              contents: read
            jobs:
              deploy:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


# ---------------------------------------------------------------------------
# Quality-fix 5 (adversarial): a negation cue or a detection/inspection
# command preceding a bypass marker on the same line means the line talks
# *about* the bypass rather than issuing it; a generic `--force`/`--admin`
# flag unrelated to `git push`/`gh pr merge` is never itself a bypass.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run_command",
    [
        "echo 'this pipeline must never bypass required checks'",
        "echo 'reviewers should not admin-merge this repository'",
        "grep -rq '\\[skip ci\\]' CHANGELOG.md && echo marker-present",
    ],
)
def test_negation_or_detection_context_bypass_text_is_not_flagged(tmp_path, run_command):
    workflow = _workflow_with_run_command(tmp_path, run_command)
    result = assess_workflow(workflow)
    assert result.pr_gate == "pass"


@pytest.mark.parametrize(
    "run_command",
    [
        "docker rm --force my-container",
        "rm --force /tmp/scratch-file",
        "aws iam create-user --user-name admin-bot",
    ],
)
def test_generic_force_or_admin_flag_outside_bound_context_is_not_flagged(
    tmp_path, run_command
):
    workflow = _workflow_with_run_command(tmp_path, run_command)
    result = assess_workflow(workflow)
    assert result.pr_gate == "pass"


# ---------------------------------------------------------------------------
# Quality-fix 6: `id-token: write` must be granted in the *same job* as the
# `azure/login` step it backs -- a sibling job granting it (or a workflow-
# level default a job's own permissions block overrides away) is never
# evidence that this job's login step can actually mint an OIDC token.
# ---------------------------------------------------------------------------


def test_id_token_write_in_sibling_job_only_is_must_fix(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              grant-id-token-elsewhere:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - run: echo unrelated job
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{ secrets.AZURE_CLIENT_ID }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif == "must-fix"


def test_id_token_write_inherited_from_workflow_default_in_same_job_passes(tmp_path):
    """A login job with no permissions block of its own inherits the
    workflow-level default -- if that default grants `id-token: write`,
    the login step's own (only) job does carry the grant, so this passes."""
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
              id-token: write
            jobs:
              other:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - run: echo unrelated job with its own explicit permissions
              deploy:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{ secrets.AZURE_CLIENT_ID }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif == "pass"


# ---------------------------------------------------------------------------
# Quality-fix 7: `affected_paths` are always repository-relative, never the
# absolute filesystem path this module happened to read a file from.
# ---------------------------------------------------------------------------


def test_affected_paths_are_repository_relative(fixture_root):
    root = fixture_root / "unprotected-ghcp"
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.findings, "expected at least one finding to inspect"
    resolved_root = str(root.resolve())
    for finding in result.findings:
        for affected_path in finding.affected_paths:
            assert not affected_path.startswith("/"), affected_path
            assert resolved_root not in affected_path, affected_path
            assert not Path(affected_path).is_absolute(), affected_path


# ---------------------------------------------------------------------------
# Quality-fix 8: workflow-set evidence accounts for a dirty working tree --
# an uncommitted change to a workflow file means its on-disk bytes are no
# longer what the recorded `source_commit` actually contains, so evidence
# must be omitted rather than paired with a commit it doesn't match.
# ---------------------------------------------------------------------------


def test_dirty_workflow_file_yields_no_evidence(tmp_path):
    root = _write_clean_repo(tmp_path)
    _init_git_repo(root)
    # Modify the already-committed workflow file without committing again.
    workflow_path = root / ".github" / "workflows" / "ci.yml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8") + "\n# an uncommitted local edit\n",
        encoding="utf-8",
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.evidence == ()


def test_clean_working_tree_still_yields_evidence(tmp_path):
    root = _write_clean_repo(tmp_path)
    _init_git_repo(root)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert len(result.evidence) == 1


# ---------------------------------------------------------------------------
# Final blocker 6: a repository with no discovered workflows at all must
# never resolve GHCP-001/GHCP-004/GHCP-005 as a vacuous `pass` -- there is
# nothing this assessor could have actually checked, so the honest answer
# is "not-applicable", exactly like GHCP-006 already does when there is no
# identity to compare at all.
# ---------------------------------------------------------------------------


def test_empty_workflow_set_never_resolves_pr_gate_to_pass(tmp_path):
    root = tmp_path / "no-workflows-repo"
    root.mkdir()
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.controls["ghcp_pr_only_gate"] == "not-applicable"


def test_empty_workflow_set_never_resolves_pinned_least_privilege_to_pass(tmp_path):
    root = tmp_path / "no-workflows-repo"
    root.mkdir()
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.controls["ghcp_pinned_least_privilege"] == "not-applicable"


def test_empty_workflow_set_never_resolves_azure_oidc_to_pass(tmp_path):
    root = tmp_path / "no-workflows-repo"
    root.mkdir()
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.controls["ghcp_azure_oidc"] == "not-applicable"


# ---------------------------------------------------------------------------
# Final blocker 5: the dirty check must also catch a *staged but
# uncommitted* change -- the index recording something different from what
# is actually committed at HEAD -- not only an unstaged working-tree-vs-
# index difference. Pairing `source_commit` with a hash of staged-but-
# never-committed bytes would misrepresent what that commit actually
# contains.
# ---------------------------------------------------------------------------


def test_staged_but_uncommitted_workflow_change_yields_no_evidence(tmp_path):
    root = _write_clean_repo(tmp_path)
    _init_git_repo(root)
    workflow_path = root / ".github" / "workflows" / "ci.yml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8") + "\n# staged but not committed\n",
        encoding="utf-8",
    )
    # Stage the change -- working tree now matches the index, but the
    # index no longer matches what HEAD actually has committed.
    subprocess.run(
        ["git", "add", "-A"], cwd=root, capture_output=True, text=True, check=True
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.evidence == ()


# ---------------------------------------------------------------------------
# Quality-fix 9: a workflow file this module cannot safely read, parse, or
# bound (oversized, a deeply-nested YAML bomb, a symlink escaping the
# repository, malformed YAML) is converted into a contained, per-file
# finding, never a crash -- and never erases any other workflow's own,
# independently-computed results.
# ---------------------------------------------------------------------------


def test_oversized_workflow_file_is_contained_alongside_a_good_workflow(tmp_path):
    root = tmp_path / "oversized-repo"
    _write_workflow(
        root,
        "ci.yml",
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
              - name: Run CTK and application probes
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
        """,
    )
    huge_path = root / ".github" / "workflows" / "huge.yml"
    huge_path.write_text("# padding\n" + ("x" * (2 * 1024 * 1024)), encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    # The oversized file never crashes the assessment, and the good
    # workflow's own passing CI-probe coverage still satisfies rule 3.
    assert result.controls["ghcp_ci_probes"] == "pass"
    assert "GHCP-003" not in {f.finding_id for f in result.findings}


def test_deeply_nested_yaml_workflow_is_contained_not_crashed(tmp_path):
    root = tmp_path / "yaml-bomb-repo"
    _write_workflow(
        root,
        "ci.yml",
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
              - name: Run CTK and application probes
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
        """,
    )
    bomb_path = root / ".github" / "workflows" / "bomb.yml"
    bomb_path.write_text("bomb: " + ("[" * 150) + ("]" * 150) + "\n", encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.controls["ghcp_ci_probes"] == "pass"
    assert "GHCP-003" not in {f.finding_id for f in result.findings}


def test_symlinked_workflow_file_is_rejected_not_dereferenced(tmp_path):
    root = tmp_path / "symlink-repo"
    _write_workflow(
        root,
        "ci.yml",
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
              - name: Run CTK and application probes
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
        """,
    )
    outside_target = tmp_path / "outside-secret.yml"
    outside_target.write_text("name: Outside\non:\n  push:\n", encoding="utf-8")
    symlink_path = root / ".github" / "workflows" / "linked.yml"
    symlink_path.symlink_to(outside_target)

    result = assess_change_plane(root, live_github=None, live_azure=None)
    # Never crashes, and the good workflow's own CI-probe coverage still
    # satisfies rule 3 despite the rejected symlinked file sitting
    # alongside it.
    assert result.controls["ghcp_ci_probes"] == "pass"
    assert "GHCP-003" not in {f.finding_id for f in result.findings}
    # The rejected symlink still surfaces as a finding somewhere (it is a
    # conservative "must-fix everything" stand-in), never silently ignored.
    assert any(
        "linked.yml" in " ".join(f.affected_paths) for f in result.findings
    )


def test_malformed_yaml_workflow_is_contained_alongside_a_good_workflow(tmp_path):
    root = tmp_path / "malformed-repo"
    _write_workflow(
        root,
        "ci.yml",
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
              - name: Run CTK and application probes
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
        """,
    )
    malformed_path = root / ".github" / "workflows" / "malformed.yml"
    malformed_path.write_text("name: Broken\non: [pull_request\n", encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.controls["ghcp_ci_probes"] == "pass"
    assert "GHCP-003" not in {f.finding_id for f in result.findings}


# ---------------------------------------------------------------------------
# Fresh quality review, item 1: `pull_request_target`'s own `repository:`
# input is just as attacker-controlled as its `ref:` input -- a PR head
# fork checked out at an otherwise-trusted-looking ref hands the fork's own
# content to elevated permissions/secrets exactly the same way. A raw
# `git fetch`/`checkout`/`clone`/`pull` command naming the same untrusted
# content is an equivalent checkout mechanism and must be caught too.
# ---------------------------------------------------------------------------


def test_pull_request_target_untrusted_repository_input_is_must_fix(tmp_path):
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
                      repository: ${{ github.event.pull_request.head.repo.full_name }}
                      ref: main
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_raw_git_fetch_of_untrusted_ref_is_must_fix(tmp_path):
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
                  - name: Fetch PR head directly
                    run: git fetch origin refs/pull/${{ github.event.pull_request.number }}/head
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_trusted_repository_input_stays_pass(tmp_path):
    """A `repository:`/`ref:` pair that both stay literal and trusted (the
    base repository, the base branch) is exactly what `pull_request_target`
    is safe to do -- it must never be flagged merely for supplying a
    `repository:` input at all."""
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
                      repository: octo-org/my-repo
                      ref: main
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "pass"


# ---------------------------------------------------------------------------
# Fresh quality review, item 2: rule 3's CTK/application-probe and eval-
# runner checks must only ever count a real, execution-reachable
# invocation -- never a step name, a shell comment, a bare echo/printf
# line, or a statically (step- or job-level) disabled step.
# ---------------------------------------------------------------------------


def _workflow_with_ci_probe_run(tmp_path: Path, run_body: str, *, job_if: str = "") -> Path:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "ci.yml"
    job_if_line = f"    if: {job_if}\n" if job_if else ""
    text = (
        "name: CI\n"
        "on:\n"
        "  pull_request:\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  test:\n"
        f"{job_if_line}"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c\n"
        "      - name: Run CTK and application probes\n"
        "        run: |\n"
        f"          {run_body}\n"
    )
    workflow_path.write_text(text, encoding="utf-8")
    return workflow_path


def test_ctk_probe_via_echo_only_lines_is_must_fix(tmp_path):
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        'echo "python -m ctk run-vectors"\n                      '
        'echo "python -m probes run-application-probe"',
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_mentioned_only_in_shell_comment_is_must_fix(tmp_path):
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        "# python -m ctk run-vectors\n                      "
        "# python -m probes run-application-probe\n                      "
        "echo done",
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_in_statically_disabled_step_is_must_fix(tmp_path):
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
              test:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK and application probes
                    if: false
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_in_statically_disabled_job_is_must_fix(tmp_path):
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        "python -m ctk run-vectors\n                      "
        "python -m probes run-application-probe",
        job_if="false",
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_with_unrelated_comment_line_still_passes(tmp_path):
    """A genuine invocation is unaffected by an unrelated comment line
    stripped alongside it -- comment-stripping never removes real evidence,
    only inert commentary."""
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        "# Run governance CTK/probe checks\n                      "
        "python -m ctk run-vectors\n                      "
        "python -m probes run-application-probe",
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "pass"


def test_eval_runner_via_echo_only_line_is_must_fix(tmp_path):
    root = _repo_with_eval_suite(
        tmp_path, eval_relative_dir="evals", ci_extra_run="echo pytest evals"
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert "GHCP-003" in {f.finding_id for f in result.findings}


def test_eval_runner_via_disabled_step_is_must_fix(tmp_path):
    root = tmp_path / "eval-disabled-repo"
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
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
                  - name: Run evals
                    if: false
                    run: pytest evals
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
    eval_dir = root / "evals"
    eval_dir.mkdir(parents=True)
    (eval_dir / "test_eval.py").write_text("def test_eval(): pass\n", encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert "GHCP-003" in {f.finding_id for f in result.findings}


# ---------------------------------------------------------------------------
# Fresh quality review, item 3: a live required-status-check list must bind
# to the *specific* job(s) whose own steps actually run CTK/application
# probes -- never any other job that merely happens to live in the same
# workflow file.
# ---------------------------------------------------------------------------


def _repo_with_gating_and_sibling_job(tmp_path: Path) -> Path:
    root = tmp_path / "multi-job-repo"
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
              lint:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - run: echo lint only, no ctk or probe here
              test:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
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


def test_required_status_check_naming_sibling_non_gating_job_stays_not_verified(
    tmp_path,
):
    root = _repo_with_gating_and_sibling_job(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["lint"],
                "enforce_admins": True,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" in {f.finding_id for f in result.findings}
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_required_status_check_naming_actual_gating_job_confirms_pass(tmp_path):
    root = _repo_with_gating_and_sibling_job(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["test"],
                "enforce_admins": True,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert result.findings == ()


# ---------------------------------------------------------------------------
# Fresh quality review, item 4: real, API-shaped branch-protection evidence
# must explicitly turn on CODEOWNER review and require a positive approving-
# review count -- a `required_pull_request_reviews` mapping present for some
# other reason is never itself proof reviews are actually required. A bare
# boolean (whether from a simplified test fixture or otherwise) is never
# itself structured evidence and must always be rejected.
# ---------------------------------------------------------------------------


def _strong_protection_rule(**overrides: object) -> dict:
    rule = {
        "required_pull_request_reviews": {
            "require_code_owner_reviews": True,
            "required_approving_review_count": 1,
        },
        "enforce_admins": True,
        "allow_force_pushes": False,
        "required_status_checks": ["test"],
    }
    rule.update(overrides)
    return rule


def test_branch_protection_mapping_without_codeowner_review_stays_not_verified(
    tmp_path,
):
    root = _write_clean_repo(tmp_path)
    reviews = {"required_approving_review_count": 1}
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _strong_protection_rule(
            required_pull_request_reviews=reviews
        )},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" in {f.finding_id for f in result.findings}
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_branch_protection_mapping_with_zero_required_approvals_stays_not_verified(
    tmp_path,
):
    root = _write_clean_repo(tmp_path)
    reviews = {"require_code_owner_reviews": True, "required_approving_review_count": 0}
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _strong_protection_rule(
            required_pull_request_reviews=reviews
        )},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" in {f.finding_id for f in result.findings}
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_branch_protection_real_api_shape_with_codeowner_review_confirms_pass(
    tmp_path,
):
    root = _write_clean_repo(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _strong_protection_rule()},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert result.findings == ()


def test_branch_protection_bare_boolean_form_is_rejected_never_false_pass(tmp_path):
    """A simplified/legacy bare `True` for `required_pull_request_reviews`
    (rather than GitHub's own nested object shape) must never be accepted
    as sufficient live evidence -- it carries no proof CODEOWNER review or
    a positive required-approval count are actually turned on, and must
    always fail closed to `not-verified`, never an inferred `pass`."""
    root = _write_clean_repo(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": True,
                "required_status_checks": ["test"],
                "enforce_admins": True,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" in {f.finding_id for f in result.findings}
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


# ---------------------------------------------------------------------------
# Fresh quality review, item 5: an Azure deploy with no explicit
# azure/login/OIDC evidence at all -- including a raw `az`/`azd` CLI deploy
# command rather than a marketplace deploy action -- must stay
# not-verified, never an inferred `pass`.
# ---------------------------------------------------------------------------


def test_raw_az_cli_deploy_without_login_step_stays_not_verified(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              ship:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Deploy via CLI
                    run: az webapp deploy --resource-group rg --name my-app
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.oidc_wif == "not-verified"


# ---------------------------------------------------------------------------
# Fresh quality review, item 6: an inline (non-`${{ }}`) client-id/creds
# value a developer hardcoded instead of referencing a secret must never be
# retained or rendered verbatim in any finding's details -- only a
# redacted, non-reversible placeholder is ever shown, even while two
# occurrences of the very same inline value are still recognized as the
# same (shared) identity.
# ---------------------------------------------------------------------------


def test_inline_identity_value_is_never_rendered_verbatim_in_finding_details(
    tmp_path,
):
    root = tmp_path / "inline-secret-repo"
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    inline_value = "11111111-2222-3333-4444-555555555555"
    (workflow_dir / "deploy.yml").write_text(
        textwrap.dedent(
            f"""\
            name: Deploy
            on:
              push:
                branches: [main]
            permissions:
              contents: read
              id-token: write
            jobs:
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: {inline_value}
                      tenant-id: ${{{{ secrets.AZURE_TENANT_ID }}}}
                      subscription-id: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}
                  - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    (workflow_dir / "ci.yml").write_text(
        textwrap.dedent(
            f"""\
            name: CI
            on:
              pull_request:
            permissions:
              contents: read
              id-token: write
            jobs:
              test:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: {inline_value}
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
    result = assess_change_plane(root, live_github=None, live_azure=None)
    all_text = " ".join(
        finding.details + " ".join(finding.affected_actions) for finding in result.findings
    )
    assert inline_value not in all_text
    # The two workflows sharing that same inline identity still surface a
    # finding recognizing them as a shared identity, so redaction never
    # silently drops the underlying signal -- only the literal value.
    assert "GHCP-006" in {f.finding_id for f in result.findings}


def test_secret_backed_identity_ref_stays_untouched(tmp_path):
    """A `${{ secrets.* }}` identity reference is not itself a credential
    value at all -- it must pass through unredacted so genuinely distinct
    secret names remain distinguishable."""
    root = _write_clean_repo(tmp_path)
    (root / ".github" / "workflows" / "deploy.yml").write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              push:
                branches: [main]
            permissions:
              contents: read
              id-token: write
            jobs:
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{ secrets.DEPLOY_CLIENT_ID }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    # No live Azure evidence is supplied, so identity separation itself can
    # only land on "not-verified" here (never an inferred `pass`) -- what
    # this test actually guards is that a genuine `${{ secrets.* }}`
    # reference is never mistaken for an inline value and redacted into a
    # collision with some other workflow's own (different) secret
    # reference, which would falsely report a *shared* identity instead.
    identity_findings = [f for f in result.findings if f.finding_id == "GHCP-006"]
    assert all(f.reason_code != "shared-identity" for f in identity_findings)
    assert all(f.status != "must-fix" for f in identity_findings)


# ---------------------------------------------------------------------------
# Fresh quality review, item 7: GitHub's own real CODEOWNERS precedence --
# `.github/CODEOWNERS` first, then root `CODEOWNERS`, then `docs/CODEOWNERS`
# -- must be honored exactly, and no finding may ever leak an absolute
# host filesystem path (only repo-relative paths).
# ---------------------------------------------------------------------------


def test_github_directory_codeowners_takes_precedence_over_root(tmp_path):
    root = tmp_path / "codeowners-precedence-repo"
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
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    complete_lines = [
        "src/governance/** @octo-org/governance",
        "policies/** @octo-org/governance",
        "tests/** @octo-org/governance",
        ".github/workflows/governed-actions.yml @octo-org/governance",
        "tests/governed-actions-manifest.json @octo-org/governance",
        "tests/governed-actions-apply-plan.json @octo-org/governance",
        "",
    ]
    (workflow_dir.parent / "CODEOWNERS").write_text(
        "\n".join(complete_lines), encoding="utf-8"
    )
    # The root CODEOWNERS covers nothing at all -- if precedence were
    # wrong and root were consulted instead of `.github/CODEOWNERS`,
    # coverage would be incomplete (a GHCP-002 "must-fix" finding).
    (root / "CODEOWNERS").write_text("# no real coverage here\n", encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    # No live_github evidence is supplied, so GHCP-002 can only ever land on
    # "not-verified" here (branch-protection enforcement is never provable
    # from static files alone) -- what this test actually guards is that
    # coverage itself is complete: if precedence were wrong and the
    # (incomplete) root file were consulted instead, coverage would be
    # incomplete and the finding would be `must-fix` with reason code
    # "codeowners-incomplete-coverage".
    codeowners_findings = [f for f in result.findings if f.finding_id == "GHCP-002"]
    assert all(f.status != "must-fix" for f in codeowners_findings)
    assert all(
        f.reason_code != "codeowners-incomplete-coverage" for f in codeowners_findings
    )


def test_root_codeowners_takes_precedence_over_docs(tmp_path):
    root = tmp_path / "codeowners-docs-precedence-repo"
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
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    complete_lines = [
        "src/governance/** @octo-org/governance",
        "policies/** @octo-org/governance",
        "tests/** @octo-org/governance",
        ".github/workflows/governed-actions.yml @octo-org/governance",
        "tests/governed-actions-manifest.json @octo-org/governance",
        "tests/governed-actions-apply-plan.json @octo-org/governance",
        "",
    ]
    (root / "CODEOWNERS").write_text("\n".join(complete_lines), encoding="utf-8")
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True)
    # `docs/CODEOWNERS` covers nothing -- if it were consulted instead of
    # the root file, coverage would be incomplete.
    (docs_dir / "CODEOWNERS").write_text("# no real coverage here\n", encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    codeowners_findings = [f for f in result.findings if f.finding_id == "GHCP-002"]
    assert all(f.status != "must-fix" for f in codeowners_findings)
    assert all(
        f.reason_code != "codeowners-incomplete-coverage" for f in codeowners_findings
    )


def test_incomplete_codeowners_finding_never_leaks_absolute_host_path(tmp_path):
    root = _repo_with_codeowners(
        tmp_path,
        [
            "# no real coverage at all",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = _codeowners_finding(result)
    assert str(tmp_path) not in finding.details
    assert str(root) not in finding.details


def test_symlink_escape_finding_never_leaks_absolute_host_path(tmp_path):
    root = tmp_path / "symlink-path-leak-repo"
    _write_workflow(
        root,
        "ci.yml",
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
              - name: Run CTK and application probes
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
        """,
    )
    outside_target = tmp_path / "outside-secret.yml"
    outside_target.write_text("name: Outside\non:\n  push:\n", encoding="utf-8")
    symlink_path = root / ".github" / "workflows" / "linked.yml"
    symlink_path.symlink_to(outside_target)

    result = assess_change_plane(root, live_github=None, live_azure=None)
    all_details = " ".join(f.details for f in result.findings)
    all_paths = " ".join(path for f in result.findings for path in f.affected_paths)
    assert str(tmp_path) not in all_details
    assert str(tmp_path) not in all_paths


# ---------------------------------------------------------------------------
# Verification-gap fix 1: an aggregate `ghcp_azure_oidc` control (and its
# GHCP-005 finding) must reflect a single workflow's own `not-verified`
# OIDC/WIF status -- never silently resolve to a rolled-up `pass` just
# because no *other* workflow is an outright `must-fix` offender.
# ---------------------------------------------------------------------------


def _repo_with_unverified_azure_deploy_and_clean_workflow(tmp_path: Path) -> Path:
    root = tmp_path / "unverified-oidc-repo"
    _write_workflow(
        root,
        "deploy.yml",
        """\
        name: Deploy
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          ship:
            runs-on: ubuntu-latest
            permissions:
              contents: read
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - name: Deploy via CLI
                run: az webapp deploy --resource-group rg --name my-app
        """,
    )
    _write_workflow(
        root,
        "ci.yml",
        """\
        name: CI
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          test:
            runs-on: ubuntu-latest
            permissions:
              contents: read
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - name: Run CTK and application probes
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
        """,
    )
    return root


def test_aggregate_azure_oidc_control_propagates_not_verified_never_pass(tmp_path):
    root = _repo_with_unverified_azure_deploy_and_clean_workflow(tmp_path)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.controls["ghcp_azure_oidc"] == "not-verified"
    finding = next(f for f in result.findings if f.finding_id == "GHCP-005")
    assert finding.status == "not-verified"
    assert finding.reason_code == "azure-login-not-verified-statically"


def test_aggregate_azure_oidc_control_stays_pass_with_no_unverified_workflow(tmp_path):
    """A clean repo with no Azure deploy evidence at all still confirms a
    passing `ghcp_azure_oidc` control -- the not-verified propagation from
    the test above is specific to an actual unverified deploy, never a
    blanket regression to a permanently-unverifiable control."""
    root = _write_clean_repo(tmp_path)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.controls["ghcp_azure_oidc"] == "pass"
    assert "GHCP-005" not in {f.finding_id for f in result.findings}


# ---------------------------------------------------------------------------
# Verification-gap fix 2: `_is_statically_disabled` must normalize a GitHub
# expression wrapper -- `if: ${{ false }}`, `if: "${{ false }}"`, and
# equivalent constant forms -- down to the same literal `false` a bare
# `if: false` already produces, so a disabled step or job can never satisfy
# CTK/application-probe/eval evidence just because its own disabling
# condition happens to be spelled as an expression instead of a bare
# boolean.
# ---------------------------------------------------------------------------


def test_ctk_probe_in_expression_wrapped_disabled_step_is_must_fix(tmp_path):
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
              test:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK and application probes
                    if: ${{ false }}
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_in_quoted_expression_wrapped_disabled_step_is_must_fix(tmp_path):
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
              test:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK and application probes
                    if: "${{ false }}"
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_in_expression_wrapped_disabled_job_is_must_fix(tmp_path):
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        "python -m ctk run-vectors\n                      "
        "python -m probes run-application-probe",
        job_if="${{ false }}",
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "must-fix"


def test_eval_runner_in_expression_wrapped_disabled_step_is_must_fix(tmp_path):
    root = tmp_path / "eval-expr-disabled-repo"
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
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
                  - name: Run evals
                    if: ${{ false }}
                    run: pytest evals
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
    eval_dir = root / "evals"
    eval_dir.mkdir(parents=True)
    (eval_dir / "test_eval.py").write_text("def test_eval(): pass\n", encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert "GHCP-003" in {f.finding_id for f in result.findings}


def test_ctk_probe_with_genuinely_enabled_expression_condition_still_passes(tmp_path):
    """A real, non-constant expression condition (or one that normalizes to
    a truthy constant) must never be mistaken for a disabling condition --
    only a step/job that actually normalizes to a false-y literal is
    treated as statically disabled."""
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        "python -m ctk run-vectors\n                      "
        "python -m probes run-application-probe",
        job_if="${{ true }}",
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "pass"


# ---------------------------------------------------------------------------
# Verification-gap fix 3: live required-status-check enforcement must bind
# to whichever job(s) actually run the repo's own eval-suite command, not
# just the job(s) satisfying CTK/application-probe evidence -- a separate,
# unrequired job quietly running the eval suite must never confirm branch
# protection on its own.
# ---------------------------------------------------------------------------


def _repo_with_ctk_probe_and_separate_eval_job(tmp_path: Path) -> Path:
    root = tmp_path / "separate-eval-job-repo"
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
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
              evals:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run evals
                    run: pytest evals
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
    eval_dir = root / "evals"
    eval_dir.mkdir(parents=True)
    (eval_dir / "test_eval.py").write_text("def test_eval(): pass\n", encoding="utf-8")
    return root


def test_required_check_naming_only_ctk_job_stays_not_verified_when_eval_job_separate(
    tmp_path,
):
    root = _repo_with_ctk_probe_and_separate_eval_job(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["test"],
                "enforce_admins": True,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" in {f.finding_id for f in result.findings}
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_required_check_naming_both_ctk_and_eval_jobs_confirms_pass(tmp_path):
    root = _repo_with_ctk_probe_and_separate_eval_job(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["test", "evals"],
                "enforce_admins": True,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" not in {
        f.finding_id for f in result.findings if f.status == "not-verified"
    }


def test_required_check_naming_only_eval_job_stays_not_verified_when_ctk_job_separate(
    tmp_path,
):
    """The binding requirement is symmetric: naming only the eval job while
    leaving the CTK/probe job unrequired is just as unconfirmed as the
    reverse."""
    root = _repo_with_ctk_probe_and_separate_eval_job(tmp_path)
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["evals"],
                "enforce_admins": True,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" in {f.finding_id for f in result.findings}
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_required_check_naming_single_combined_job_confirms_pass(tmp_path):
    """A single job that itself runs both the CTK/application probes and
    the eval suite satisfies both roles at once, without needing to be
    named twice."""
    root = tmp_path / "combined-job-repo"
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
                permissions:
                  contents: read
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Run CTK, application probes, and evals
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
                      pytest evals
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
    eval_dir = root / "evals"
    eval_dir.mkdir(parents=True)
    (eval_dir / "test_eval.py").write_text("def test_eval(): pass\n", encoding="utf-8")
    live_github = {
        "default_branch": "main",
        "branch_protection": {
            "main": {
                "required_pull_request_reviews": dict(_STRUCTURED_REQUIRED_REVIEWS),
                "required_status_checks": ["test"],
                "enforce_admins": True,
                "allow_force_pushes": False,
            }
        },
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" not in {
        f.finding_id for f in result.findings if f.status == "not-verified"
    }


# ---------------------------------------------------------------------------
# Verification-gap fix 5: only a single, validated GitHub Actions context
# reference (``secrets.*``, ``vars.*``, ``env.*``, ``needs.*.outputs.*``,
# ...) wrapped in ``${{ ... }}`` and nothing else is ever preserved as an
# identity reference -- a string literal, a function call, or any other
# expression payload wrapped in the very same ``${{ ... }}`` syntax is not
# itself proof it is a safe reference rather than an inline credential a
# workflow author hardcoded directly into the expression, and must still be
# redacted.
# ---------------------------------------------------------------------------


def _workflow_with_client_id(tmp_path: Path, client_id_value: str) -> Path:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflow_dir / "deploy.yml"
    workflow_path.write_text(
        textwrap.dedent(
            f"""\
            name: Deploy
            on:
              pull_request:
            permissions:
              contents: read
              id-token: write
            jobs:
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@92a5484dfaf04ca78a94597f4f19fea633851fa2
                    with:
                      client-id: {client_id_value}
                      tenant-id: ${{{{ secrets.AZURE_TENANT_ID }}}}
                      subscription-id: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}
            """
        ),
        encoding="utf-8",
    )
    return workflow_path


def test_string_literal_wrapped_in_expression_syntax_is_still_redacted(tmp_path):
    workflow = _workflow_with_client_id(tmp_path, "${{ 'hardcoded-client-id-value' }}")
    result = assess_workflow(workflow)
    refs = result.identity_refs
    assert refs
    assert "hardcoded-client-id-value" not in " ".join(refs)
    assert any(ref.startswith("<inline-identity-value-redacted:sha256:") for ref in refs)


def test_function_call_wrapped_in_expression_syntax_is_still_redacted(tmp_path):
    workflow = _workflow_with_client_id(
        tmp_path, "${{ fromJSON(secrets.AZURE_CREDENTIALS).clientId }}"
    )
    result = assess_workflow(workflow)
    refs = result.identity_refs
    assert refs
    assert "AZURE_CREDENTIALS" not in " ".join(refs)
    assert any(ref.startswith("<inline-identity-value-redacted:sha256:") for ref in refs)


def test_bare_secrets_context_reference_is_preserved_unredacted(tmp_path):
    workflow = _workflow_with_client_id(tmp_path, "${{ secrets.AZURE_CLIENT_ID }}")
    result = assess_workflow(workflow)
    refs = result.identity_refs
    assert "${{ secrets.AZURE_CLIENT_ID }}" in refs


def test_bare_vars_and_needs_context_references_are_preserved_unredacted(tmp_path):
    for safe_ref in (
        "${{ vars.AZURE_CLIENT_ID }}",
        "${{ needs.build.outputs.client_id }}",
    ):
        workflow = _workflow_with_client_id(tmp_path, safe_ref)
        result = assess_workflow(workflow)
        assert safe_ref in result.identity_refs


# ---------------------------------------------------------------------------
# Verification-gap fix 6: a workflow file this module cannot safely read or
# parse must never leak the assessment host's own absolute directory layout
# into the resulting finding, even though the underlying `OSError`/
# `yaml.YAMLError` text itself embeds whatever path this module happened to
# read the file from.
# ---------------------------------------------------------------------------


def test_malformed_yaml_finding_never_leaks_absolute_host_path(tmp_path):
    root = tmp_path / "malformed-path-leak-repo"
    _write_workflow(
        root,
        "ci.yml",
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
              - name: Run CTK and application probes
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
        """,
    )
    malformed_path = root / ".github" / "workflows" / "malformed.yml"
    malformed_path.write_text("name: Broken\non: [pull_request\n", encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    all_details = " ".join(f.details for f in result.findings)
    all_paths = " ".join(path for f in result.findings for path in f.affected_paths)
    assert str(tmp_path) not in all_details
    assert str(tmp_path) not in all_paths
    assert str(root) not in all_details
    assert str(root) not in all_paths


def test_oversized_workflow_finding_never_leaks_absolute_host_path(tmp_path):
    root = tmp_path / "oversized-path-leak-repo"
    _write_workflow(
        root,
        "ci.yml",
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
              - name: Run CTK and application probes
                run: |
                  python -m ctk run-vectors
                  python -m probes run-application-probe
        """,
    )
    huge_path = root / ".github" / "workflows" / "huge.yml"
    huge_path.write_text("# padding\n" + ("x" * (2 * 1024 * 1024)), encoding="utf-8")
    result = assess_change_plane(root, live_github=None, live_azure=None)
    all_details = " ".join(f.details for f in result.findings)
    all_paths = " ".join(path for f in result.findings for path in f.affected_paths)
    assert str(tmp_path) not in all_details
    assert str(tmp_path) not in all_paths
    assert str(root) not in all_details
    assert str(root) not in all_paths
