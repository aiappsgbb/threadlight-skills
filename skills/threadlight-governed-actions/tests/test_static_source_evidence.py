"""Static-source evidence binding: real bytes, or nothing at all.

``inventory.py``/``mediation.py``/``maf_adapter.py`` cite the exact
repository-relative source paths they statically read (``agent.yaml``,
``app/agent.py``, ...) as their own ``Finding.evidence_refs``. Task 12
requires every one of those references to resolve to a real
``contracts.EvidenceRef`` in the emitted artifacts, which
``governed_actions._bind_static_source_evidence`` does by reading the
actual target file and hashing its actual bytes.

The properties pinned here are the ones that keep that binding honest:

* the digest is always ``sha256`` over the file's real bytes on disk --
  never derived from the evidence id, the path string, or anything else
  that would let a citation "resolve" without the artifact existing;
* every entry is bound to this assessment's own repository, source
  commit, phase, capture instant and canonical policy set, and carries
  no payload;
* an absent, unreadable, escaping, absolute, traversing, or aliased
  citation is left *unresolved* -- so the verdict cannot pass -- rather
  than bound to invented provenance;
* opaque protected-system ids (probe digests, alert/GHCP collector ids,
  the spec-section id) are never converted through this path.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Tuple

import pytest

import canonical
import contracts
import governed_actions
import render


_NOW = "2026-09-01T12:00:00Z"
_REPOSITORY = "octo-org/governed-actions-fixtures"
_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _source() -> contracts.SourceRef:
    return contracts.SourceRef(repository=_REPOSITORY, commit=_COMMIT, dirty=False)


def _options(root: Path) -> contracts.AssessmentOptions:
    return contracts.AssessmentOptions(
        root=root, phase="pre-deploy", repository=_REPOSITORY, now=_NOW
    )


def _finding(*evidence_refs: str) -> contracts.Finding:
    return contracts.Finding(
        finding_id="MED-001",
        status="must-fix",
        phase="pre-deploy",
        plane="runtime",
        reason_code="bypass",
        summary="synthetic finding for evidence binding",
        details="synthetic finding for evidence binding",
        evidence_refs=tuple(evidence_refs),
    )


def _bind(
    root: Path,
    *evidence_refs: str,
    policy_hashes: Tuple[Dict[str, str], ...] = (),
) -> Dict[str, contracts.EvidenceRef]:
    entries = governed_actions._bind_static_source_evidence(
        root, (_finding(*evidence_refs),), _source(), _options(root), policy_hashes
    )
    return {entry.evidence_id: entry for entry in entries}


@pytest.fixture()
def target(tmp_path: Path) -> Path:
    root = tmp_path / "target"
    (root / "app").mkdir(parents=True)
    (root / "agent.yaml").write_text("tools: []\n", encoding="utf-8")
    (root / "app" / "agent.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


def test_digest_is_sha256_over_the_files_real_bytes(target: Path) -> None:
    bound = _bind(target, "agent.yaml", "app/agent.py")
    assert set(bound) == {"agent.yaml", "app/agent.py"}
    for relative in ("agent.yaml", "app/agent.py"):
        raw = (target / relative).read_bytes()
        expected = "sha256:" + hashlib.sha256(raw).hexdigest()
        assert bound[relative].sha256 == expected


def test_digest_tracks_the_file_content_not_the_path(target: Path) -> None:
    before = _bind(target, "agent.yaml")["agent.yaml"].sha256
    (target / "agent.yaml").write_text("tools: [{id: a.b}]\n", encoding="utf-8")
    after = _bind(target, "agent.yaml")["agent.yaml"].sha256
    assert before != after
    # Never a digest synthesized from the id/path string itself.
    path_digest = "sha256:" + hashlib.sha256(b"agent.yaml").hexdigest()
    assert after != path_digest
    assert before != path_digest


def test_entries_are_bound_to_this_assessments_provenance(target: Path) -> None:
    policy_hashes = ({"path": "agent.yaml", "sha256": "sha256:" + "0" * 64},)
    entry = _bind(target, "agent.yaml", policy_hashes=policy_hashes)["agent.yaml"]
    assert entry.kind == "static-file-hash"
    assert entry.source == "agent.yaml"
    assert entry.repository == _REPOSITORY
    assert entry.source_commit == _COMMIT
    assert entry.phase == "pre-deploy"
    assert entry.collected_at == _NOW
    assert entry.freshness_seconds == 0
    assert entry.live_verified is False
    assert entry.target_environment is None
    assert entry.policy_set_sha256 == render.canonical_policy_set_sha256(policy_hashes)


def test_phase_follows_the_citing_finding_not_the_running_phase(target: Path) -> None:
    """A static-source citation is bound to the phase of the analysis that
    actually read the file -- the phase carried by the findings citing it --
    exactly as ``EVID-spec-section-8`` is. Stamping the running phase
    instead would leave every design-plane citation disagreeing with its
    own findings, and ``render`` would rightly treat the entry as
    untrustworthy even though its bytes were read honestly.
    """
    design_finding = contracts.Finding(
        finding_id="MED-002",
        status="must-fix",
        phase="design",
        plane="runtime",
        reason_code="coverage-incomplete",
        summary="synthetic design finding",
        details="synthetic design finding",
        evidence_refs=("agent.yaml",),
    )
    entries = governed_actions._bind_static_source_evidence(
        target, (design_finding,), _source(), _options(target), ()
    )
    assert _options(target).phase == "pre-deploy"
    assert [entry.phase for entry in entries] == ["design"]


def test_entries_carry_no_payload(target: Path) -> None:
    (target / "app" / "agent.py").write_text(
        'SECRET_ARGUMENTS = {"amount": 7, "currency": "USD"}\n', encoding="utf-8"
    )
    entry = _bind(target, "app/agent.py")["app/agent.py"]
    rendered = json.dumps(entry.__dict__, sort_keys=True)
    assert "amount" not in rendered
    assert "currency" not in rendered
    canonical.validate_payload_free_audit(dict(entry.__dict__))


@pytest.mark.parametrize(
    "reference",
    [
        "missing.yaml",
        "app/missing.py",
        "app",
        "../escape.yaml",
        "app/../../escape.yaml",
        "/etc/hosts",
        "specs/SPEC.md#section-8",
    ],
)
def test_unsafe_or_absent_references_are_left_unresolved(
    target: Path, reference: str
) -> None:
    assert _bind(target, reference) == {}


def test_symlink_escaping_the_root_is_left_unresolved(
    target: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text("tools: []\n", encoding="utf-8")
    os.symlink(outside, target / "linked.yaml")
    assert _bind(target, "linked.yaml") == {}


def test_symlink_aliasing_another_in_root_file_is_left_unresolved(
    target: Path,
) -> None:
    os.symlink(target / "agent.yaml", target / "alias.yaml")
    assert _bind(target, "alias.yaml") == {}


@pytest.mark.parametrize(
    "reference",
    [
        "EVID-spec-section-8",
        "alert-catalog",
        "ghcp-workflows",
        "audit-0001",
        "sha256:" + "a" * 64,
    ],
)
def test_opaque_protected_system_ids_are_never_converted(
    target: Path, reference: str
) -> None:
    assert _bind(target, reference) == {}


def test_unreadable_file_is_left_unresolved(target: Path) -> None:
    unreadable = target / "locked.yaml"
    unreadable.write_text("tools: []\n", encoding="utf-8")
    unreadable.chmod(0o000)
    try:
        if os.access(unreadable, os.R_OK):  # pragma: no cover - running as root
            pytest.skip("cannot make a file unreadable for this user")
        assert _bind(target, "locked.yaml") == {}
    finally:
        unreadable.chmod(0o600)


def test_references_are_deduplicated_and_sorted(target: Path) -> None:
    entries = governed_actions._bind_static_source_evidence(
        target,
        (
            _finding("app/agent.py", "agent.yaml"),
            _finding("agent.yaml"),
        ),
        _source(),
        _options(target),
        (),
    )
    assert [entry.evidence_id for entry in entries] == ["agent.yaml", "app/agent.py"]


def test_already_collected_ids_are_never_overwritten(target: Path) -> None:
    entries = governed_actions._bind_static_source_evidence(
        target,
        (_finding("agent.yaml", "app/agent.py"),),
        _source(),
        _options(target),
        (),
        already_collected=frozenset({"agent.yaml"}),
    )
    assert [entry.evidence_id for entry in entries] == ["app/agent.py"]
