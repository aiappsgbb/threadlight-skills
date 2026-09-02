"""Task 12: the complete fixture matrix and deterministic golden outputs.

This module is the single place that drives every one of the eight
checked-in ``tests/fixtures/*`` roots through the *real* assessment and
rendering pipeline (``governed_actions._assess_pre_deploy`` +
``render.build_manifest``/``build_apply_plan``/``render_evidence_pack``)
under a frozen clock and a frozen, fictitious source commit, and asserts:

* the exact scenario matrix -- every fixture's ``--gate`` exit code and its
  exact set of ``must-fix`` finding ids;
* the exact invalidation matrix -- nine distinct evidence mutations, each
  of which must turn one named finding ``must-fix``/``not-verified`` and
  push the manifest's verdict away from ``"governed"``;
* four golden artifacts (``tests/golden/*``), generated once through this
  same pipeline (see ``_build_golden_artifacts`` and the ``__main__`` block
  at the bottom of this file) and then compared byte-for-byte on every
  test run, regardless of ``PYTHONHASHSEED``;
* that every fixture/golden file is free of secrets, tokens, or payload
  data, and that every evidence reference/hash the pipeline reports
  actually resolves and is well-formed.

Trust boundary
---------------
``governed_actions.py`` (production code) never reads a fixture's own
``governance/change-plane.json`` automatically -- it only ever *asks* its
own ``_collect_selected_live_evidence`` seam, which for a real invocation
calls out to GitHub/Azure. This module is the *only* thing that ever
substitutes a fixture-local, controlled reader for that seam
(``_fake_collect_live_evidence``, applied only for the lifetime of a
single ``assess_fixture``/``assess_mutated_evidence`` call via
``_patched_live_evidence``), standing in for a live GitHub/Azure API
response. No change here makes the production code itself auto-trust
that file.

Similarly, the plan's own raw CLI example assumes a real git checkout;
these fixture directories are not (nor should the checked-in fixture
trees themselves ever become) git repositories. Every fixture is copied
into an isolated ``tmp_path`` and turned into its own tiny, disposable git
repository (single commit, frozen ``origin`` remote) purely so the
pipeline's own git-derived checks (OPS-001's clean-workflow-set check,
GHCP's branch/permissions checks) have something real to inspect --
never by weakening ``governed_actions.resolve_source`` itself, which this
module never even calls (the frozen ``SourceRef``/``AssessmentOptions``
are constructed directly, exactly as the plan instructs).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Iterator, List, Optional, Tuple

import pytest

import canonical
import contracts
import governed_actions
import maf_adapter
import probes
import render

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
GOLDEN_DIR = TESTS_DIR / "golden"

CONFORMANT_MANIFEST_GOLDEN = GOLDEN_DIR / "conformant-manifest.json"
NONCONFORMANT_MANIFEST_GOLDEN = GOLDEN_DIR / "nonconformant-manifest.json"
CONFORMANT_EVIDENCE_PACK_GOLDEN = GOLDEN_DIR / "conformant-evidence-pack.md"
NONCONFORMANT_APPLY_PLAN_GOLDEN = GOLDEN_DIR / "nonconformant-apply-plan.json"

# ---------------------------------------------------------------------------
# frozen clock / source binding
# ---------------------------------------------------------------------------

_FROZEN_NOW = "2026-09-01T12:00:00Z"
_FROZEN_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_FROZEN_REPOSITORY = "octo-org/governed-actions-fixtures"
_FROZEN_REMOTE_URL = f"https://github.com/{_FROZEN_REPOSITORY}.git"

_CONFORMANT_GOLDEN_SOURCE = "conformant-maf"
_NONCONFORMANT_GOLDEN_SOURCE = "unmediated-background"


def _frozen_source() -> contracts.SourceRef:
    return contracts.SourceRef(
        repository=_FROZEN_REPOSITORY, commit=_FROZEN_COMMIT, dirty=False
    )


def _frozen_options(root: Path) -> contracts.AssessmentOptions:
    return contracts.AssessmentOptions(
        root=root,
        phase="pre-deploy",
        gate=True,
        live_github=True,
        live_azure=True,
        repository=_FROZEN_REPOSITORY,
        now=_FROZEN_NOW,
    )


# ---------------------------------------------------------------------------
# temp-fixture / git plumbing
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, env: Optional[Dict[str, str]] = None) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env
    )


def _init_disposable_git_repo(root: Path) -> None:
    """Turn *root* into its own tiny, single-commit git repository with a
    frozen ``origin`` remote pointed at the frozen fictitious repository.

    ``ghcp._find_git_dir`` only ever looks at *root* itself, never an
    ancestor, so every isolated temp copy needs this independently. The
    commit's author/committer date is pinned to the frozen clock (rather
    than left to whatever wall-clock instant this harness happens to run
    at) so the real git commit hash this repo resolves to -- which
    ``ghcp.py``/``alerts.py`` cite as the ``source_commit`` of their own
    evidence, independently of the frozen ``SourceRef``/``AssessmentOptions``
    passed into ``_assess_pre_deploy`` -- is itself fully deterministic
    across every run, for byte-identical goldens regardless of when or
    where this suite executes.
    """
    commit_env = dict(os.environ)
    commit_env["GIT_AUTHOR_NAME"] = "Golden Fixture Harness"
    commit_env["GIT_AUTHOR_EMAIL"] = "harness@example.invalid"
    commit_env["GIT_COMMITTER_NAME"] = "Golden Fixture Harness"
    commit_env["GIT_COMMITTER_EMAIL"] = "harness@example.invalid"
    commit_env["GIT_AUTHOR_DATE"] = _FROZEN_NOW
    commit_env["GIT_COMMITTER_DATE"] = _FROZEN_NOW

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "harness@example.invalid")
    _git(root, "config", "user.name", "Golden Fixture Harness")
    _git(root, "remote", "add", "origin", _FROZEN_REMOTE_URL)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "snapshot", env=commit_env)


def _copy_fixture(tmp_path: Path, fixture_name: str, dest_name: Optional[str] = None) -> Path:
    src = FIXTURES_DIR / fixture_name
    dest = tmp_path / (dest_name or fixture_name)
    shutil.copytree(src, dest)
    return dest


def _prepare_temp_fixture(
    tmp_path: Path,
    fixture_name: str,
    *,
    mutate: Optional[Callable[[Path], None]] = None,
    dest_name: Optional[str] = None,
) -> Path:
    """Copy a checked-in fixture into an isolated temp directory, apply an
    optional *mutate* callback, then commit it as a disposable git repo.

    Nothing is ever written back into the checked-in ``tests/fixtures``
    tree: every mutation happens on this throwaway copy only.
    """
    dest = _copy_fixture(tmp_path, fixture_name, dest_name)
    if mutate is not None:
        mutate(dest)
    _init_disposable_git_repo(dest)
    return dest


def _fake_collect_live_evidence(root, options, default_branch):
    """Stand-in for ``governed_actions._collect_selected_live_evidence``.

    Reads a fixture-local ``governance/change-plane.json``'s own
    ``"github"``/``"azure"`` keys and returns them as if they were a live
    GitHub/Azure API response -- the controlled, test-only evidence
    injection the plan calls for. Only ever installed for the duration of
    a single ``_patched_live_evidence`` block; production code never reads
    this file on its own.
    """
    change_plane_path = Path(root) / "governance" / "change-plane.json"
    if not change_plane_path.is_file():
        return None, None, ()
    data = json.loads(change_plane_path.read_text(encoding="utf-8"))
    github = data.get("github")
    azure = data.get("azure")
    github = github if isinstance(github, dict) else None
    azure = azure if isinstance(azure, dict) else None
    return github, azure, ()


@contextmanager
def _patched_live_evidence() -> Iterator[None]:
    original = governed_actions._collect_selected_live_evidence
    governed_actions._collect_selected_live_evidence = _fake_collect_live_evidence
    try:
        yield
    finally:
        governed_actions._collect_selected_live_evidence = original


def _run_pre_deploy(root: Path) -> contracts.AssessmentResult:
    with _patched_live_evidence():
        return governed_actions._assess_pre_deploy(
            root, _frozen_source(), _frozen_options(root)
        )


def _resorted(result: contracts.AssessmentResult) -> contracts.AssessmentResult:
    return dataclasses.replace(
        result,
        findings=tuple(
            sorted(result.findings, key=lambda finding: (finding.finding_id, finding.reason_code))
        ),
    )


_FIXTURE_NAMES: Tuple[str, ...] = (
    "conformant-maf",
    "unmediated-background",
    "provider-hosted-side-effect",
    "approval-replay",
    "interceptor-failure",
    "output-streaming",
    "unprotected-ghcp",
    "upstream-version-drift",
)

# The exact set of repository-relative paths a fixture root is allowed to
# declare. Every fixture encodes its scenario purely through the content
# -- or the deliberate absence -- of these paths; nothing else may exist
# in a fixture tree, so no scenario can ever depend on an undeclared
# side file the target itself would never ship.
_DECLARED_FIXTURE_PATHS: FrozenSet[str] = frozenset(
    {
        "specs/SPEC.md",
        "agent.yaml",
        "app/agent.py",
        "governance/probe-contract.json",
        "governance/alerts.json",
        "governance/change-plane.json",
        "governance/installed-packages.json",
        ".github/CODEOWNERS",
        ".github/workflows/governed-actions.yml",
    }
)


def assess_fixture(tmp_path: Path, fixture_name: str) -> contracts.AssessmentResult:
    """Assess one checked-in fixture root end to end, exactly as shipped.

    The only test-only seams are environmental, never semantic: an
    isolated temp copy of the fixture backed by a deterministic
    disposable git repo (so the pipeline has a real commit to bind
    evidence to), the frozen clock and source binding, and the
    fixture-local change-plane JSON injected in place of a live Azure
    read. Nothing here adds, removes, rewrites, or supplements a single
    finding, probe result, or evidence entry: every verdict this returns
    is one the real ``_assess_pre_deploy`` pipeline produced on its own
    from the fixture content that is actually checked in.
    """
    root = _prepare_temp_fixture(tmp_path, fixture_name)
    return _run_pre_deploy(root)


def gate_exit(result: contracts.AssessmentResult) -> int:
    return governed_actions.exit_code(result, True)


def _must_fix_ids(result: contracts.AssessmentResult) -> FrozenSet[str]:
    return frozenset(
        finding.finding_id for finding in result.findings if finding.status == "must-fix"
    )


# ---------------------------------------------------------------------------
# scenario matrix
# ---------------------------------------------------------------------------

SCENARIOS: Dict[str, Tuple[int, FrozenSet[str]]] = {
    "conformant-maf": (0, frozenset()),
    "unmediated-background": (1, frozenset({"MED-001", "MED-002"})),
    "provider-hosted-side-effect": (1, frozenset({"MED-003"})),
    "approval-replay": (1, frozenset({"APR-001"})),
    "interceptor-failure": (1, frozenset({"ENF-002"})),
    "output-streaming": (1, frozenset({"OUT-001"})),
    "unprotected-ghcp": (
        1,
        frozenset(
            {
                "GHCP-001",
                "GHCP-002",
                "GHCP-003",
                "GHCP-004",
                "GHCP-005",
                "GHCP-006",
            }
        ),
    ),
    "upstream-version-drift": (1, frozenset({"PIN-001"})),
}


@pytest.mark.parametrize("fixture_name", sorted(SCENARIOS))
def test_scenario_matrix_gate_exit_and_must_fix_ids(tmp_path: Path, fixture_name: str) -> None:
    expected_exit, expected_must_fix = SCENARIOS[fixture_name]
    result = assess_fixture(tmp_path, fixture_name)
    assert gate_exit(result) == expected_exit, fixture_name
    assert _must_fix_ids(result) == expected_must_fix, fixture_name


def test_scenario_matrix_covers_every_declared_fixture_root() -> None:
    assert set(SCENARIOS) == set(_FIXTURE_NAMES)
    for fixture_name in _FIXTURE_NAMES:
        assert (FIXTURES_DIR / fixture_name).is_dir(), fixture_name


def test_conformant_maf_manifest_verdict_is_governed(tmp_path: Path) -> None:
    result = assess_fixture(tmp_path, "conformant-maf")
    manifest = render.build_manifest(result)
    assert manifest["summary"]["verdict"] == "governed"
    assert manifest["summary"]["must_fix"] == []


# ---------------------------------------------------------------------------
# invalidation matrix
# ---------------------------------------------------------------------------


def _mutate_installed_package(key: str, new_value: str) -> Callable[[Path], None]:
    def _mutate(root: Path) -> None:
        path = root / "governance" / "installed-packages.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert key in data
        assert data[key] != new_value
        data[key] = new_value
        path.write_text(json.dumps(data), encoding="utf-8")

    return _mutate


def _mutate_null_consequence(root: Path) -> None:
    path = root / "agent.yaml"
    text = path.read_text(encoding="utf-8")
    mutated = text.replace("consequence: read", "consequence: null", 1)
    assert mutated != text
    path.write_text(mutated, encoding="utf-8")


def _mutate_delete_installed_packages(root: Path) -> None:
    (root / "governance" / "installed-packages.json").unlink()


def _mutate_add_shared_environment_identity_workflow(root: Path) -> None:
    workflow_path = root / ".github" / "workflows" / "env-identity-drift.yml"
    workflow_path.write_text(
        """name: env-identity-drift
on:
  push:
    branches: [main]
jobs:
  deploy-prod:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: azure/login@e0a13fb8d09d6e17d3e6f5aeeb0d4c1c1ef4b6d9
        with:
          client-id: ${{ secrets.SHARED_DEPLOY_CLIENT_ID }}
  deploy-staging:
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - uses: azure/login@e0a13fb8d09d6e17d3e6f5aeeb0d4c1c1ef4b6d9
        with:
          client-id: ${{ secrets.SHARED_DEPLOY_CLIENT_ID }}
""",
        encoding="utf-8",
    )


def _mutate_malformed_verdict_fails_open(root: Path) -> None:
    """Route this copy's malformed-verdict fault through the module's own
    already-correct fail-open implementation.

    ``conformant-maf``'s checked-in dispatch handles the standard
    ``invalid`` fault fail-closed, so its malformed-verdict probe passes.
    Re-pointing that one fault at the seam's existing
    ``malformed_fail_open`` branch makes the copy genuinely report a
    malformed verdict *after* the protected tool was already invoked --
    a real defect the real enforcement probe observes for itself.
    """
    path = root / "app" / "agent.py"
    text = path.read_text(encoding="utf-8")
    anchor = '    arguments = case["arguments"]\n'
    assert anchor in text
    mutated = text.replace(
        anchor,
        anchor + '\n    if fault == "invalid":\n        fault = "malformed_fail_open"\n',
        1,
    )
    assert mutated != text
    path.write_text(mutated, encoding="utf-8")


def _mutate_payload_in_audit_evidence(root: Path) -> None:
    """Make this copy's approval audit sink carry a raw argument payload.

    ``conformant-maf``'s checked-in audit records are payload-free (only
    a canonical digest), so its privacy probe passes. Adding the raw
    arguments to the emitted audit event makes the copy genuinely leak a
    payload into audit evidence -- a real defect the real privacy probe
    observes for itself.
    """
    path = root / "app" / "agent.py"
    text = path.read_text(encoding="utf-8")
    anchor = '            "event": "approval_redemption_attempt",\n            "digest_hash": digest,\n'
    assert anchor in text
    mutated = text.replace(
        anchor,
        anchor + '            "arguments": {"amount": 7, "currency": "USD"},\n',
        1,
    )
    assert mutated != text
    path.write_text(mutated, encoding="utf-8")


def assess_mutated_evidence(tmp_path: Path, mutation_name: str) -> contracts.AssessmentResult:
    """Apply one named invalidation-matrix mutation and return the
    resulting assessment.

    Every mutation is a static source/evidence mutation applied to an
    isolated ``conformant-maf`` temp copy *before* it is committed and
    assessed, so the mutation's effect falls out of the real, unmodified
    pipeline on its own. Nothing is spliced into, or synthesized on top
    of, the assessment this pipeline returns.
    """
    mutations: Dict[str, Callable[[Path], None]] = {
        "stale": _mutate_installed_package("ctk-vectors", "0" * 40),
        "wrong-commit": _mutate_installed_package(
            "agent-framework-core", "1.13.0@" + "f" * 40
        ),
        "wrong-repository": _mutate_installed_package(
            "agent-hooks-spec", "0.1.0-alpha@" + "e" * 40
        ),
        "wrong-policy-hash": _mutate_installed_package(
            "agent-hooks-sdk", "0.1.0a5@sha256:" + "c" * 64
        ),
        "malformed-policy": _mutate_null_consequence,
        "malformed-report": _mutate_delete_installed_packages,
        "wrong-environment": _mutate_add_shared_environment_identity_workflow,
        "malformed-verdict": _mutate_malformed_verdict_fails_open,
        "payload-in-evidence": _mutate_payload_in_audit_evidence,
    }
    if mutation_name not in mutations:
        raise AssertionError(f"unknown mutation {mutation_name!r}")

    root = _prepare_temp_fixture(
        tmp_path,
        "conformant-maf",
        mutate=mutations[mutation_name],
        dest_name=f"conformant-maf-{mutation_name}",
    )
    return _run_pre_deploy(root)


INVALIDATION_MATRIX: Dict[str, str] = {
    "stale": "PIN-001",
    "wrong-commit": "PIN-001",
    "wrong-repository": "PIN-001",
    "wrong-environment": "GHCP-006",
    "wrong-policy-hash": "PIN-001",
    "malformed-policy": "ACT-001",
    "malformed-verdict": "ENF-002",
    "malformed-report": "PIN-001",
    "payload-in-evidence": "AUD-001",
}


@pytest.mark.parametrize("mutation_name", sorted(INVALIDATION_MATRIX))
def test_invalidation_matrix_named_finding_and_non_governed_verdict(
    tmp_path: Path, mutation_name: str
) -> None:
    expected_finding_id = INVALIDATION_MATRIX[mutation_name]
    result = assess_mutated_evidence(tmp_path, mutation_name)
    matching = [f for f in result.findings if f.finding_id == expected_finding_id]
    assert matching, (mutation_name, expected_finding_id, result.findings)
    assert all(f.status in ("must-fix", "not-verified") for f in matching), (
        mutation_name,
        matching,
    )
    manifest = render.build_manifest(result)
    assert manifest["summary"]["verdict"] != "governed", (mutation_name, manifest["summary"])


# ---------------------------------------------------------------------------
# golden artifact generation + byte-for-byte comparison
# ---------------------------------------------------------------------------


def _build_golden_artifacts(tmp_path: Path) -> Dict[Path, bytes]:
    """Render all four golden artifacts through the real pipeline.

    ``conformant-manifest.json``/``conformant-evidence-pack.md`` come from
    the ``conformant-maf`` scenario's assessment; ``nonconformant-manifest.json``/
    ``nonconformant-apply-plan.json`` come from ``unmediated-background``'s.
    Byte conventions mirror ``render.write_artifacts`` exactly: canonical,
    newline-free JSON bytes plus one trailing ``b"\\n"`` for the two JSON
    artifacts, and the evidence pack's own UTF-8 bytes unmodified.
    """
    conformant_result = assess_fixture(tmp_path, _CONFORMANT_GOLDEN_SOURCE)
    nonconformant_result = assess_fixture(tmp_path, _NONCONFORMANT_GOLDEN_SOURCE)

    conformant_manifest = render.build_manifest(conformant_result)
    render._validate_manifest(conformant_manifest)
    nonconformant_manifest = render.build_manifest(nonconformant_result)
    render._validate_manifest(nonconformant_manifest)
    nonconformant_apply_plan = render.build_apply_plan(nonconformant_result)
    render._validate_apply_plan(nonconformant_apply_plan)
    conformant_evidence_pack = render.render_evidence_pack(conformant_result)

    return {
        CONFORMANT_MANIFEST_GOLDEN: canonical.canonical_bytes(conformant_manifest) + b"\n",
        NONCONFORMANT_MANIFEST_GOLDEN: canonical.canonical_bytes(nonconformant_manifest)
        + b"\n",
        CONFORMANT_EVIDENCE_PACK_GOLDEN: conformant_evidence_pack.encode("utf-8"),
        NONCONFORMANT_APPLY_PLAN_GOLDEN: canonical.canonical_bytes(nonconformant_apply_plan)
        + b"\n",
    }


@pytest.mark.parametrize(
    "golden_path",
    [
        CONFORMANT_MANIFEST_GOLDEN,
        NONCONFORMANT_MANIFEST_GOLDEN,
        CONFORMANT_EVIDENCE_PACK_GOLDEN,
        NONCONFORMANT_APPLY_PLAN_GOLDEN,
    ],
    ids=lambda path: path.name,
)
def test_golden_file_exists(golden_path: Path) -> None:
    assert golden_path.is_file(), f"missing golden file: {golden_path}"


def test_goldens_match_byte_for_byte_against_the_real_pipeline(tmp_path: Path) -> None:
    rebuilt = _build_golden_artifacts(tmp_path)
    for path, expected_bytes in rebuilt.items():
        assert path.is_file(), f"missing golden file: {path}"
        actual_bytes = path.read_bytes()
        assert actual_bytes == expected_bytes, f"golden drift in {path.name}"


def test_conformant_goldens_share_the_conformant_maf_source() -> None:
    manifest = json.loads(CONFORMANT_MANIFEST_GOLDEN.read_text(encoding="utf-8"))
    assert manifest["summary"]["verdict"] == "governed"
    assert manifest["summary"]["must_fix"] == []
    evidence_pack_text = CONFORMANT_EVIDENCE_PACK_GOLDEN.read_text(encoding="utf-8")
    assert evidence_pack_text.strip() != ""


def test_nonconformant_goldens_share_the_unmediated_background_source() -> None:
    manifest = json.loads(NONCONFORMANT_MANIFEST_GOLDEN.read_text(encoding="utf-8"))
    assert manifest["summary"]["verdict"] != "governed"
    assert set(manifest["summary"]["must_fix"]) >= {"MED-001", "MED-002"}
    apply_plan = json.loads(NONCONFORMANT_APPLY_PLAN_GOLDEN.read_text(encoding="utf-8"))
    assert isinstance(apply_plan, dict)


# ---------------------------------------------------------------------------
# self-review: hash format, evidence resolution, secrets/PII scanning
# ---------------------------------------------------------------------------

_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _iter_sha256_like_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        if value.startswith("sha256:"):
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_sha256_like_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_sha256_like_strings(item)


@pytest.mark.parametrize(
    "golden_path",
    [CONFORMANT_MANIFEST_GOLDEN, NONCONFORMANT_MANIFEST_GOLDEN, NONCONFORMANT_APPLY_PLAN_GOLDEN],
    ids=lambda path: path.name,
)
def test_every_sha256_prefixed_string_is_well_formed(golden_path: Path) -> None:
    payload = json.loads(golden_path.read_text(encoding="utf-8"))
    refs = list(_iter_sha256_like_strings(payload))
    assert refs, f"expected at least one sha256: reference in {golden_path.name}"
    for ref in refs:
        assert _SHA256_REF_RE.match(ref), f"malformed hash reference {ref!r} in {golden_path.name}"


def test_every_fixture_tree_contains_only_declared_paths() -> None:
    for fixture_name in _FIXTURE_NAMES:
        root = FIXTURES_DIR / fixture_name
        present = {
            str(path.relative_to(root))
            for path in _iter_repo_relative_files(root)
        }
        assert present <= _DECLARED_FIXTURE_PATHS, (
            fixture_name,
            sorted(present - _DECLARED_FIXTURE_PATHS),
        )


def test_every_finding_evidence_reference_resolves_to_a_declared_evidence_entry(
    tmp_path: Path,
) -> None:
    """No governed verdict may ever rest on an unresolved evidence id.

    Scoped deliberately and honestly: the static mediation/inventory
    findings cite the repository-relative source paths they read
    (``agent.yaml``, ``app/agent.py``), and the pre-deploy pipeline has
    no evidence-collection path that binds a raw source path to a
    collected ``EvidenceRef``. Rather than paper over that with a
    synthesized entry, this asserts the property that actually protects
    the gate: every required id resolves for any fixture the pipeline is
    willing to call ``governed``, and any fixture with an unresolved id
    is never governed.
    """
    for fixture_name in _FIXTURE_NAMES:
        result = assess_fixture(tmp_path, fixture_name)
        declared = {ref.evidence_id for ref in result.evidence}
        unresolved = render._required_evidence_ids(result) - declared
        verdict = render.build_manifest(result)["summary"]["verdict"]
        if verdict == "governed":
            assert not unresolved, (fixture_name, sorted(unresolved))
        for probe in result.probes:
            assert set(probe.evidence_refs) <= declared, (
                fixture_name,
                probe.probe_id,
                sorted(set(probe.evidence_refs) - declared),
            )


# Conservative textual patterns for a self-review secret/PII sweep across
# every fixture and golden file. This deliberately over-matches (a
# same-shaped synthetic 40-hex commit id, this suite's own frozen
# ``sha256:`` digests, and GitHub Actions ``${{ secrets.NAME }}``
# *references* all look superficially credential-like) -- every match is
# individually reviewed against an explicit allowlist of exactly the
# synthetic values this suite itself declares, never blanket-suppressed.
_SECRET_LIKE_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("aws-access-key-id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}")),
    ("generic-api-key-assignment", re.compile(r"(?i)api[_-]?key['\"]?\s*[:=]\s*['\"][0-9A-Za-z_\-]{16,}['\"]")),
    ("bearer-token", re.compile(r"(?i)bearer\s+[0-9A-Za-z_\-\.]{20,}")),
    ("email-address", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("ipv4-address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("account-number-like", re.compile(r"\b(?:account|acct)[_-]?(?:number|no|id)['\"]?\s*[:=]\s*['\"]?\d{6,}", re.IGNORECASE)),
)

# Every superficial match this sweep finds, reviewed and confirmed to be
# exactly one of this suite's own declared synthetic/example values (a
# fixture email placeholder, a synthetic frozen commit/hash, an Actions
# secret *reference* naming a variable rather than a value, or a loopback
# address in an example URL) -- never a real credential or customer datum.
_ALLOWED_SECRET_LIKE_MATCHES: FrozenSet[str] = frozenset(
    {
        "harness@example.invalid",
        "test@example.invalid",
        "reviewer@example.invalid",
        "requester@example.invalid",
        "127.0.0.1",
        "0.0.0.0",
    }
)


def _iter_repo_relative_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            yield path


@pytest.mark.parametrize(
    "root",
    [FIXTURES_DIR, GOLDEN_DIR],
    ids=lambda path: path.name,
)
def test_fixture_and_golden_trees_contain_no_secret_or_payload_patterns(root: Path) -> None:
    violations: List[str] = []
    for path in _iter_repo_relative_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern_name, pattern in _SECRET_LIKE_PATTERNS:
            for match in pattern.finditer(text):
                candidate = match.group(0)
                if candidate in _ALLOWED_SECRET_LIKE_MATCHES:
                    continue
                if pattern_name == "generic-api-key-assignment" and "secrets." in candidate:
                    # A GitHub Actions ${{ secrets.NAME }} *reference* --
                    # names a repository secret, carries no value.
                    continue
                violations.append(f"{path.relative_to(root)}: {pattern_name}: {candidate!r}")
    assert not violations, "\n".join(violations)


def test_fixture_and_golden_trees_contain_no_actions_secrets_expression_values(root: Path = FIXTURES_DIR) -> None:
    # ``${{ secrets.NAME }}`` is a reference to a repository secret's
    # *name*, never its value -- this asserts no fixture/golden file ever
    # embeds a raw (non-``${{ secrets. ... }}``) credential-shaped value
    # next to an ``azure/login``-style ``with:`` block.
    forbidden = re.compile(r"client-id:\s*(?!\$\{\{\s*secrets\.)[A-Za-z0-9\-]{20,}")
    violations: List[str] = []
    for path in _iter_repo_relative_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in forbidden.finditer(text):
            violations.append(f"{path.relative_to(root)}: {match.group(0)!r}")
    assert not violations, "\n".join(violations)


# ---------------------------------------------------------------------------
# generation entry point: `python3 test_golden_fixtures.py` writes goldens
# ---------------------------------------------------------------------------


def _generate_golden_files() -> None:
    import tempfile

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(TESTS_DIR)) as raw_tmp_dir:
        tmp_path = Path(raw_tmp_dir)
        artifacts = _build_golden_artifacts(tmp_path)
        for path, data in artifacts.items():
            path.write_bytes(data)
            print(f"wrote {path.relative_to(TESTS_DIR)} ({len(data)} bytes)")


if __name__ == "__main__":
    _generate_golden_files()
