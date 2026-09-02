"""Operational alert catalog assessment for threadlight-governed-actions
(Task 8, design section 17).

Governance controls are only as trustworthy as their own failure
signaling. If an unmediated action, an interceptor crash, a replayed
approval, an output-mediator failure, a lost audit record, a drifted
policy tuple, a drifted repository protection rule, or a drifted
deployment identity would never itself raise an alert, an operator could
be silently unprotected for an arbitrary length of time. ``assess_alerts``
proves the assessed repository declares production alert *definitions*
for exactly the eight alert classes design section 17 requires, that
each is enabled and carries a stable ``reason_code``/``correlation_id``
pair without ever declaring a payload field, and reports the single
``OPS-001`` finding this project's finding catalog already reserves for
that gap -- it never invents a business policy, alerting threshold, or
on-call assignment of its own.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_alerts.py -q
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Optional, Tuple

import canonical
from contracts import EvidenceRef, Finding, Phase
from ghcp import (
    _find_git_dir,
    _resolve_commit_sha,
    _resolve_repository_identifier,
    _workflow_set_is_clean,
)


#: The exact eight alert classes design section 17 requires a production
#: alert catalog to declare -- never more, never fewer, and never a name
#: this project invented on its own.
REQUIRED_ALERT_CLASSES: Tuple[str, ...] = (
    "unmediated-action",
    "interceptor-failure",
    "approval-replay",
    "output-mediator-failure",
    "audit-delivery-failure",
    "tuple-drift",
    "repository-protection-drift",
    "deployment-identity-drift",
)

_ALERTS_CATALOG_RELATIVE_PATH = Path("governance") / "alerts.json"

_ALL_ALERT_CLASSES_TEXT = ", ".join(REQUIRED_ALERT_CLASSES)


def _load_alert_catalog(root: Path) -> Tuple[Optional[Mapping[str, object]], Optional[str]]:
    """Read *root*'s ``governance/alerts.json`` catalog.

    Returns ``(catalog, None)`` on success or ``(None, problem)`` when the
    file is missing, is not valid JSON, or is not a JSON object -- every
    one of these is reported the same "cannot confirm the catalog is
    complete" way; this function never guesses at a partial catalog from
    malformed input.
    """
    catalog_path = root / _ALERTS_CATALOG_RELATIVE_PATH
    if not catalog_path.is_file():
        return None, (
            f"The production alert catalog {_ALERTS_CATALOG_RELATIVE_PATH.as_posix()} "
            "was not found."
        )
    try:
        text = catalog_path.read_text(encoding="utf-8")
    except OSError:
        return None, (
            f"The production alert catalog {_ALERTS_CATALOG_RELATIVE_PATH.as_posix()} "
            "could not be read."
        )
    try:
        catalog = json.loads(text)
    except ValueError:
        return None, (
            f"The production alert catalog {_ALERTS_CATALOG_RELATIVE_PATH.as_posix()} "
            "is not valid JSON."
        )
    if not isinstance(catalog, Mapping):
        return None, (
            f"The production alert catalog {_ALERTS_CATALOG_RELATIVE_PATH.as_posix()} "
            "must be a JSON object mapping each alert class to its definition."
        )
    return catalog, None


def _definition_is_complete(definition: object) -> bool:
    """True only if *definition* is enabled, declares a non-empty, stable
    ``reason_code``/``correlation_id`` pair, and contains no payload
    field.

    Any other shape -- disabled, missing/blank/non-string identifiers, or
    a payload-bearing key such as ``message``/``body``/``output`` -- is
    reported incomplete rather than guessed at as acceptable.
    """
    if not isinstance(definition, Mapping):
        return False
    if definition.get("enabled") is not True:
        return False
    reason_code = definition.get("reason_code")
    if not isinstance(reason_code, str) or not reason_code:
        return False
    correlation_id = definition.get("correlation_id")
    if not isinstance(correlation_id, str) or not correlation_id:
        return False
    try:
        canonical.validate_payload_free_audit(definition)
    except canonical.PayloadExposureError:
        return False
    return True


def _incomplete_alert_classes(catalog: Mapping[str, object]) -> Tuple[str, ...]:
    return tuple(
        name
        for name in REQUIRED_ALERT_CLASSES
        if not _definition_is_complete(catalog.get(name))
    )


def _validate_alert_class_list(values: object) -> Optional[Tuple[str, ...]]:
    """Validate a live-evidence alert-class list (``dropped_alert_classes``
    or ``unavailable_alert_classes``).

    Returns:

    - ``()`` when *values* is absent (``None``) -- no claim was made.
    - A deduplicated, sorted tuple of alert-class names when *values* is
      a ``list``/``tuple`` of at most eight entries, every one of which
      is a ``str`` drawn from :data:`REQUIRED_ALERT_CLASSES`.
    - ``None`` for any other shape -- a bare ``str``/``bytes``, a
      ``Mapping``, anything else that is not a ``list``/``tuple``, a
      collection longer than the eight recognized alert classes, or one
      containing any entry that is not itself one of those exact eight
      recognized names -- signalling to the caller that this live
      evidence cannot be trusted and must never be echoed back.
    """
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        return None
    if not isinstance(values, (list, tuple)):
        return None
    if len(values) > len(REQUIRED_ALERT_CLASSES):
        return None
    if not all(isinstance(value, str) and value in REQUIRED_ALERT_CLASSES for value in values):
        return None
    return tuple(sorted(set(values)))


def _catalog_evidence(root: Path, phase: Phase) -> Tuple[EvidenceRef, ...]:
    """Bind the alert catalog file to the real repository/commit it was
    actually read from -- or omit evidence entirely when that provenance
    cannot be established from real, on-disk git metadata.

    Mirrors ``ghcp._workflow_set_evidence``'s pattern exactly (including
    refusing to record evidence for a working tree that is dirty with
    respect to the very file being hashed): an assessor must never
    fabricate the evidence it reports, and a repository-relative alert
    catalog is no exception.
    """
    git_dir = _find_git_dir(root)
    if git_dir is None:
        return ()
    repository = _resolve_repository_identifier(git_dir)
    source_commit = _resolve_commit_sha(git_dir)
    if repository is None or source_commit is None:
        return ()
    catalog_paths = (root / _ALERTS_CATALOG_RELATIVE_PATH,)
    if not _workflow_set_is_clean(git_dir, root, catalog_paths, source_commit):
        return ()
    try:
        hashed = canonical.hash_files(root, catalog_paths)
    except canonical.CanonicalizationError:
        return ()
    return (
        EvidenceRef(
            evidence_id="alert-catalog",
            kind="file-set",
            source=_ALERTS_CATALOG_RELATIVE_PATH.as_posix(),
            sha256=str(hashed["set_sha256"]),
            collected_at=None,
            freshness_seconds=None,
            live_verified=False,
            phase=phase,
            repository=repository,
            source_commit=source_commit,
            target_environment=None,
            policy_set_sha256=None,
        ),
    )


def _alert_finding(phase: Phase, status: str, reason_code: str, summary: str, details: str) -> Finding:
    return Finding(
        finding_id="OPS-001",
        status=status,  # type: ignore[arg-type]
        phase=phase,
        plane="both",
        reason_code=reason_code,
        summary=summary,
        details=details,
    )


def assess_alerts(
    root: Path,
    phase: Phase,
    live_evidence: Optional[Mapping[str, object]],
) -> Tuple[Finding, Tuple[EvidenceRef, ...]]:
    """Assess whether *root* declares a complete, production alert
    catalog for all eight required alert classes, folding in any
    already-collected *live_evidence* about alert delivery this function
    was handed (never collecting any of it itself).

    Reports exactly one ``OPS-001`` :class:`Finding`, in this fail-closed
    precedence -- applied only *after* *live_evidence* itself has been
    validated:

    0. ``"not-verified"`` when *live_evidence* is present but is not a
       ``Mapping``, or when its ``dropped_alert_classes``/
       ``unavailable_alert_classes`` entries are not each a bounded list
       of recognized alert-class names -- malformed live evidence can
       never be trusted enough to even evaluate precedence, and its raw
       value is never echoed back.
    1. ``"must-fix"`` when *live_evidence* proves a mandatory alert class
       was actually dropped (``live_evidence["dropped_alert_classes"]``)
       -- proven event loss is never merely a "should fix".
    2. ``"should-fix"`` when the on-disk catalog itself is missing,
       malformed, or declares an incomplete/disabled/payload-bearing
       definition for any required alert class.
    3. ``"not-verified"`` when *live_evidence* says a required alert
       class's live delivery state could not be confirmed
       (``live_evidence["unavailable_alert_classes"]``) -- an
       inaccessible live check is never silently treated as passing.
    4. ``"not-verified"`` when the catalog is complete but this
       function's own evidence for it cannot be source-bound to a real,
       clean git checkout (no ``.git``, unresolved repository/commit, or
       a working tree dirty with respect to the catalog file) -- a
       complete-looking catalog is not itself proof unless it can be
       tied to the exact repository/commit assessed.
    5. ``"pass"`` only once every required alert class is confirmed
       complete on disk, no live evidence reports either drop or
       unavailability, and clean, source-bound evidence for the catalog
       could actually be built.

    Never includes a definition's ``reason_code``/``correlation_id``/any
    other field value in the returned finding -- only the affected alert
    class *names* -- so this assessment can report exactly what is wrong
    without exposing catalog contents that might themselves carry an
    operational secret.
    """
    if live_evidence is not None and not isinstance(live_evidence, Mapping):
        return (
            _alert_finding(
                phase,
                "not-verified",
                "alert-live-evidence-malformed",
                "Live evidence about alert delivery was not in a recognized shape.",
                "The supplied live evidence was not a JSON object, so it could not "
                "be interpreted.",
            ),
            (),
        )

    dropped = _validate_alert_class_list(
        live_evidence.get("dropped_alert_classes") if live_evidence else None
    )
    unavailable = _validate_alert_class_list(
        live_evidence.get("unavailable_alert_classes") if live_evidence else None
    )
    if dropped is None or unavailable is None:
        return (
            _alert_finding(
                phase,
                "not-verified",
                "alert-live-evidence-malformed",
                "Live evidence about alert delivery was not in a recognized shape.",
                "dropped_alert_classes/unavailable_alert_classes must each be a list "
                "of at most eight recognized alert-class names; the supplied value "
                "was not, so it could not be interpreted.",
            ),
            (),
        )

    if dropped:
        return (
            _alert_finding(
                phase,
                "must-fix",
                "alert-mandatory-event-dropped",
                "Live evidence proved a mandatory governance alert event was not "
                "delivered.",
                "The following mandatory alert class(es) were proven dropped rather "
                f"than delivered: {', '.join(dropped)}.",
            ),
            (),
        )

    catalog, load_problem = _load_alert_catalog(root)
    if load_problem is not None:
        return (
            _alert_finding(
                phase,
                "should-fix",
                "alert-catalog-incomplete",
                "The production alert catalog required by design section 17 "
                "could not be confirmed complete.",
                load_problem,
            ),
            (),
        )

    incomplete = _incomplete_alert_classes(catalog)
    if incomplete:
        return (
            _alert_finding(
                phase,
                "should-fix",
                "alert-catalog-incomplete",
                "The production alert catalog required by design section 17 "
                "could not be confirmed complete.",
                "The following required alert class definition(s) are missing, "
                "disabled, incomplete, or declare a payload field in "
                f"{_ALERTS_CATALOG_RELATIVE_PATH.as_posix()}: {', '.join(incomplete)}.",
            ),
            (),
        )

    if unavailable:
        return (
            _alert_finding(
                phase,
                "not-verified",
                "alert-live-state-unavailable",
                "Live delivery state for a required governance alert class could "
                "not be confirmed.",
                "Live delivery state could not be confirmed for the following "
                f"alert class(es): {', '.join(unavailable)}.",
            ),
            (),
        )

    catalog_evidence = _catalog_evidence(root, phase)
    if not catalog_evidence:
        return (
            _alert_finding(
                phase,
                "not-verified",
                "alert-catalog-evidence-unbound",
                "The production alert catalog looked complete but could not be "
                "bound to real, clean git provenance.",
                f"{_ALERTS_CATALOG_RELATIVE_PATH.as_posix()} could not be tied to a "
                "genuine, clean git checkout's repository and commit, so its "
                "completeness cannot be treated as proven.",
            ),
            (),
        )

    return (
        _alert_finding(
            phase,
            "pass",
            "alert-catalog-complete",
            "Every required governance alert class is enabled and declares a "
            "stable reason/correlation identifier.",
            f"All required alert classes ({_ALL_ALERT_CLASSES_TEXT}) are present, "
            "enabled, and payload-free in "
            f"{_ALERTS_CATALOG_RELATIVE_PATH.as_posix()}.",
        ),
        catalog_evidence,
    )
