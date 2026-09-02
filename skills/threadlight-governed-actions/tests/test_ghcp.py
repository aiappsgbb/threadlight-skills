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
from typing import List, Sequence

import pytest

import canonical
import ghcp
from ghcp import (
    ChangePlaneResult,
    Finding,
    LiveEvidenceResult,
    assess_change_plane,
    assess_workflow,
    collect_live_azure,
    collect_live_github,
)


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


# ---------------------------------------------------------------------------
# High-priority fix 1: rule 1's `pull_request_target` checkout trust check
# fails closed for a `ref:`/`repository:` expression it cannot actually
# reason about as base-scoped -- an unrecognized dynamic expression is
# never assumed safe merely because it fails to match a known-bad marker
# by name; only a small allowlist of GitHub contexts this module can
# actually prove stay base-scoped, or a statically-resolvable `env.NAME`
# indirection recursively bound to one of those, is treated as trusted.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref_expression",
    [
        "${{ inputs.target_ref }}",
        "${{ github.event.pull_request.number }}",
        "${{ vars.SOME_REF }}",
        "${{ needs.build.outputs.ref }}",
        "${{ fromJSON(github.event.client_payload).ref }}",
    ],
)
def test_pull_request_target_unrecognized_dynamic_ref_is_must_fix(
    tmp_path, ref_expression
):
    """An expression this module cannot actually reason about as
    resolving to trusted, base-scoped data must fail closed -- it is
    never assumed safe merely because it fails to match a known-bad
    marker by name."""
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


def test_pull_request_target_bracket_indexed_untrusted_ref_is_must_fix(tmp_path):
    """A bracket-indexed equivalent (`['head']['ref']`) of an already-
    recognized dotted-path marker (`.head.ref`) is normalized to its
    dotted form before matching -- it cannot dodge the denylist purely
    by using bracket notation."""
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
                      ref: ${{ github.event.pull_request['head']['ref'] }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


@pytest.mark.parametrize(
    "ref_expression",
    [
        "${{ github.repository }}",
        "${{ github.sha }}",
        "${{ github.ref }}",
        "${{ github.base_ref }}",
        "${{ github.event.repository.default_branch }}",
        "${{ github.event.pull_request.base.ref }}",
        "${{ github.event.pull_request.base.sha }}",
    ],
)
def test_pull_request_target_trusted_expression_ref_stays_pass(tmp_path, ref_expression):
    """A `${{ ... }}` expression referencing one of the small set of
    GitHub contexts this module can actually reason about as always
    resolving to trusted, base-branch/base-repository-scoped data is
    not a false positive -- new coverage previously untested against a
    fully fail-open default."""
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
    assert result.pr_gate == "pass"


def test_pull_request_target_env_indirection_to_trusted_literal_stays_pass(tmp_path):
    """A `${{ env.NAME }}` indirection whose value is statically
    declared in the workflow's own `env:` block, and itself resolves to
    a plain literal (no further expression), is trusted -- static env
    indirection can be safely resolved rather than failing closed on
    every dynamic-looking expression indiscriminately."""
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "label.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Label
            on:
              pull_request_target:
            env:
              BASE_REF: main
            permissions:
              contents: read
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      ref: ${{ env.BASE_REF }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "pass"


def test_pull_request_target_env_indirection_to_trusted_expression_stays_pass(tmp_path):
    """A job-level `env:` value that itself resolves to a trusted
    expression is recursively re-evaluated by the same trust rule, not
    assumed safe merely because it was indirected through `env.NAME`
    once."""
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
                env:
                  BASE_REF: ${{ github.event.pull_request.base.ref }}
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      ref: ${{ env.BASE_REF }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "pass"


def test_pull_request_target_unresolvable_env_indirection_is_must_fix(tmp_path):
    """A `${{ env.NAME }}` indirection whose name is declared nowhere in
    the step's, job's, or workflow's own `env:` blocks cannot be
    resolved at all -- unresolvable indirection fails closed rather
    than being assumed safe."""
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
                      ref: ${{ env.UNDECLARED_REF }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_env_indirection_to_untrusted_ref_is_must_fix(tmp_path):
    """An `env.NAME` indirection that itself resolves to an
    attacker-controlled ref must still fail closed -- indirection
    through `env:` is never itself proof of trust."""
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "label.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Label
            on:
              pull_request_target:
            env:
              BASE_REF: ${{ github.event.pull_request.head.ref }}
            permissions:
              contents: read
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      ref: ${{ env.BASE_REF }}
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


def test_missing_environment_evidence_never_confirms_a_silent_pass(tmp_path):
    """A deploy job that declares an ``environment:`` but live evidence
    supplies no ``environments`` mapping at all must never be treated
    as confirmed by that silence -- a provenance-free/incomplete
    mapping is exactly as inconclusive as no live evidence at all, and
    can never upgrade GHCP-002 to a pass."""
    root = _write_clean_repo_with_deploy_environment(tmp_path, "production")
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_environments_mapping_missing_declared_name_stays_not_verified(tmp_path):
    """The ``environments`` mapping is present but simply omits the
    deploy job's own declared environment name entirely -- still not
    itself confirmation the environment is protected."""
    root = _write_clean_repo_with_deploy_environment(tmp_path, "production")
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
        "environments": {"staging": {"protected": True}},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


def test_environment_entry_missing_protected_key_stays_not_verified(tmp_path):
    """The declared environment's own entry is present but omits the
    ``protected`` key entirely (rather than explicitly declaring it
    ``False``) -- an incomplete mapping still cannot confirm a pass."""
    root = _write_clean_repo_with_deploy_environment(tmp_path, "production")
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
        "environments": {"production": {}},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"


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
# High-priority fix 5: identity separation is compared at *job*, not
# whole-workflow, granularity -- a single workflow file mixing a build/test
# job with a separate deploy job, where the two jobs happen to reuse the
# exact same identity, must still be caught even though both jobs live in
# the very same file (and the file's own whole-workflow `is_deploy` flag is
# a single shared boolean that would otherwise fold both jobs' identity
# references into the same bucket).
# ---------------------------------------------------------------------------


def _repo_with_shared_identity_within_one_mixed_workflow(tmp_path: Path) -> Path:
    root = tmp_path / "mixed-identity-repo"
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci-and-deploy.yml").write_text(
        textwrap.dedent(
            """\
            name: CI and Deploy
            on:
              pull_request:
              workflow_dispatch:
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
                      client-id: ${{ secrets.SHARED_CLIENT_ID }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{ secrets.SHARED_CLIENT_ID }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    return root


def test_identity_separation_catches_shared_identity_within_single_workflow(tmp_path):
    """A build/test job and a deploy job sharing the exact same
    identity, side by side in the *very same* workflow file, must be
    flagged -- classifying jobs at whole-workflow granularity (a single
    shared `is_deploy` boolean for the entire file) would fold both
    jobs' identity references into the same bucket and never catch
    this; job-scoped classification catches it correctly."""
    root = _repo_with_shared_identity_within_one_mixed_workflow(tmp_path)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-006")
    assert finding.status == "must-fix"
    assert finding.reason_code == "shared-identity"


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
# High-priority fix 2: CODEOWNERS wildcard-overlap semantics -- a
# depth-unanchored basename pattern (`*.json`, `*.yml`, a bare filename)
# matches GitHub's own gitignore-style matching at *any* depth, not just
# where a pattern is textually declared. Such a pattern can both satisfy
# an exact-file requirement directly, and -- appearing *later* in the
# file with a different (or no) owner -- carve a conservative override
# hole out of an otherwise fully-owned recursive tree requirement.
# ---------------------------------------------------------------------------


def test_codeowners_depth_unanchored_pattern_satisfies_exact_file_requirements(
    tmp_path,
):
    """A bare `*.yml`/`*.json` basename glob, with an owner, can satisfy
    the exact-file governance requirements directly -- GitHub applies
    such a pattern at any depth, including to these specific files."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            "*.yml @octo-org/governance",
            "*.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "not-verified"


def test_codeowners_ownerless_depth_unanchored_pattern_grants_no_coverage(tmp_path):
    """The same bare basename glob, declared with *no* owner token at
    all, is a valid CODEOWNERS shape that disowns whatever it matches --
    it must never be treated as satisfying the exact-file requirement it
    happens to also textually match."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            "*.yml",
            "*.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "must-fix"


def test_codeowners_later_unanchored_different_owner_breaks_recursive_coverage(
    tmp_path,
):
    """A later, depth-unanchored `*.json` pattern with a *different*
    owner than the broad `tests/**` line that otherwise fully covers the
    required tree must be treated as a potential override -- GitHub's
    own matching applies it at any depth inside that tree, and this
    module cannot enumerate the tree's real contents from pattern text
    alone to rule that out."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
            "*.json @octo-org/other-team",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "must-fix"


def test_codeowners_later_unanchored_ownerless_breaks_recursive_coverage(tmp_path):
    """A later, depth-unanchored, *ownerless* pattern is exactly as much
    an override as one reassigning a different owner -- an empty owner
    set still differs from the covering tree's own non-empty one."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
            "*.md",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "must-fix"


def test_codeowners_later_unanchored_same_owner_does_not_break_coverage(tmp_path):
    """A later depth-unanchored pattern that re-declares the *identical*
    owner set is not a real override in substance and must not break
    coverage that would otherwise be fully confirmed."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
            "*.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "not-verified"


# ---------------------------------------------------------------------------
# Static-scope review, item 2: an *anchored*, `/`-bearing nested-path glob
# (`.github/workflows/*.yml`) matches the exact governance file
# `.github/workflows/governed-actions.yml` under GitHub's own
# gitignore-style matching, just as surely as a bare depth-unanchored
# basename pattern does -- it must be evaluated for both satisfying and
# overriding an exact-file requirement, not only patterns with no
# internal `/` at all.
# ---------------------------------------------------------------------------


def test_codeowners_nested_path_glob_satisfies_exact_file_requirement(tmp_path):
    """`.github/workflows/*.yml`, with an owner, matches the exact
    `.github/workflows/governed-actions.yml` requirement directly under
    GitHub's own wildcard matching -- a redundant, more specific line
    naming the file exactly is not required."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/*.yml @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "not-verified"


def test_codeowners_ownerless_nested_path_glob_grants_no_coverage(tmp_path):
    """The same nested-path glob declared with *no* owner token still
    textually matches the exact required file -- it must never be
    treated as satisfying the requirement it disowns."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/*.yml",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "must-fix"


def test_codeowners_later_nested_path_glob_different_owner_overrides_exact_file(
    tmp_path,
):
    """A broad `src/**`-style catch-all is not even involved here: a
    fully-owned exact-file requirement, once a *later* anchored
    nested-path glob also matching that exact file reassigns a
    different owner, must resolve to that later entry's owner under
    real last-match-wins semantics -- still coverage in this case,
    since the later entry still has *some* owner, just a different
    one; this exercises that the later, more specific pattern is now
    actually evaluated at all instead of being invisible to the
    resolver."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
            ".github/workflows/*.yml @octo-org/other-team",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "not-verified"


def test_codeowners_later_ownerless_nested_path_glob_breaks_exact_file_coverage(
    tmp_path,
):
    """A later anchored nested-path glob matching the exact required
    file, declared with *no* owner at all, disowns it under real
    last-match-wins semantics -- coverage must be invalidated, not
    silently kept from an earlier, now-superseded owned line."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
            ".github/workflows/*.yml",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "must-fix"


def test_codeowners_nested_path_glob_does_not_match_deeper_nested_file(tmp_path):
    """`.github/workflows/*.yml`'s single `*` must never cross a `/` --
    it must not be treated as matching (or overriding) a file one
    directory deeper than it, since GitHub's own matching would not
    apply it there either."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
            ".github/workflows/nested/*.yml",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    # The ownerless line only matches a strictly deeper path than the
    # required file itself, so it can never override this coverage.
    assert _codeowners_finding(result).status == "not-verified"


# ---------------------------------------------------------------------------
# Final static review, item 2: a leading `**/` matches *zero or more*
# leading directory segments (GitHub's own gitignore-style semantics) --
# `**/foo` must match a root-level `foo` exactly as readily as one nested
# several directories deep, in both the exact-file-coverage and the
# recursive-tree overlap/override logic.
# ---------------------------------------------------------------------------


def test_codeowners_leading_recursive_glob_bare_filename_satisfies_exact_requirement(
    tmp_path,
):
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            "**/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "not-verified"


def test_codeowners_leading_recursive_glob_nested_path_satisfies_exact_requirement(
    tmp_path,
):
    """`**/.github/workflows/*.yml` must match the exact required file
    at zero leading directories -- not merely one nested at least one
    directory below the repository root."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            "**/.github/workflows/*.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "not-verified"


def test_codeowners_ownerless_leading_recursive_glob_breaks_exact_requirement(
    tmp_path,
):
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
            "**/.github/workflows/*.yml",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    # The later ownerless leading-`**/` pattern still matches the exact
    # required file at zero leading directories, so it overrides the
    # earlier full coverage.
    assert _codeowners_finding(result).status == "must-fix"


def test_codeowners_leading_recursive_glob_overlaps_required_recursive_tree(
    tmp_path,
):
    """A later, differently-owned `**/tests/**` matches the required
    `tests/**` tree at zero leading directories too -- it must be
    recognized as overlapping/overriding that tree, not dismissed
    merely because its own raw text carries a `**/` prefix the
    required tree's own pattern does not."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
            "**/tests/**",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "must-fix"


def test_codeowners_leading_recursive_glob_nested_exact_file_overlaps_required_tree(
    tmp_path,
):
    """A later ownerless `**/tests/governed-actions-manifest.json`
    resolves, at zero leading directories, to exactly the required
    nested file inside the `tests/**` tree -- it must be recognized as
    carving a hole out of that tree's coverage."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
            "**/tests/governed-actions-manifest.json",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "must-fix"


def test_codeowners_leading_recursive_glob_does_not_match_unrelated_deeper_directory(
    tmp_path,
):
    """`**/workflows/*.yml` still requires the segment *immediately*
    preceding the wildcard filename to be literally `workflows` --
    zero-or-more leading directories is not license to match an
    unrelated deeper path that never actually contains that
    component."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
            "**/nonexistent-dir/*.yml",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert _codeowners_finding(result).status == "not-verified"



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


# ---------------------------------------------------------------------------
# Static-scope review, item 1: a raw git checkout/fetch/clone command whose
# operand is an `${{ env.NAME }}` indirection must have that indirection
# actually resolved -- to an untrusted value (must-fix), to nothing
# resolvable at all (must-fix, fail-closed), or to a genuinely trusted
# literal/context expression (stays pass) -- rather than only pattern
# matching known-bad ref markers directly present in the run text itself.
# ---------------------------------------------------------------------------


def test_pull_request_target_raw_git_command_env_indirection_to_untrusted_value_is_must_fix(
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
                env:
                  PR_REF: ${{ github.event.pull_request.head.ref }}
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Fetch PR head via env indirection
                    run: git fetch origin ${{ env.PR_REF }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_raw_git_command_unresolved_env_indirection_is_must_fix(
    tmp_path,
):
    """No `env:` block anywhere declares `UNDECLARED_REF` at all -- an
    indirection this module cannot actually resolve must fail closed,
    not be assumed safe merely because no known-bad marker literally
    appears in the run text."""
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
                  - name: Fetch via undeclared env indirection
                    run: git fetch origin ${{ env.UNDECLARED_REF }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_raw_git_command_env_indirection_to_trusted_literal_stays_pass(
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
                env:
                  BASE_REF: main
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Fetch base branch via env indirection
                    run: git fetch origin ${{ env.BASE_REF }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "pass"


def test_pull_request_target_raw_git_command_env_indirection_to_trusted_context_stays_pass(
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
                env:
                  BASE_SHA: ${{ github.sha }}
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Fetch base commit via env indirection
                    run: git fetch origin ${{ env.BASE_SHA }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "pass"


def test_pull_request_target_raw_git_command_direct_dynamic_expression_is_must_fix(
    tmp_path,
):
    """A dynamic expression used directly as a git operand -- not even
    routed through `env.*` -- that is not itself one of the recognized
    trusted checkout contexts must fail closed too, not merely be
    marker-matched against a known-bad list."""
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "label.yml"
    workflow_path.write_text(
        textwrap.dedent(
            """\
            name: Label
            on:
              pull_request_target:
                types: [labeled]
            permissions:
              contents: read
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Checkout arbitrary input ref
                    run: git checkout ${{ inputs.ref }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


# ---------------------------------------------------------------------------
# Final static review, item 1: a raw `git` command run in a `run:` step
# executes in a real shell, so a workflow author can route the very same
# untrusted PR-head env value into the command via ordinary shell `$VAR`/
# `${VAR}` syntax instead of a `${{ env.VAR }}` GitHub-expression
# indirection -- it must be resolved and fail-closed exactly the same way.
# ---------------------------------------------------------------------------


def test_pull_request_target_raw_git_command_bare_shell_var_untrusted_is_must_fix(
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
                env:
                  PR_REF: ${{ github.event.pull_request.head.ref }}
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Fetch PR head via bare shell variable
                    run: git fetch origin $PR_REF
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_raw_git_command_braced_shell_var_untrusted_is_must_fix(
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
                env:
                  PR_REF: ${{ github.event.pull_request.head.ref }}
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Fetch PR head via braced shell variable
                    run: git fetch origin "${PR_REF}"
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_raw_git_command_undeclared_shell_var_is_must_fix(
    tmp_path,
):
    """No `env:` block anywhere declares `PR_REF` at all -- an
    unresolved shell-variable indirection must fail closed exactly
    like an unresolved `${{ env.NAME }}` indirection does."""
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
                  - name: Fetch via undeclared shell variable
                    run: git fetch origin $PR_REF
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_raw_git_command_shell_var_trusted_literal_stays_pass(
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
                env:
                  BASE_REF: main
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Fetch base branch via bare shell variable
                    run: git fetch origin $BASE_REF
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "pass"


def test_pull_request_target_raw_git_command_shell_var_trusted_context_stays_pass(
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
                env:
                  BASE_SHA: ${{ github.sha }}
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Fetch base commit via braced shell variable
                    run: git fetch origin "${BASE_SHA}"
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "pass"


def test_pull_request_target_raw_git_command_chained_shell_var_untrusted_is_must_fix(
    tmp_path,
):
    """A shell-variable value that is itself another shell-variable
    reference, chained through to an untrusted PR-head expression,
    must still fail closed via recursive resolution."""
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
                env:
                  OUTER_REF: $INNER_REF
                  INNER_REF: ${{ github.event.pull_request.head.sha }}
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - name: Fetch via chained shell variable indirection
                    run: git fetch origin $OUTER_REF
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_raw_git_command_shell_var_never_confused_with_gh_expression(
    tmp_path,
):
    """A GitHub `${{ ... }}` expression segment must never be
    misparsed as a shell `${VAR}` reference -- the two syntaxes are
    distinct and a trusted GitHub context expression alone (no bare
    shell variable at all) must stay pass."""
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
                  - name: Fetch base commit via trusted GitHub expression
                    run: git fetch origin ${{ github.sha }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "pass"


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


# ---------------------------------------------------------------------------
# High-priority fix 3: shell command segments (chained at `&&`/`||`/`;`/`|`)
# are inspected individually -- a step cannot smuggle a CTK/application-probe
# marker past rule 3's inert-line filter merely by chaining an
# echo/printf/comment fragment alongside a marker word on the same physical
# line, since that line never itself *starts* with `echo`/`printf`.
# ---------------------------------------------------------------------------


def test_ctk_probe_chained_after_true_via_echo_is_must_fix(tmp_path):
    """`true && echo "...ctk..."` never itself starts with `echo`, so a
    whole-line-only inert filter would let it through; each shell
    segment is inspected on its own, and the `echo`-only segment here
    still contributes nothing but inert text."""
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        'true && echo "python -m ctk run-vectors"\n                      '
        'true && echo "python -m probes run-application-probe"',
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_chained_after_pipe_to_cat_is_must_fix(tmp_path):
    """A marker appearing only after a `|` pipe into an inert command
    (`cat`) must not satisfy the check either -- the marker-bearing
    segment is itself inert output, not a real invocation."""
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        'echo "python -m ctk run-vectors" | cat\n                      '
        'echo "python -m probes run-application-probe" | cat',
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_real_command_chained_with_trailing_echo_still_passes(tmp_path):
    """A genuine invocation chained on the same line as a *trailing*
    inert echo must still be recognized -- segment splitting only
    filters the inert segment, it never discards a real command that
    happens to share a line with one."""
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        'python -m ctk run-vectors && echo "done"\n                      '
        'python -m probes run-application-probe && echo "done"',
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "pass"


def test_ctk_probe_trailing_inline_comment_on_real_command_still_passes(tmp_path):
    """A genuine invocation followed by a trailing `#`-comment fragment
    on the very same segment is unaffected -- only the comment
    fragment itself is stripped, not the real command preceding it."""
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        "python -m ctk run-vectors  # runs governance vectors\n                      "
        "python -m probes run-application-probe  # runs app probe",
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
    """Preserved unredacted -- and in its canonical, whitespace-free
    form, since the exact incidental spacing around `${{ ... }}` a
    workflow author happened to type is not itself part of the
    reference's identity for comparison purposes."""
    workflow = _workflow_with_client_id(tmp_path, "${{ secrets.AZURE_CLIENT_ID }}")
    result = assess_workflow(workflow)
    refs = result.identity_refs
    assert "${{secrets.AZURE_CLIENT_ID}}" in refs


def test_bare_vars_and_needs_context_references_are_preserved_unredacted(tmp_path):
    for safe_ref, canonical_ref in (
        ("${{ vars.AZURE_CLIENT_ID }}", "${{vars.AZURE_CLIENT_ID}}"),
        ("${{ needs.build.outputs.client_id }}", "${{needs.build.outputs.client_id}}"),
    ):
        workflow = _workflow_with_client_id(tmp_path, safe_ref)
        result = assess_workflow(workflow)
        assert canonical_ref in result.identity_refs


# ---------------------------------------------------------------------------
# Static-scope review, item 3: two spellings of the very same GitHub Actions
# context reference -- differing only in incidental whitespace inside the
# `${{ ... }}` wrapper, or using bracket-indexed property access as an exact
# equivalent of dotted access -- must canonicalize to the identical form, so
# rule 6's identity-separation comparison recognizes them as the same
# identity rather than two distinct ones.
# ---------------------------------------------------------------------------


def test_identity_refs_differing_only_in_wrapper_whitespace_canonicalize_equal(
    tmp_path,
):
    workflow = _workflow_with_client_id(tmp_path, "${{secrets.AZURE_CLIENT_ID}}")
    result = assess_workflow(workflow)
    assert result.identity_refs == ("${{secrets.AZURE_CLIENT_ID}}",)
    workflow2 = _workflow_with_client_id(tmp_path, "${{   secrets.AZURE_CLIENT_ID   }}")
    result2 = assess_workflow(workflow2)
    assert result2.identity_refs == result.identity_refs


def test_bracket_indexed_identity_ref_canonicalizes_to_dotted_form(tmp_path):
    workflow = _workflow_with_client_id(tmp_path, "${{ secrets['AZURE_CLIENT_ID'] }}")
    result = assess_workflow(workflow)
    assert result.identity_refs == ("${{secrets.AZURE_CLIENT_ID}}",)


def test_identity_separation_catches_bracket_vs_dot_shared_identity_within_workflow(
    tmp_path,
):
    """A build/test job spelling its identity with bracket-indexed
    access and a deploy job spelling the exact same identity with
    dotted access, side by side in the very same workflow file, must
    still be caught as a shared identity -- the two spellings are
    semantically identical and must canonicalize to the same
    reference before comparison."""
    root = tmp_path / "bracket-dot-identity-repo"
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci-and-deploy.yml").write_text(
        textwrap.dedent(
            """\
            name: CI and Deploy
            on:
              pull_request:
              workflow_dispatch:
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
                      client-id: ${{ secrets['SHARED_CLIENT_ID'] }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{secrets.SHARED_CLIENT_ID}}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-006")
    assert finding.status == "must-fix"
    assert finding.reason_code == "shared-identity"


# ---------------------------------------------------------------------------
# Final static review, item 3: GitHub documents the `secrets`/`vars`
# contexts specifically as case-insensitive -- both the context keyword
# itself and every secret/variable name looked up on it are stored and
# resolved as uppercase regardless of how they were entered or
# referenced, so `${{ SECRETS.Azure_Client_Id }}` and
# `${{ secrets.AZURE_CLIENT_ID }}` name the exact same secret. Every
# other context (`github`, `env`, `needs`, ...) remains case-sensitive
# and must never have its case collapsed.
# ---------------------------------------------------------------------------


def test_identity_refs_differing_only_in_secrets_context_case_canonicalize_equal(
    tmp_path,
):
    workflow = _workflow_with_client_id(tmp_path, "${{ SECRETS.AZURE_CLIENT_ID }}")
    result = assess_workflow(workflow)
    assert result.identity_refs == ("${{secrets.AZURE_CLIENT_ID}}",)
    workflow2 = _workflow_with_client_id(tmp_path, "${{ secrets.AZURE_CLIENT_ID }}")
    result2 = assess_workflow(workflow2)
    assert result2.identity_refs == result.identity_refs


def test_identity_refs_differing_only_in_secret_name_case_canonicalize_equal(
    tmp_path,
):
    """A secret's own *name*, not merely the `secrets` context keyword,
    is documented case-insensitive and stored uppercase -- differing
    only in the name's own letter case must still canonicalize
    identically."""
    workflow = _workflow_with_client_id(tmp_path, "${{ secrets.Azure_Client_Id }}")
    result = assess_workflow(workflow)
    assert result.identity_refs == ("${{secrets.AZURE_CLIENT_ID}}",)


def test_identity_refs_differing_only_in_vars_context_case_canonicalize_equal(
    tmp_path,
):
    workflow = _workflow_with_client_id(tmp_path, "${{ VARS.azure_client_id }}")
    result = assess_workflow(workflow)
    assert result.identity_refs == ("${{vars.AZURE_CLIENT_ID}}",)


def test_identity_separation_catches_secrets_case_variant_shared_identity(tmp_path):
    """A build/test job spelling its identity in one letter case and a
    deploy job spelling the exact same secret with a differently-cased
    context/name, side by side in the very same workflow file, must
    still be caught as a shared identity."""
    root = tmp_path / "secrets-case-identity-repo"
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci-and-deploy.yml").write_text(
        textwrap.dedent(
            """\
            name: CI and Deploy
            on:
              pull_request:
              workflow_dispatch:
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
                      client-id: ${{ SECRETS.Shared_Client_Id }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{ secrets.SHARED_CLIENT_ID }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-006")
    assert finding.status == "must-fix"
    assert finding.reason_code == "shared-identity"


def test_identity_refs_differing_case_in_noncase_insensitive_context_stay_distinct(
    tmp_path,
):
    """`github`/`env`/`needs`/etc. are not documented case-insensitive
    contexts -- their case must never be collapsed, unlike
    `secrets`/`vars`."""
    workflow = _workflow_with_client_id(tmp_path, "${{ env.AZURE_CLIENT_ID }}")
    result = assess_workflow(workflow)
    assert result.identity_refs == ("${{env.AZURE_CLIENT_ID}}",)
    workflow2 = _workflow_with_client_id(tmp_path, "${{ env.azure_client_id }}")
    result2 = assess_workflow(workflow2)
    assert result2.identity_refs == ("${{env.azure_client_id}}",)
    assert result.identity_refs != result2.identity_refs


def test_identity_separation_live_mapping_key_with_extra_whitespace_still_resolves(
    tmp_path,
):
    """A live-evidence mapping keyed with an incidental whitespace
    variant of the exact same reference the workflow itself declares
    must still resolve on lookup -- the mapping's own keys are
    canonicalized identically before use, not just this module's
    internally computed identity references."""
    root = _repo_with_separate_deploy_and_test_identities(tmp_path)
    live_azure = {
        "identity_principal_ids": {
            "${{  secrets.TEST_CLIENT_ID  }}": "11111111-1111-1111-1111-111111111111",
            "${{secrets.DEPLOY_CLIENT_ID}}": "22222222-2222-2222-2222-222222222222",
        }
    }
    result = assess_change_plane(root, live_github=None, live_azure=live_azure)
    assert "GHCP-006" not in {f.finding_id for f in result.findings}


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


# ---------------------------------------------------------------------------
# Final trust-gap fix 1: `github.event.pull_request.merge_commit_sha` is a
# *sibling* field to `.head`, not nested under it -- the merge commit it
# names still incorporates the PR author's own (attacker-controlled) diff
# merged into the base, so a `pull_request_target` checkout of it is exactly
# as untrusted as checking out `.head.ref`/`.head.sha` directly.
# ---------------------------------------------------------------------------


def test_pull_request_target_merge_commit_sha_ref_is_must_fix(tmp_path):
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
                      ref: ${{ github.event.pull_request.merge_commit_sha }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_raw_checkout_of_merge_commit_sha_is_must_fix(tmp_path):
    """The same untrusted merge-commit ref is just as unsafe when checked
    out via a raw `git checkout`/`fetch` command rather than through
    `actions/checkout`'s own `ref:` input."""
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
                  - name: Fetch merge commit directly
                    run: git fetch origin ${{ github.event.pull_request.merge_commit_sha }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


# ---------------------------------------------------------------------------
# Final trust-gap fix 2: CODEOWNERS resolution is last-match-wins *per
# file*, not per requirement pattern -- a broad entry that covers a whole
# required tree is not enough on its own if a *later*, narrower entry
# nested inside that tree disowns (or reassigns) part of it. Broad
# ownership can never be trusted to prove full recursive coverage when a
# descendant subtree is later carved out.
# ---------------------------------------------------------------------------


def test_codeowners_later_narrower_ownerless_entry_breaks_recursive_coverage(tmp_path):
    """A broad `src/governance/**` owner entry followed by a later,
    narrower, ownerless `src/governance/legacy/**` entry must not be
    treated as full coverage of the required `src/governance/**` tree --
    the later entry wins for every file under `legacy/`, disowning it."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "src/governance/legacy/**",  # later, narrower, ownerless
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


def test_codeowners_later_narrower_other_owner_entry_breaks_recursive_coverage(
    tmp_path,
):
    """The same gap when the later, narrower entry reassigns the subtree
    to a *different* owner rather than disowning it outright -- either
    way, the broad entry no longer proves full recursive coverage."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "src/governance/legacy/** @octo-org/legacy-team",  # different owner
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


def test_codeowners_later_narrower_same_owner_entry_still_confirms_coverage(tmp_path):
    """A later, narrower entry that merely re-declares the *identical*
    owner set is not a real override in substance -- coverage must still
    be confirmed (this is a regression/parity guard against becoming an
    overly-aggressive false negative)."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/** @octo-org/governance",
            "src/governance/legacy/** @octo-org/governance",  # same owner
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"
    assert finding.reason_code != "codeowners-incomplete-coverage"


def test_codeowners_earlier_narrower_ownerless_entry_does_not_break_coverage(tmp_path):
    """A narrower ownerless entry appearing *before* the broad covering
    entry is irrelevant -- the later, broad entry already overrides it
    for every file it matches, so coverage is genuinely complete."""
    root = _repo_with_codeowners(
        tmp_path,
        [
            "src/governance/legacy/**",  # earlier, narrower, ownerless
            "src/governance/** @octo-org/governance",  # later, broad, owns all
            "policies/** @octo-org/governance",
            "tests/** @octo-org/governance",
            ".github/workflows/governed-actions.yml @octo-org/governance",
            "tests/governed-actions-manifest.json @octo-org/governance",
            "tests/governed-actions-apply-plan.json @octo-org/governance",
        ],
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"
    assert finding.reason_code != "codeowners-incomplete-coverage"


# ---------------------------------------------------------------------------
# Final trust-gap fix 3: eval-suite execution detection must see an actual
# recognized test/eval runner invocation targeting the discovered eval
# directory -- a command that merely lists, prints, cats, or searches the
# path (`ls evals`, `find evals -name ...`, `cat evals/x.py`) can never
# count, however literally the path appears in the run text.
# ---------------------------------------------------------------------------


def test_ls_of_eval_directory_alone_is_must_fix(tmp_path):
    root = _repo_with_eval_suite(
        tmp_path, eval_relative_dir="evals", ci_extra_run="ls evals"
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next((f for f in result.findings if f.finding_id == "GHCP-003"), None)
    assert finding is not None
    assert finding.status == "must-fix"


def test_find_of_eval_directory_alone_is_must_fix(tmp_path):
    root = _repo_with_eval_suite(
        tmp_path, eval_relative_dir="evals", ci_extra_run="find evals -name '*.py'"
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next((f for f in result.findings if f.finding_id == "GHCP-003"), None)
    assert finding is not None
    assert finding.status == "must-fix"


def test_cat_of_eval_directory_alone_is_must_fix(tmp_path):
    root = _repo_with_eval_suite(
        tmp_path, eval_relative_dir="evals", ci_extra_run="cat evals/case_one.py"
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next((f for f in result.findings if f.finding_id == "GHCP-003"), None)
    assert finding is not None
    assert finding.status == "must-fix"


def test_python_module_pytest_runner_against_evals_passes(tmp_path):
    """`python -m pytest evals` is a recognized runner invocation, not just
    a bare mention of the directory, and must still satisfy rule 3."""
    root = _repo_with_eval_suite(
        tmp_path, eval_relative_dir="evals", ci_extra_run="python -m pytest evals"
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    assert result.controls["ghcp_ci_probes"] == "pass"
    assert "GHCP-003" not in {f.finding_id for f in result.findings}


# ---------------------------------------------------------------------------
# Final trust-gap fix 4: a malformed-YAML finding must never retain the
# parser's own raw message or source-line snippet -- only a sanitized error
# class plus line/column, never source content or credential-shaped values
# a broken workflow file happened to contain.
# ---------------------------------------------------------------------------


def test_malformed_yaml_finding_never_leaks_source_snippet_or_secret(tmp_path):
    root = tmp_path / "malformed-secret-repo"
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
    # The line that trips the parser also happens to contain a
    # plausible-looking secret -- PyYAML's own `str(error)` would embed
    # this exact source line as a "context" snippet if it were ever
    # interpolated directly into a finding.
    malformed_path.write_text(
        "name: Broken\non: [pull_request\npassword: hunter2supersecret\n",
        encoding="utf-8",
    )
    result = assess_change_plane(root, live_github=None, live_azure=None)
    all_details = " ".join(f.details for f in result.findings)
    all_sha_violations = " ".join(
        v for f in result.findings for v in getattr(f, "sha_violations", ())
    )
    combined = all_details + " " + all_sha_violations
    assert "hunter2supersecret" not in combined
    assert "password:" not in combined
    # A sanitized error class is still surfaced -- the finding is not
    # emptied of all diagnostic value, only of raw source content.
    assert "Error" in combined


# ---------------------------------------------------------------------------
# Approval review, item 1: a `with:` input name is resolved case-
# insensitively, exactly like GitHub Actions' own runner does -- every
# declared `with:` entry is exposed to the invoked action as an
# `INPUT_<NAME>` environment variable built by uppercasing the literal
# YAML key, and `@actions/core`'s own `getInput()` uppercases the name it
# looks up the identical way, so `Client-Id:`, `CLIENT-ID:`, and
# `client-id:` all resolve to the exact same actual input regardless of
# a workflow author's chosen letter case.
# ---------------------------------------------------------------------------


def _workflow_with_cased_client_id_key(tmp_path: Path, key: str) -> Path:
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
                      {key}: ${{{{ secrets.AZURE_CLIENT_ID }}}}
                      tenant-id: ${{{{ secrets.AZURE_TENANT_ID }}}}
                      subscription-id: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}
            """
        ),
        encoding="utf-8",
    )
    return workflow_path


def test_identity_refs_recognizes_mixed_case_client_id_with_key(tmp_path):
    workflow = _workflow_with_cased_client_id_key(tmp_path, "Client-Id")
    result = assess_workflow(workflow)
    assert result.identity_refs == ("${{secrets.AZURE_CLIENT_ID}}",)


def test_identity_refs_recognizes_uppercase_client_id_with_key(tmp_path):
    workflow = _workflow_with_cased_client_id_key(tmp_path, "CLIENT-ID")
    result = assess_workflow(workflow)
    assert result.identity_refs == ("${{secrets.AZURE_CLIENT_ID}}",)


def test_job_scoped_identity_refs_recognizes_mixed_case_client_id_key(tmp_path):
    """The job-scoped bucketing rule 6 relies on (deploy vs. non-deploy
    identities) must resolve a differently-cased `with:` key exactly like
    the workflow-level identity extraction does, or a mixed-case deploy
    identity would silently vanish from the identity-separation check."""
    root = tmp_path / "mixed-case-identity-repo"
    _write_workflow(
        root,
        "deploy.yml",
        """\
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
                  Client-Id: ${{ secrets.DEPLOY_CLIENT_ID }}
                  tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                  subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
          test:
            runs-on: ubuntu-latest
            permissions:
              contents: read
              id-token: write
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - uses: azure/login@92a5484dfaf04ca78a94597f4f19fea633851fa2
                with:
                  Client-Id: ${{ secrets.DEPLOY_CLIENT_ID }}
                  tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                  subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "deploy.yml")
    assert result.deploy_identity_refs
    assert result.non_deploy_identity_refs
    assert set(result.deploy_identity_refs) & set(result.non_deploy_identity_refs)


def test_pull_request_target_checkout_flags_mixed_case_ref_key(tmp_path):
    """`actions/checkout`'s own `ref:` input, spelled with a different
    letter case, must still fail closed under `pull_request_target` when
    it resolves to an attacker-controlled value."""
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
                      Ref: ${{ github.event.pull_request.head.sha }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_pull_request_target_checkout_flags_uppercase_repository_key(tmp_path):
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
                      REPOSITORY: ${{ github.event.pull_request.head.repo.full_name }}
                      ref: ${{ github.event.pull_request.head.sha }}
            """
        ),
        encoding="utf-8",
    )
    result = assess_workflow(workflow_path)
    assert result.pr_gate == "must-fix"


def test_secret_login_still_detected_regardless_of_with_key_case(tmp_path):
    """No regression: presence-only checks (`_has_secret_credential_input`,
    the OIDC `client-id`/`tenant-id` presence check) were already
    case-insensitive before this change and must remain so."""
    root = tmp_path / "cased-secret-login-repo"
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
            permissions:
              contents: read
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - uses: azure/login@92a5484dfaf04ca78a94597f4f19fea633851fa2
                with:
                  Creds: ${{ secrets.AZURE_CREDENTIALS }}
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "deploy.yml")
    assert result.oidc_wif == "must-fix"


# ---------------------------------------------------------------------------
# Approval review, item 2: CODEOWNERS coverage must extend to a repo's own,
# actually-discovered infrastructure/Infrastructure-as-Code (IaC) surface --
# conventional top-level directories (`infra/`, `infrastructure/`,
# `terraform/`, `bicep/`), individual `.tf`/`.bicep` files anywhere in the
# tree, and genuine ARM JSON templates (sniffed by their own `$schema`) --
# without ever inventing a requirement for an infra category this repo
# simply does not have.
# ---------------------------------------------------------------------------


def _write_codeowners(root: Path, patterns: Sequence[str]) -> None:
    root.joinpath("CODEOWNERS").write_text(
        "\n".join(f"{pattern} @octo-org/governance" for pattern in patterns) + "\n",
        encoding="utf-8",
    )


_CLEAN_CODEOWNERS_PATTERNS = (
    "src/governance/**",
    "policies/**",
    "tests/**",
    ".github/workflows/governed-actions.yml",
    "tests/governed-actions-manifest.json",
    "tests/governed-actions-apply-plan.json",
)


def test_infra_directory_with_no_codeowners_coverage_is_must_fix(tmp_path):
    root = tmp_path / "infra-repo"
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
    (root / "infra").mkdir(parents=True)
    (root / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"
    assert "infra/**" in finding.details


def test_infra_directory_with_codeowners_coverage_clears_ghcp_002(tmp_path):
    root = tmp_path / "infra-covered-repo"
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
    (root / "infra").mkdir(parents=True)
    (root / "infra" / "main.bicep").write_text("param location string\n", encoding="utf-8")
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS + ("infra/**",))
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" not in {f.finding_id for f in result.findings}


def test_nested_terraform_file_requires_its_own_top_level_directory(tmp_path):
    root = tmp_path / "terraform-nested-repo"
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
    (root / "deploy" / "modules").mkdir(parents=True)
    (root / "deploy" / "modules" / "network.tf").write_text(
        "resource \"azurerm_virtual_network\" \"vnet\" {}\n", encoding="utf-8"
    )
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"
    assert "deploy/**" in finding.details


def test_arm_template_json_requires_its_own_containing_directory(tmp_path):
    root = tmp_path / "arm-repo"
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
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "azuredeploy.json").write_text(
        json.dumps(
            {
                "$schema": (
                    "https://schema.management.azure.com/schemas/2019-04-01/"
                    "deploymentTemplate.json#"
                ),
                "contentVersion": "1.0.0.0",
                "resources": [],
            }
        ),
        encoding="utf-8",
    )
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"
    assert "templates/**" in finding.details


def test_unrelated_json_file_is_never_mistaken_for_an_arm_template(tmp_path):
    """A `.json` file with no ARM `$schema` marker -- an ordinary config
    file, say -- must never manufacture a CODEOWNERS requirement."""
    root = tmp_path / "plain-json-repo"
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
    (root / "config").mkdir(parents=True)
    (root / "config" / "settings.json").write_text(
        json.dumps({"featureFlags": {"beta": True}}), encoding="utf-8"
    )
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" not in {f.finding_id for f in result.findings}


def test_repo_with_no_infrastructure_at_all_has_no_behavior_change(tmp_path):
    """The core conservatism requirement: a repository with no
    infra/IaC surface at all must produce the exact same CODEOWNERS
    result as before this feature existed -- discovery must add zero
    requirements, never subtract from or otherwise alter the fixed
    baseline patterns (here, still `not-verified` on live-only branch
    protection, never `must-fix` from a manufactured infra requirement)."""
    root = _write_clean_repo(tmp_path)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"
    assert finding.reason_code == "branch-protection-not-verified-statically"


def test_vendored_infra_named_directory_is_not_mistaken_for_repo_infrastructure(
    tmp_path,
):
    """A vendored dependency's own `infra/`-named directory, nested deep
    inside an excluded tree, must never manufacture a requirement for
    this repository's own CODEOWNERS file."""
    root = tmp_path / "vendored-infra-repo"
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
    (root / "node_modules" / "some-pkg" / "infra").mkdir(parents=True)
    (root / "node_modules" / "some-pkg" / "infra" / "main.bicep").write_text(
        "param location string\n", encoding="utf-8"
    )
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" not in {f.finding_id for f in result.findings}


# ---------------------------------------------------------------------------
# Approval review, item 3: GHCP-006 must additionally catch a
# production-environment deploy identity that is also used for a
# staging/development-environment deploy -- a dimension the coarser
# build-vs-deploy bucketing cannot see when both jobs are themselves
# deploy jobs. An unrecognized/custom environment name must never be
# guessed into either tier, and must never itself invent a must-fix.
# ---------------------------------------------------------------------------


def _write_environment_tier_repo(
    tmp_path: Path, *, prod_env: str, other_env: str, shared_identity: bool
) -> Path:
    root = tmp_path / "env-tier-repo"
    identity_a = "${{ secrets.SHARED_CLIENT_ID }}"
    identity_b = (
        identity_a if shared_identity else "${{ secrets.OTHER_CLIENT_ID }}"
    )
    _write_workflow(
        root,
        "deploy.yml",
        f"""\
        name: Deploy
        on:
          pull_request:
        permissions:
          contents: read
          id-token: write
        jobs:
          deploy-prod:
            runs-on: ubuntu-latest
            environment: {prod_env}
            permissions:
              contents: read
              id-token: write
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - uses: azure/login@92a5484dfaf04ca78a94597f4f19fea633851fa2
                with:
                  client-id: {identity_a}
                  tenant-id: ${{{{ secrets.AZURE_TENANT_ID }}}}
                  subscription-id: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}
          deploy-other:
            runs-on: ubuntu-latest
            environment: {other_env}
            permissions:
              contents: read
              id-token: write
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - uses: azure/login@92a5484dfaf04ca78a94597f4f19fea633851fa2
                with:
                  client-id: {identity_b}
                  tenant-id: ${{{{ secrets.AZURE_TENANT_ID }}}}
                  subscription-id: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}
        """,
    )
    return root


def test_production_identity_shared_with_staging_is_must_fix(tmp_path):
    root = _write_environment_tier_repo(
        tmp_path, prod_env="production", other_env="staging", shared_identity=True
    )
    workflow = root / ".github" / "workflows" / "deploy.yml"
    result = assess_workflow(workflow)
    from ghcp import _assess_identity_separation

    findings: list = []
    controls: dict = {}
    _assess_identity_separation((result,), None, findings, controls)
    assert controls["ghcp_identity_separation"] == "must-fix"
    finding = next(f for f in findings if f.finding_id == "GHCP-006")
    assert finding.reason_code == "production-environment-identity-overlap"


def test_prod_alias_shared_with_dev_alias_is_must_fix(tmp_path):
    """`prod`/`dev` are recognized aliases for the same two tiers as
    `production`/`development`."""
    root = _write_environment_tier_repo(
        tmp_path, prod_env="prod", other_env="dev", shared_identity=True
    )
    workflow = root / ".github" / "workflows" / "deploy.yml"
    result = assess_workflow(workflow)
    from ghcp import _assess_identity_separation

    findings: list = []
    controls: dict = {}
    _assess_identity_separation((result,), None, findings, controls)
    assert controls["ghcp_identity_separation"] == "must-fix"
    finding = next(f for f in findings if f.finding_id == "GHCP-006")
    assert finding.reason_code == "production-environment-identity-overlap"


def test_production_identity_distinct_from_staging_is_not_flagged_by_tier_check(
    tmp_path,
):
    root = _write_environment_tier_repo(
        tmp_path, prod_env="production", other_env="staging", shared_identity=False
    )
    workflow = root / ".github" / "workflows" / "deploy.yml"
    result = assess_workflow(workflow)
    from ghcp import _assess_identity_separation

    findings: list = []
    controls: dict = {}
    _assess_identity_separation((result,), None, findings, controls)
    assert not any(
        f.reason_code == "production-environment-identity-overlap" for f in findings
    )


def test_unrecognized_custom_environment_name_is_never_guessed_into_a_tier(
    tmp_path,
):
    """A custom environment name (e.g. `qa`) sharing an identity with a
    `staging` deploy must never be flagged by this specific
    production-vs-non-production check -- it simply falls outside both
    recognized tiers."""
    root = _write_environment_tier_repo(
        tmp_path, prod_env="qa", other_env="staging", shared_identity=True
    )
    workflow = root / ".github" / "workflows" / "deploy.yml"
    result = assess_workflow(workflow)
    from ghcp import _assess_identity_separation

    findings: list = []
    controls: dict = {}
    _assess_identity_separation((result,), None, findings, controls)
    assert not any(
        f.reason_code == "production-environment-identity-overlap" for f in findings
    )


def test_environment_tier_helper_never_guesses_unknown_names():
    from ghcp import _environment_tier

    assert _environment_tier("production") == "production"
    assert _environment_tier("Prod") == "production"
    assert _environment_tier("staging") == "non-production"
    assert _environment_tier("Development") == "non-production"
    assert _environment_tier("qa") is None
    assert _environment_tier("uat") is None
    assert _environment_tier("") is None


def test_deploy_job_without_declared_environment_is_unaffected_by_tier_check(
    tmp_path,
):
    """A deploy job that declares no `environment:` at all contributes
    nothing to `environment_identity_refs`, and must therefore never be
    considered by the production-vs-non-production tier check -- it only
    ever falls through to the existing deploy/non-deploy comparison."""
    root = tmp_path / "no-environment-repo"
    _write_workflow(
        root,
        "deploy.yml",
        """\
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
                  client-id: ${{ secrets.AZURE_CLIENT_ID }}
                  tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                  subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "deploy.yml")
    assert result.environment_identity_refs == ()



# ---------------------------------------------------------------------------
# Readiness review, item 1: deployment detection must resolve a local
# reusable-workflow call (``uses: ./.github/workflows/callee.yml``) and
# inspect an ``azure/cli`` step's own ``inlineScript:`` input for a pinned
# deployment command -- a push-triggered caller that delegates its actual
# deployment to either mechanism must still fail rule 1 (GHCP-001), exactly
# as if it had deployed directly. Resolution must never hang on a cycle,
# recurse unbounded, or escape the repository root.
# ---------------------------------------------------------------------------


def test_push_caller_of_local_reusable_deploying_workflow_is_must_fix(tmp_path):
    root = tmp_path / "reusable-deploy-repo"
    _write_workflow(
        root,
        "caller.yml",
        """\
        name: Caller
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          ship:
            uses: ./.github/workflows/callee.yml
        """,
    )
    _write_workflow(
        root,
        "callee.yml",
        """\
        name: Callee
        on:
          workflow_call:
        jobs:
          release:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "caller.yml")
    assert result.is_deploy is True
    assert result.pr_gate == "must-fix"


def test_push_caller_of_local_reusable_non_deploying_workflow_still_passes(tmp_path):
    root = tmp_path / "reusable-nondeploy-repo"
    _write_workflow(
        root,
        "caller.yml",
        """\
        name: Caller
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          run-it:
            uses: ./.github/workflows/callee.yml
        """,
    )
    _write_workflow(
        root,
        "callee.yml",
        """\
        name: Callee
        on:
          workflow_call:
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - run: echo "build only"
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "caller.yml")
    assert result.is_deploy is False
    assert result.pr_gate == "pass"


def test_pull_request_caller_of_local_reusable_deploying_workflow_is_unaffected(
    tmp_path,
):
    """Rule 1 only gates a *push*-triggered deploy; a pull_request-
    triggered caller of a deploying reusable workflow is still correctly
    classified `is_deploy=True` (for other checks, e.g. rule 6), but must
    not itself fail rule 1 -- it is already PR-gated."""
    root = tmp_path / "reusable-deploy-pr-repo"
    _write_workflow(
        root,
        "caller.yml",
        """\
        name: Caller
        on:
          pull_request:
        permissions:
          contents: read
        jobs:
          ship:
            uses: ./.github/workflows/callee.yml
        """,
    )
    _write_workflow(
        root,
        "callee.yml",
        """\
        name: Callee
        on:
          workflow_call:
        jobs:
          release:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "caller.yml")
    assert result.is_deploy is True
    assert result.pr_gate == "pass"


def test_transitively_nested_reusable_workflow_deploy_is_detected(tmp_path):
    """A calls B (non-deploying) which itself calls C (deploying) -- the
    deploy evidence must propagate all the way back to A."""
    root = tmp_path / "reusable-nested-repo"
    _write_workflow(
        root,
        "a.yml",
        """\
        name: A
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          delegate:
            uses: ./.github/workflows/b.yml
        """,
    )
    _write_workflow(
        root,
        "b.yml",
        """\
        name: B
        on:
          workflow_call:
        jobs:
          delegate:
            uses: ./.github/workflows/c.yml
        """,
    )
    _write_workflow(
        root,
        "c.yml",
        """\
        name: C
        on:
          workflow_call:
        jobs:
          release:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - run: az webapp deploy --resource-group rg --name app
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "a.yml")
    assert result.is_deploy is True
    assert result.pr_gate == "must-fix"


def test_cyclic_reusable_workflow_calls_do_not_crash_or_falsely_classify_as_deploy(
    tmp_path,
):
    """Two reusable workflows calling each other must never hang, recurse
    unboundedly, or crash -- and, since neither ever actually declares any
    deploy evidence of its own, must not be misclassified as deploying."""
    root = tmp_path / "reusable-cycle-repo"
    _write_workflow(
        root,
        "cyc-a.yml",
        """\
        name: CycA
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          delegate:
            uses: ./.github/workflows/cyc-b.yml
        """,
    )
    _write_workflow(
        root,
        "cyc-b.yml",
        """\
        name: CycB
        on:
          workflow_call:
        jobs:
          delegate:
            uses: ./.github/workflows/cyc-a.yml
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "cyc-a.yml")
    assert result.is_deploy is False
    assert result.pr_gate == "pass"


def test_reusable_workflow_path_escape_is_not_followed(tmp_path):
    """A `uses:` reference attempting to traverse outside the repository
    root must never be resolved/read -- it simply contributes no deploy
    evidence via this mechanism, rather than crashing or being silently
    followed outside the repository."""
    root = tmp_path / "reusable-escape-repo"
    _write_workflow(
        root,
        "caller.yml",
        """\
        name: Caller
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          delegate:
            uses: ./../../outside.yml
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "caller.yml")
    assert result.is_deploy is False
    assert result.pr_gate == "pass"


def test_external_reusable_workflow_call_is_not_statically_resolved(tmp_path):
    """A reusable-workflow call into a *different* repository
    (``org/repo/.github/workflows/x.yml@ref``, not `./`-prefixed) cannot
    be resolved statically without fetching that other repository -- it
    must never be treated as deploy evidence (nor crash)."""
    root = tmp_path / "reusable-external-repo"
    _write_workflow(
        root,
        "caller.yml",
        """\
        name: Caller
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          delegate:
            uses: octo-org/other-repo/.github/workflows/deploy.yml@main
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "caller.yml")
    assert result.is_deploy is False
    assert result.pr_gate == "pass"


def test_azure_cli_inline_script_with_deploy_command_on_push_is_must_fix(tmp_path):
    root = tmp_path / "azure-cli-inline-deploy-repo"
    _write_workflow(
        root,
        "deploy.yml",
        """\
        name: Deploy
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          run-script:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - uses: azure/cli@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                with:
                  inlineScript: |
                    az webapp deploy --resource-group rg --name app
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "deploy.yml")
    assert result.is_deploy is True
    assert result.pr_gate == "must-fix"


def test_azure_cli_inline_script_without_deploy_command_is_unaffected(tmp_path):
    root = tmp_path / "azure-cli-inline-nondeploy-repo"
    _write_workflow(
        root,
        "check.yml",
        """\
        name: Check
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          run-script:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - uses: azure/cli@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                with:
                  inlineScript: |
                    az account show
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "check.yml")
    assert result.is_deploy is False
    assert result.pr_gate == "pass"


def test_azure_cli_inline_script_deploy_scoped_correctly_to_its_own_job(tmp_path):
    """`_is_deploy_job` (rule 6's per-job classifier) must also see a
    deploying `azure/cli` `inlineScript:`, scoped to that job alone."""
    from ghcp import _is_deploy_job, _load_workflow_document

    root = tmp_path / "azure-cli-inline-job-scope-repo"
    workflow_path = _write_workflow(
        root,
        "mixed.yml",
        """\
        name: Mixed
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
              - run: echo "build"
          ship:
            runs-on: ubuntu-latest
            steps:
              - uses: azure/cli@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                with:
                  inlineScript: |
                    az webapp deploy --resource-group rg --name app
        """,
    )
    document = _load_workflow_document(workflow_path)
    jobs = document["jobs"]
    assert _is_deploy_job("build", jobs["build"]) is False
    assert _is_deploy_job("ship", jobs["ship"]) is True


def test_resolve_local_reusable_workflow_path_rejects_non_local_ref(tmp_path):
    from ghcp import _resolve_local_reusable_workflow_path

    root = tmp_path / "root-only"
    root.mkdir()
    assert (
        _resolve_local_reusable_workflow_path(
            root, "octo-org/other-repo/.github/workflows/x.yml@main"
        )
        is None
    )


def test_resolve_local_reusable_workflow_path_rejects_traversal_segment(tmp_path):
    from ghcp import _resolve_local_reusable_workflow_path

    root = tmp_path / "root-only"
    root.mkdir()
    assert _resolve_local_reusable_workflow_path(root, "./../outside.yml") is None


def test_resolve_local_reusable_workflow_path_rejects_missing_file(tmp_path):
    from ghcp import _resolve_local_reusable_workflow_path

    root = tmp_path / "root-only"
    root.mkdir()
    assert (
        _resolve_local_reusable_workflow_path(
            root, "./.github/workflows/does-not-exist.yml"
        )
        is None
    )


# ---------------------------------------------------------------------------
# Readiness review, item 2: rule 3's CTK/application-probe check must
# require a *recognized runner invocation* -- the actual command being run
# is literally `ctk`/`probes` -- never merely a marker word appearing
# anywhere in a `grep`/`cat`/`find`/`echo`-argument or a log filename.
# ---------------------------------------------------------------------------


def test_ctk_probe_via_grep_command_does_not_satisfy_rule_3(tmp_path):
    workflow = _workflow_with_ci_probe_run(
        tmp_path, 'grep -r "ctk application-probe" .'
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_via_cat_log_filename_does_not_satisfy_rule_3(tmp_path):
    workflow = _workflow_with_ci_probe_run(
        tmp_path, "cat ctk-application-probe.log"
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_via_find_command_does_not_satisfy_rule_3(tmp_path):
    workflow = _workflow_with_ci_probe_run(
        tmp_path, "find . -iname '*ctk*application-probe*'"
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_via_real_run_but_marker_only_in_log_redirect_filename_still_fails(
    tmp_path,
):
    """A step that runs something real, but only mentions the CTK/
    application-probe markers in a *log filename* it redirects output to
    -- never actually running either -- must not satisfy rule 3."""
    workflow = _workflow_with_ci_probe_run(
        tmp_path, "pytest tests/ > ctk-application-probe-results.log"
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "must-fix"


def test_ctk_probe_python3_m_invocation_still_satisfies_rule_3(tmp_path):
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        'python3 -m ctk run-vectors\n                      '
        'python3 -m probes run-application-probe',
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "pass"


def test_ctk_probe_dot_slash_invocation_still_satisfies_rule_3(tmp_path):
    workflow = _workflow_with_ci_probe_run(
        tmp_path,
        "./ctk run-vectors\n                      "
        "./probes run-application-probe",
    )
    result = assess_workflow(workflow)
    assert result.ci_probes == "pass"


def test_ctk_probe_recognized_runner_helper_rejects_grep_and_accepts_real_invocation():
    from ghcp import _ci_probes_satisfied

    assert _ci_probes_satisfied('grep -r "ctk application-probe" .') is False
    assert _ci_probes_satisfied("cat ctk-application-probe.log") is False
    assert (
        _ci_probes_satisfied(
            "python -m ctk run-vectors\npython -m probes run-application-probe"
        )
        is True
    )


# ---------------------------------------------------------------------------
# Readiness review, item 3: applicable infrastructure ownership discovery
# must also recognize concrete deployment descriptors -- an Azure Developer
# CLI manifest, a Dockerfile variant, a Kubernetes manifest, or a Helm
# chart -- actually present in the repository, deterministically and
# conservatively (never inventing an absent category).
# ---------------------------------------------------------------------------


def _write_ctk_probe_ci_workflow(root: Path) -> None:
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


def test_azure_yaml_manifest_at_root_requires_codeowners_coverage(tmp_path):
    root = tmp_path / "azd-repo"
    _write_ctk_probe_ci_workflow(root)
    (root / "azure.yaml").write_text("name: myapp\n", encoding="utf-8")
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"
    assert "azure.yaml" in finding.details


def test_azure_yaml_manifest_with_codeowners_coverage_clears_ghcp_002(tmp_path):
    root = tmp_path / "azd-covered-repo"
    _write_ctk_probe_ci_workflow(root)
    (root / "azure.yaml").write_text("name: myapp\n", encoding="utf-8")
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS + ("azure.yaml",))
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" not in {f.finding_id for f in result.findings}


def test_dockerfile_variant_in_subdirectory_requires_its_own_containing_directory(
    tmp_path,
):
    root = tmp_path / "dockerfile-repo"
    _write_ctk_probe_ci_workflow(root)
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "Dockerfile.prod").write_text(
        "FROM python:3.12\n", encoding="utf-8"
    )
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"
    assert "backend/**" in finding.details


def test_bare_root_dockerfile_requires_exact_file_coverage(tmp_path):
    root = tmp_path / "root-dockerfile-repo"
    _write_ctk_probe_ci_workflow(root)
    (root / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"
    assert "Dockerfile" in finding.details


def test_kubernetes_manifest_requires_its_own_containing_directory(tmp_path):
    root = tmp_path / "k8s-repo"
    _write_ctk_probe_ci_workflow(root)
    (root / "k8s").mkdir(parents=True)
    (root / "k8s" / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\n",
        encoding="utf-8",
    )
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"
    assert "k8s/**" in finding.details


def test_unrelated_yaml_file_is_never_mistaken_for_a_kubernetes_manifest(tmp_path):
    root = tmp_path / "plain-yaml-repo"
    _write_ctk_probe_ci_workflow(root)
    (root / "config").mkdir(parents=True)
    (root / "config" / "settings.yaml").write_text(
        "featureFlags:\n  beta: true\n", encoding="utf-8"
    )
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    live_github = {
        "default_branch": "main",
        "branch_protection": {"main": _STRONG_BRANCH_PROTECTION},
    }
    result = assess_change_plane(root, live_github=live_github, live_azure=None)
    assert "GHCP-002" not in {f.finding_id for f in result.findings}


def test_helm_chart_yaml_requires_its_own_containing_directory(tmp_path):
    root = tmp_path / "helm-repo"
    _write_ctk_probe_ci_workflow(root)
    (root / "charts" / "myapp" / "templates").mkdir(parents=True)
    (root / "charts" / "myapp" / "Chart.yaml").write_text(
        "apiVersion: v2\nname: myapp\n", encoding="utf-8"
    )
    _write_codeowners(root, _CLEAN_CODEOWNERS_PATTERNS)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "must-fix"
    assert "charts/**" in finding.details


def test_deployment_descriptor_repo_with_no_matching_category_has_no_behavior_change(
    tmp_path,
):
    """A repository with none of the newly-recognized deployment
    descriptors (or any prior infra/IaC surface) at all must produce the
    exact same CODEOWNERS result as before this feature existed."""
    root = _write_clean_repo(tmp_path)
    result = assess_change_plane(root, live_github=None, live_azure=None)
    finding = next(f for f in result.findings if f.finding_id == "GHCP-002")
    assert finding.status == "not-verified"
    assert finding.reason_code == "branch-protection-not-verified-statically"


def test_discover_infrastructure_requirements_covers_all_deployment_descriptors(
    tmp_path,
):
    from ghcp import _discover_infrastructure_codeowners_requirements

    root = tmp_path / "all-descriptors-repo"
    root.mkdir()
    (root / "azure.yaml").write_text("name: myapp\n", encoding="utf-8")
    (root / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    (root / "backend").mkdir()
    (root / "backend" / "Dockerfile.dev").write_text(
        "FROM python:3.12\n", encoding="utf-8"
    )
    (root / "k8s").mkdir()
    (root / "k8s" / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\n", encoding="utf-8"
    )
    (root / "charts" / "myapp").mkdir(parents=True)
    (root / "charts" / "myapp" / "Chart.yaml").write_text(
        "apiVersion: v2\nname: myapp\n", encoding="utf-8"
    )
    (root / "unrelated.yaml").write_text("foo: bar\n", encoding="utf-8")
    discovered = _discover_infrastructure_codeowners_requirements(root)
    assert discovered == (
        "Dockerfile",
        "azure.yaml",
        "backend/**",
        "charts/**",
        "k8s/**",
    )


# ---------------------------------------------------------------------------
# One remaining Important issue: local reusable-workflow tracing must
# cover GitHub's own full supported nesting bound -- "a maximum of ten
# levels of workflows... the top-level caller workflow and up to nine
# levels of reusable workflows" -- not stop short of it. A deploy that
# only appears at, say, the ninth (last supported) level must still be
# detected exactly as if it were direct; and a chain that goes beyond
# what this static traversal can safely resolve must fail closed
# (conservatively treated as a deploy) rather than silently reported as
# non-deploying merely because tracing had to stop somewhere.
# ---------------------------------------------------------------------------


def _write_reusable_workflow_chain(
    root: Path, levels: int, *, deploy_at: Optional[int] = None
) -> Path:
    """Write a push-triggered ``caller.yml`` that calls a local reusable
    workflow ``l1.yml``, which calls ``l2.yml``, ... down to
    ``l{levels}.yml`` -- an unbroken chain exactly ``levels`` reusable
    workflows deep. When `deploy_at` is given, that one level (1-indexed)
    actually deploys via a raw `az webapp deploy` run command; every
    other level in the chain is otherwise inert (no deploy evidence of
    its own, job ids/names deliberately free of "deploy"). Returns the
    path to `caller.yml`.
    """
    _write_workflow(
        root,
        "caller.yml",
        """\
        name: Caller
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          delegate:
            uses: ./.github/workflows/l1.yml
        """,
    )
    for level in range(1, levels + 1):
        if level == deploy_at:
            body = f"""\
                name: L{level}
                on:
                  workflow_call:
                jobs:
                  release:
                    runs-on: ubuntu-latest
                    steps:
                      - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                      - run: az webapp deploy --resource-group rg --name app
                """
        elif level < levels:
            body = f"""\
                name: L{level}
                on:
                  workflow_call:
                jobs:
                  delegate:
                    uses: ./.github/workflows/l{level + 1}.yml
                """
        else:
            body = f"""\
                name: L{level}
                on:
                  workflow_call:
                jobs:
                  noop:
                    runs-on: ubuntu-latest
                    steps:
                      - run: echo done
                """
        _write_workflow(root, f"l{level}.yml", body)
    return root / ".github" / "workflows" / "caller.yml"


def test_deploy_at_maximum_supported_reusable_workflow_depth_is_must_fix(tmp_path):
    """GitHub supports up to nine levels of chained reusable workflows;
    a deploy that only appears at the ninth (last supported) level must
    still be traced and classified as a deploy -- not silently missed
    because tracing stopped short of GitHub's own actual bound."""
    root = tmp_path / "max-depth-deploy-repo"
    caller = _write_reusable_workflow_chain(root, levels=9, deploy_at=9)
    result = assess_workflow(caller)
    assert result.is_deploy is True
    assert result.pr_gate == "must-fix"


def test_nine_level_chain_with_no_deploy_anywhere_still_passes(tmp_path):
    """A chain that is fully within GitHub's supported nesting bound and
    genuinely never deploys anywhere along it must not be conservatively
    misclassified as a deploy -- fail-closed applies only once tracing
    can no longer safely continue, not to every deep chain."""
    root = tmp_path / "max-depth-nondeploy-repo"
    caller = _write_reusable_workflow_chain(root, levels=9, deploy_at=None)
    result = assess_workflow(caller)
    assert result.is_deploy is False
    assert result.pr_gate == "pass"


def test_over_depth_reusable_workflow_chain_fails_closed_as_deploy(tmp_path):
    """A chain ten reusable workflows deep goes beyond what GitHub's own
    documented bound (and hence this static traversal) can safely
    resolve -- even with no declared deploy evidence anywhere in the
    part of the chain that can be traced, it must fail closed
    (conservatively treated as a deploy) rather than be reported as a
    definite non-deploy that this analysis cannot actually prove."""
    root = tmp_path / "over-depth-repo"
    caller = _write_reusable_workflow_chain(root, levels=10, deploy_at=None)
    result = assess_workflow(caller)
    assert result.is_deploy is True
    assert result.pr_gate == "must-fix"


def test_cyclic_reusable_workflow_chain_still_terminates_without_false_positive(
    tmp_path,
):
    """A cycle is a revisit, not an exceeded-depth boundary: it is
    already fully accounted for on the branch that first visited it, so
    it must continue to resolve to non-deploying (never hang, crash, or
    conservatively fail closed just because a cycle exists)."""
    root = tmp_path / "cycle-still-nondeploy-repo"
    _write_workflow(
        root,
        "cyc-a.yml",
        """\
        name: CycA
        on:
          push:
            branches: [main]
        permissions:
          contents: read
        jobs:
          delegate:
            uses: ./.github/workflows/cyc-b.yml
        """,
    )
    _write_workflow(
        root,
        "cyc-b.yml",
        """\
        name: CycB
        on:
          workflow_call:
        jobs:
          delegate:
            uses: ./.github/workflows/cyc-a.yml
        """,
    )
    result = assess_workflow(root / ".github" / "workflows" / "cyc-a.yml")
    assert result.is_deploy is False
    assert result.pr_gate == "pass"


def test_local_reusable_workflow_deploys_helper_fails_closed_exactly_at_bound():
    from ghcp import _MAX_REUSABLE_WORKFLOW_DEPTH, _local_reusable_workflow_deploys

    # A depth already at (or past) the supported bound must fail closed
    # regardless of whether the reference even resolves -- tracing
    # cannot safely continue at all at this point.
    assert (
        _local_reusable_workflow_deploys(
            Path("/nonexistent"),
            "./.github/workflows/whatever.yml",
            None,
            _MAX_REUSABLE_WORKFLOW_DEPTH,
        )
        is True
    )


# ---------------------------------------------------------------------------
# Task 8: optional, read-only live GitHub/Azure evidence collection.
#
# ``collect_live_github``/``collect_live_azure`` never touch a real network
# or spawn a real process in these tests: every command runner below is an
# injected test double that only ever records the exact command lists it
# was asked to run and returns a scripted response. A permission failure
# (HTTP 401/403 surfaced by a non-zero exit), a missing CLI binary, an
# unparseable response, or a missing required input must never be reported
# as ``"pass"`` -- only ever ``"not-verified"`` with the specific GHCP
# control the missing evidence affects.
# ---------------------------------------------------------------------------


class _FakeRunner:
    """Injectable command runner: records every command it is asked to run,
    in order, and returns the next scripted ``subprocess.CompletedProcess``
    for it -- never actually spawns a process or touches the network."""

    def __init__(self, responses: Sequence[subprocess.CompletedProcess]):
        self.commands: List[List[str]] = []
        self._responses = list(responses)

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess:
        self.commands.append(list(command))
        if self._responses:
            return self._responses.pop(0)
        return subprocess.CompletedProcess(args=list(command), returncode=0, stdout="{}", stderr="")


def _ok(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _valid_github_responses() -> List[subprocess.CompletedProcess]:
    """A fresh, independently-mutable list of five ``_ok(...)`` responses,
    one per ``collect_live_github`` endpoint in call order, each shaped
    exactly the way that endpoint's real (non-empty, fully-populated)
    GitHub API response always is -- the baseline every adversarial
    "one endpoint is malformed" test starts from and overrides exactly
    one entry of.
    """
    return [
        _ok("[]"),
        _ok(json.dumps({"required_status_checks": {"strict": True, "contexts": []}})),
        _ok(json.dumps({"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False})),
        _ok(json.dumps({"environments": []})),
        _ok(json.dumps({"include_claim_keys": []})),
    ]


def _forbidden() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="HTTP 403 Forbidden"
    )


@pytest.fixture
def fake_runner() -> _FakeRunner:
    return _FakeRunner(
        [
            _ok("[]"),
            _ok(json.dumps({"required_status_checks": {"strict": True, "contexts": []}})),
            _ok(json.dumps({"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False})),
            _ok(json.dumps({"environments": [{"name": "staging", "protection_rules": []}]})),
            _ok(json.dumps({"include_claim_keys": ["repository", "ref"]})),
        ]
    )


@pytest.fixture
def fake_runner_403() -> _FakeRunner:
    return _FakeRunner([_forbidden()])


def test_live_github_collects_rules_and_required_checks(fake_runner):
    collect_live_github("aiappsgbb/threadlight-skills", "main", run=fake_runner)
    assert fake_runner.commands == [
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/rulesets?includes_parents=true"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/branches/main/protection"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/actions/permissions/workflow"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/environments"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/actions/oidc/customization/sub"],
    ]


def test_live_github_success_is_pass_with_evidence_and_no_finding(fake_runner):
    result = collect_live_github("aiappsgbb/threadlight-skills", "main", run=fake_runner)
    assert isinstance(result, LiveEvidenceResult)
    assert result.status == "pass"
    assert result.finding is None
    # collect_live_github is handed only a repository/branch identifier, never
    # a local checkout, so it has no trustworthy 40-hex source_commit to bind
    # -- it must never fabricate an EvidenceRef (e.g. with source_commit="")
    # merely to have something to return.
    assert result.evidence == ()
    assert result.data["environments"] == {"staging": {"protected": False}}


def test_missing_live_permissions_remain_not_verified(fake_runner_403):
    evidence = collect_live_github("owner/repo", "main", run=fake_runner_403)
    assert evidence.status == "not-verified"
    assert evidence.finding.finding_id == "GHCP-002"
    assert evidence.evidence == ()


def test_live_github_missing_cli_is_not_verified():
    def _raise_missing_cli(command):
        raise FileNotFoundError("gh")

    result = collect_live_github("owner/repo", "main", run=_raise_missing_cli)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-002"
    assert result.evidence == ()


def test_live_github_malformed_response_is_not_verified():
    def _malformed(command):
        return subprocess.CompletedProcess(args=list(command), returncode=0, stdout="not json", stderr="")

    result = collect_live_github("owner/repo", "main", run=_malformed)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-002"
    assert result.evidence == ()


def test_live_github_never_records_raw_stderr(fake_runner_403):
    result = collect_live_github("owner/repo", "main", run=fake_runner_403)
    assert "Forbidden" not in result.finding.details
    assert "Forbidden" not in json.dumps(result.data)
    assert "403" not in result.finding.details


def test_live_github_cli_failure_retains_exit_code_and_error_class(fake_runner_403):
    result = collect_live_github("owner/repo", "main", run=fake_runner_403)
    assert result.data["exit_code"] == 1
    assert result.data["error_class"] == "cli-error"
    assert "exit code 1" in result.finding.details


def test_live_github_oidc_endpoint_failure_is_ghcp_005():
    responses = _valid_github_responses()
    responses[4] = _forbidden()
    runner = _FakeRunner(responses)
    result = collect_live_github("owner/repo", "main", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-005"
    assert result.evidence == ()


@pytest.mark.parametrize(
    "index,malformed_stdout,expected_finding_id",
    [
        (0, "{}", "GHCP-002"),  # rulesets must be a JSON array, not a mapping
        (0, "[1]", "GHCP-002"),  # rulesets entries must each be a JSON object
        (1, "[]", "GHCP-002"),  # branch protection must be a JSON object
        (1, "{}", "GHCP-002"),  # branch protection must not be an empty object
        (2, "[]", "GHCP-002"),  # actions/permissions/workflow must be a JSON object
        (2, "{}", "GHCP-002"),  # actions/permissions/workflow must not be empty
        (2, json.dumps({"other": "field"}), "GHCP-002"),  # must name default_workflow_permissions
        (3, "[]", "GHCP-002"),  # environments must be a JSON object with an "environments" list
        (3, json.dumps({"environments": [{"protection_rules": []}]}), "GHCP-002"),  # entry needs a name
        (3, json.dumps({"environments": [{"name": "prod", "protection_rules": "nope"}]}), "GHCP-002"),
        (4, "[]", "GHCP-005"),  # oidc customization must be a JSON object
        (4, "{}", "GHCP-005"),  # oidc customization must not be empty
        (4, json.dumps({"other": "field"}), "GHCP-005"),  # must name include_claim_keys
    ],
)
def test_live_github_malformed_endpoint_shape_is_not_verified(
    index, malformed_stdout, expected_finding_id
):
    responses = _valid_github_responses()
    responses[index] = _ok(malformed_stdout)
    runner = _FakeRunner(responses)
    result = collect_live_github("owner/repo", "main", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == expected_finding_id
    assert result.evidence == ()


def test_live_github_environments_missing_key_is_not_verified():
    responses = _valid_github_responses()
    responses[3] = _ok("{}")
    runner = _FakeRunner(responses)
    result = collect_live_github("owner/repo", "main", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-002"


# ---------------------------------------------------------------------------
# Round 6, issue 1: an incomplete/ambiguous first page of a pageable
# GitHub list endpoint must never be trusted as a complete evidence set --
# this must never require adding a pagination flag or a sixth command,
# only conservatively refusing to trust a page that cannot itself be
# proven complete (via an endpoint's own completeness signal, or a bare
# list sitting at exactly the API's own default page-size boundary).
# ---------------------------------------------------------------------------


def test_live_github_rulesets_below_page_capacity_is_pass():
    responses = _valid_github_responses()
    below_capacity = [{"id": i, "name": f"rs{i}"} for i in range(ghcp._GITHUB_DEFAULT_PAGE_SIZE - 1)]
    responses[0] = _ok(json.dumps(below_capacity))
    runner = _FakeRunner(responses)
    result = collect_live_github("owner/repo", "main", run=runner)
    assert result.status == "pass"
    assert result.finding is None


def test_live_github_rulesets_at_page_capacity_is_not_verified():
    # A bare JSON array (no ``gh api`` pagination envelope/total_count is
    # ever returned for this endpoint) sitting at exactly the API's own
    # default page size can never be proven to be the *entire* rulesets
    # list rather than a silently truncated first page -- this project
    # never adds a pagination flag or a sixth command to resolve that
    # ambiguity, it only refuses to trust the ambiguous result.
    responses = _valid_github_responses()
    at_capacity = [{"id": i, "name": f"rs{i}"} for i in range(ghcp._GITHUB_DEFAULT_PAGE_SIZE)]
    responses[0] = _ok(json.dumps(at_capacity))
    runner = _FakeRunner(responses)
    result = collect_live_github("owner/repo", "main", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-002"
    assert result.evidence == ()


def test_live_github_environments_total_count_matches_length_is_pass():
    responses = _valid_github_responses()
    responses[3] = _ok(
        json.dumps(
            {
                "total_count": 1,
                "environments": [{"name": "staging", "protection_rules": []}],
            }
        )
    )
    runner = _FakeRunner(responses)
    result = collect_live_github("owner/repo", "main", run=runner)
    assert result.status == "pass"
    assert result.finding is None


def test_live_github_environments_total_count_mismatch_is_not_verified():
    # ``total_count`` naming more entries than were actually returned
    # proves this is only a partial page -- an explicit completeness
    # signal directly contradicting what was actually observed, which
    # must never be trusted as complete evidence.
    responses = _valid_github_responses()
    responses[3] = _ok(
        json.dumps(
            {
                "total_count": 2,
                "environments": [{"name": "staging", "protection_rules": []}],
            }
        )
    )
    runner = _FakeRunner(responses)
    result = collect_live_github("owner/repo", "main", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-002"
    assert result.evidence == ()


def test_live_github_exact_five_commands_unchanged_by_completeness_check(fake_runner):
    # Proving incomplete-page detection never adds a sixth command or a
    # pagination flag to the fixed five-command contract.
    collect_live_github("aiappsgbb/threadlight-skills", "main", run=fake_runner)
    assert fake_runner.commands == [
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/rulesets?includes_parents=true"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/branches/main/protection"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/actions/permissions/workflow"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/environments"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/actions/oidc/customization/sub"],
    ]


def test_live_github_success_includes_collected_sha256_digest_over_canonical_data(fake_runner):
    result = collect_live_github("aiappsgbb/threadlight-skills", "main", run=fake_runner)
    digest = result.data["collected_sha256"]
    assert isinstance(digest, str) and digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
    # The digest must be computed over exactly the rest of the returned
    # data -- never including itself -- so recomputing it over every other
    # key must reproduce the exact same value.
    payload_without_digest = {k: v for k, v in result.data.items() if k != "collected_sha256"}
    expected = f"sha256:{canonical.sha256_hex(canonical.canonical_bytes(payload_without_digest))}"
    assert digest == expected


def test_live_github_digest_changes_when_state_changes():
    responses_a = _valid_github_responses()
    responses_b = _valid_github_responses()
    responses_b[0] = _ok('[{"id": 1, "name": "a-ruleset"}]')
    runner_a = _FakeRunner(responses_a)
    runner_b = _FakeRunner(responses_b)
    result_a = collect_live_github("owner/repo", "main", run=runner_a)
    result_b = collect_live_github("owner/repo", "main", run=runner_b)
    assert result_a.status == result_b.status == "pass"
    assert result_a.data["collected_sha256"] != result_b.data["collected_sha256"]


def test_live_github_digest_is_invariant_to_ruleset_return_order():
    # The live GitHub API offers no ordering guarantee across two separate
    # ``rulesets`` calls; the digest must depend only on the *set* of
    # rulesets actually returned, never on the transient order two
    # equivalent calls happened to return them in.
    responses_a = _valid_github_responses()
    responses_a[0] = _ok(json.dumps([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]))
    responses_b = _valid_github_responses()
    responses_b[0] = _ok(json.dumps([{"id": 2, "name": "b"}, {"id": 1, "name": "a"}]))
    result_a = collect_live_github("owner/repo", "main", run=_FakeRunner(responses_a))
    result_b = collect_live_github("owner/repo", "main", run=_FakeRunner(responses_b))
    assert result_a.data["collected_sha256"] == result_b.data["collected_sha256"]


def test_live_github_digest_is_bound_to_repository_and_branch_scope():
    result_a = collect_live_github("owner/repo-a", "main", run=_FakeRunner(_valid_github_responses()))
    result_b = collect_live_github("owner/repo-b", "main", run=_FakeRunner(_valid_github_responses()))
    assert result_a.data["collected_sha256"] != result_b.data["collected_sha256"]
    assert result_a.data["repository"] == "owner/repo-a"
    assert result_b.data["repository"] == "owner/repo-b"


@pytest.fixture
def fake_azure_runner() -> _FakeRunner:
    return _FakeRunner(
        [
            _ok("[]"),
            _ok(
                json.dumps(
                    [
                        {"roleDefinitionName": "Reader"},
                        {"roleDefinitionName": "Contributor"},
                        {"roleDefinitionName": "Reader"},
                    ]
                )
            ),
            _ok(json.dumps([{"roleName": "Contributor"}])),
            _ok(json.dumps([{"roleName": "Reader"}])),
        ]
    )


def test_live_azure_collects_identity_and_role_evidence(fake_azure_runner):
    result = collect_live_azure(
        "SUBSCRIPTION", "STAGING_RG", "DEPLOY_IDENTITY", run=fake_azure_runner
    )
    assert fake_azure_runner.commands == [
        [
            "az", "identity", "federated-credential", "list",
            "--identity-name", "DEPLOY_IDENTITY",
            "--resource-group", "STAGING_RG",
            "--subscription", "SUBSCRIPTION",
            "-o", "json",
        ],
        [
            "az", "role", "assignment", "list",
            "--assignee", "DEPLOY_IDENTITY",
            "--resource-group", "STAGING_RG",
            "--subscription", "SUBSCRIPTION",
            "--all",
            "-o", "json",
        ],
        [
            "az", "role", "definition", "list",
            "--name", "Contributor",
            "--subscription", "SUBSCRIPTION",
            "-o", "json",
        ],
        [
            "az", "role", "definition", "list",
            "--name", "Reader",
            "--subscription", "SUBSCRIPTION",
            "-o", "json",
        ],
    ]
    assert result.status == "pass"
    assert result.finding is None
    # collect_live_azure is handed only a subscription/resource-group/identity
    # identifier, never a repository checkout, so it has no trustworthy 40-hex
    # source_commit to bind an EvidenceRef to, and the subscription must never
    # be misused as EvidenceRef.repository either -- so no evidence is ever
    # returned by this collector.
    assert result.evidence == ()
    for command in fake_azure_runner.commands:
        joined = " ".join(command).lower()
        assert "delete" not in joined
        assert "create" not in joined
        assert " set " not in f" {joined} "
        assert "secret" not in joined
        assert "password" not in joined
        assert "token" not in joined


def test_live_azure_success_includes_collected_sha256_digest_over_canonical_data(fake_azure_runner):
    result = collect_live_azure(
        "SUBSCRIPTION", "STAGING_RG", "DEPLOY_IDENTITY", run=fake_azure_runner
    )
    digest = result.data["collected_sha256"]
    assert isinstance(digest, str) and digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
    payload_without_digest = {k: v for k, v in result.data.items() if k != "collected_sha256"}
    expected = f"sha256:{canonical.sha256_hex(canonical.canonical_bytes(payload_without_digest))}"
    assert digest == expected


def test_live_azure_digest_changes_when_state_changes():
    runner_a = _FakeRunner([_ok("[]"), _ok("[]")])
    runner_b = _FakeRunner([_ok('[{"id": 1}]'), _ok("[]")])
    result_a = collect_live_azure("sub", "rg", "identity", run=runner_a)
    result_b = collect_live_azure("sub", "rg", "identity", run=runner_b)
    assert result_a.status == result_b.status == "pass"
    assert result_a.data["collected_sha256"] != result_b.data["collected_sha256"]


def test_live_azure_federated_credential_failure_is_ghcp_005(fake_runner_403):
    result = collect_live_azure("sub", "rg", "identity", run=fake_runner_403)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-005"
    assert result.evidence == ()


def test_live_azure_role_assignment_failure_is_ghcp_006():
    runner = _FakeRunner([_ok("[]"), _forbidden()])
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-006"


def test_live_azure_role_definition_failure_is_ghcp_006():
    runner = _FakeRunner(
        [_ok("[]"), _ok(json.dumps([{"roleDefinitionName": "Owner"}])), _forbidden()]
    )
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-006"


def test_live_azure_cli_failure_retains_exit_code_and_error_class():
    runner = _FakeRunner([_ok("[]"), _forbidden()])
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.data["exit_code"] == 1
    assert result.data["error_class"] == "cli-error"
    assert "exit code 1" in result.finding.details
    assert "Forbidden" not in result.finding.details


@pytest.mark.parametrize(
    "malformed_stdout",
    [
        "{}",  # federated_credentials must be a JSON array, not a mapping
        "null",
    ],
)
def test_live_azure_malformed_federated_credentials_is_ghcp_005(malformed_stdout):
    # A malformed/incomplete federated-credential-list response is unusable
    # OIDC/WIF evidence -- exactly as unusable as the command failing
    # outright -- so it must attribute to GHCP-005, the same control an
    # outright command failure at this step already attributes to, never
    # GHCP-006 (which is reserved for role-assignment/definition evidence).
    runner = _FakeRunner([_ok(malformed_stdout)])
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-005"
    assert result.evidence == ()


def test_live_azure_non_list_role_assignments_is_ghcp_006():
    runner = _FakeRunner([_ok("[]"), _ok("{}")])
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-006"
    assert result.evidence == ()


def test_live_azure_non_mapping_role_assignment_entry_is_ghcp_006():
    runner = _FakeRunner([_ok("[]"), _ok(json.dumps(["not-a-mapping"]))])
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-006"


@pytest.mark.parametrize(
    "role_assignments",
    [
        [{}],
        [{"roleDefinitionName": ""}],
        [{"roleDefinitionName": None}],
        [{"roleDefinitionName": 123}],
    ],
)
def test_live_azure_missing_or_invalid_role_definition_name_is_ghcp_006(role_assignments):
    runner = _FakeRunner([_ok("[]"), _ok(json.dumps(role_assignments))])
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-006"


def test_live_azure_non_list_role_definition_response_is_ghcp_006():
    runner = _FakeRunner(
        [_ok("[]"), _ok(json.dumps([{"roleDefinitionName": "Owner"}])), _ok("{}")]
    )
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-006"
    assert result.evidence == ()


@pytest.mark.parametrize(
    "definition_stdout",
    [
        "[]",  # zero matching definitions
        json.dumps([{"roleName": "Owner"}, {"roleName": "Owner"}]),  # more than one match
        json.dumps([{"roleName": "SomeOtherRole"}]),  # single entry but wrong role name
        json.dumps(["not-a-mapping"]),  # single entry but not itself a JSON object
    ],
)
def test_live_azure_role_definition_ambiguous_or_mismatched_is_ghcp_006(definition_stdout):
    # Azure's real CLI, queried by exact --name, always resolves to exactly
    # one matching definition for a role that genuinely exists; anything
    # else can never prove what the assigned role actually grants.
    runner = _FakeRunner(
        [_ok("[]"), _ok(json.dumps([{"roleDefinitionName": "Owner"}])), _ok(definition_stdout)]
    )
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-006"
    assert result.evidence == ()


def test_live_azure_role_definition_exact_single_match_passes():
    runner = _FakeRunner(
        [
            _ok("[]"),
            _ok(json.dumps([{"roleDefinitionName": "Owner"}])),
            _ok(json.dumps([{"roleName": "Owner", "id": "role-owner"}])),
        ]
    )
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "pass"
    assert result.data["role_definitions"] == {"Owner": {"roleName": "Owner", "id": "role-owner"}}


def test_live_azure_digest_is_bound_to_scope():
    result_a = collect_live_azure("sub-a", "rg", "identity", run=_FakeRunner([_ok("[]"), _ok("[]")]))
    result_b = collect_live_azure("sub-b", "rg", "identity", run=_FakeRunner([_ok("[]"), _ok("[]")]))
    assert result_a.data["collected_sha256"] != result_b.data["collected_sha256"]
    assert result_a.data["subscription"] == "sub-a"
    assert result_b.data["subscription"] == "sub-b"


def test_live_azure_federated_credentials_entry_must_be_a_mapping():
    runner = _FakeRunner([_ok(json.dumps(["not-a-mapping"]))])
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-005"


def test_live_azure_digest_invariant_to_role_assignment_return_order():
    responses_a = [
        _ok("[]"),
        _ok(json.dumps([{"roleDefinitionName": "Owner"}, {"roleDefinitionName": "Reader"}])),
        _ok(json.dumps([{"roleName": "Owner"}])),
        _ok(json.dumps([{"roleName": "Reader"}])),
    ]
    responses_b = [
        _ok("[]"),
        _ok(json.dumps([{"roleDefinitionName": "Reader"}, {"roleDefinitionName": "Owner"}])),
        _ok(json.dumps([{"roleName": "Owner"}])),
        _ok(json.dumps([{"roleName": "Reader"}])),
    ]
    result_a = collect_live_azure("sub", "rg", "identity", run=_FakeRunner(responses_a))
    result_b = collect_live_azure("sub", "rg", "identity", run=_FakeRunner(responses_b))
    assert result_a.data["collected_sha256"] == result_b.data["collected_sha256"]


def test_live_azure_digest_invariant_to_duplicate_role_name_entry_order():
    # Two role-assignment entries can legitimately name the *same* role
    # (e.g. assigned at different scopes) while differing in their other
    # fields. Sorting only by ``roleDefinitionName`` ties on those two
    # entries, so without a content-based tie-breaker Python's merely
    # input-order-stable sort would leave them in whatever order the
    # live API happened to return them in -- making the digest depend on
    # that transient order rather than only on the actual set of
    # elements returned.
    responses_a = [
        _ok("[]"),
        _ok(
            json.dumps(
                [
                    {"roleDefinitionName": "Reader", "principalId": "p1"},
                    {"roleDefinitionName": "Reader", "principalId": "p2"},
                ]
            )
        ),
        _ok(json.dumps([{"roleName": "Reader"}])),
    ]
    responses_b = [
        _ok("[]"),
        _ok(
            json.dumps(
                [
                    {"roleDefinitionName": "Reader", "principalId": "p2"},
                    {"roleDefinitionName": "Reader", "principalId": "p1"},
                ]
            )
        ),
        _ok(json.dumps([{"roleName": "Reader"}])),
    ]
    result_a = collect_live_azure("sub", "rg", "identity", run=_FakeRunner(responses_a))
    result_b = collect_live_azure("sub", "rg", "identity", run=_FakeRunner(responses_b))
    assert result_a.status == result_b.status == "pass"
    assert result_a.data["collected_sha256"] == result_b.data["collected_sha256"]


def test_live_azure_role_definition_fanout_cap_exceeded_is_not_verified_before_any_lookup():
    # A role-assignment list naming more unique roles than this
    # collector's bounded technical fan-out safety limit must be
    # reported not-verified *before* a single ``az role definition
    # list`` command is issued for any of them -- never partway through
    # an unbounded fan-out.
    unique_roles = [f"Role{i}" for i in range(ghcp._MAX_AZURE_ROLE_DEFINITION_LOOKUPS + 1)]
    role_assignments = [{"roleDefinitionName": name} for name in unique_roles]
    runner = _FakeRunner([_ok("[]"), _ok(json.dumps(role_assignments))])
    result = collect_live_azure("sub", "rg", "identity", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-006"
    assert result.evidence == ()
    # Exactly the federated-credential and role-assignment commands ran;
    # no "az role definition list" fan-out was ever attempted.
    assert len(runner.commands) == 2
    assert all(command[:3] != ["az", "role", "definition"] for command in runner.commands)


# --- Issue 3: collector runner hardening (timeouts, malformed JSON,
# NaN/nonfinite floats, oversized stdout, recursion) ------------------------


def test_collect_live_github_runner_timeout_is_not_verified():
    def _raise_timeout(command):
        raise subprocess.TimeoutExpired(cmd=command, timeout=5)

    result = collect_live_github("owner/repo", "main", run=_raise_timeout)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-002"
    assert result.data["error_class"] == "timeout"
    assert result.evidence == ()


def test_collect_live_azure_runner_timeout_is_not_verified():
    def _raise_timeout(command):
        raise subprocess.TimeoutExpired(cmd=command, timeout=5)

    result = collect_live_azure("sub", "rg", "identity", run=_raise_timeout)
    assert result.status == "not-verified"
    assert result.data["error_class"] == "timeout"


def test_collect_live_github_runner_declared_subprocess_error_is_not_verified():
    def _raise_subprocess_error(command):
        raise subprocess.SubprocessError("boom")

    result = collect_live_github("owner/repo", "main", run=_raise_subprocess_error)
    assert result.status == "not-verified"
    assert result.data["error_class"] == "cli-error"


def test_collect_live_github_rejects_nonfinite_float_in_payload():
    # ``json.loads`` itself silently accepts ``NaN``/``Infinity`` by
    # default; this module must never treat such a payload as usable
    # evidence merely because it happened to parse.
    responses = _valid_github_responses()
    responses[1] = _ok(json.dumps({"weight": 1}).replace("1", "NaN"))
    runner = _FakeRunner(responses)
    result = collect_live_github("owner/repo", "main", run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-002"
    assert result.data["error_class"] == "malformed-json"


def test_collect_live_github_rejects_oversized_stdout():
    responses = _valid_github_responses()
    huge = json.dumps({"padding": "x" * (ghcp._MAX_LIVE_COMMAND_STDOUT_BYTES + 1)})
    responses[1] = _ok(huge)
    runner = _FakeRunner(responses)
    result = collect_live_github("owner/repo", "main", run=runner)
    assert result.status == "not-verified"
    assert result.data["error_class"] == "malformed-json"


def test_collect_live_github_rejects_deeply_recursive_json():
    # A JSON array nested deep enough to exhaust the interpreter's
    # recursion limit during parsing or canonicalization must be
    # rejected as unusable, never allowed to propagate an unhandled
    # ``RecursionError`` out of the collector.
    deeply_nested = "[" * 100000 + "]" * 100000
    responses = _valid_github_responses()
    responses[1] = _ok(deeply_nested)
    runner = _FakeRunner(responses)
    result = collect_live_github("owner/repo", "main", run=runner)
    assert result.status == "not-verified"
    assert result.data["error_class"] == "malformed-json"


@pytest.mark.parametrize(
    "subscription,resource_group,deploy_identity",
    [("", "rg", "identity"), ("sub", "", "identity"), ("sub", "rg", "")],
)
def test_live_azure_absent_input_is_not_verified_without_running_commands(
    subscription, resource_group, deploy_identity
):
    runner = _FakeRunner([])
    result = collect_live_azure(subscription, resource_group, deploy_identity, run=runner)
    assert result.status == "not-verified"
    assert result.finding.finding_id == "GHCP-006"
    assert runner.commands == []
    assert result.evidence == ()


def test_live_azure_missing_cli_is_not_verified():
    def _raise_missing_cli(command):
        raise FileNotFoundError("az")

    result = collect_live_azure("sub", "rg", "identity", run=_raise_missing_cli)
    assert result.status == "not-verified"
    assert result.finding.finding_id in {"GHCP-005", "GHCP-006"}


def test_live_azure_never_records_raw_stderr(fake_runner_403):
    result = collect_live_azure("sub", "rg", "identity", run=fake_runner_403)
    assert "Forbidden" not in result.finding.details
    assert "Forbidden" not in json.dumps(result.data)
