"""
Tests for reconciliation_emitter.py — publish the actuals/reconciliation pair
plus the human report durably, and render the four separate headline numbers.

See `docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`
§7.3 (canonical pair, immutable history, commit marker) and §7.4 (report).

Core contract under test:
  - Nothing is written until every document, path and hash has validated.
    A validation failure leaves the destination tree byte-for-byte untouched.
  - History under `<root>/<start>--<end>/<generated_at>/` is immutable:
    re-emitting the same payload is a no-op, a different payload for the same
    snapshot raises `HistoryConflictError`, and an interrupted snapshot is
    completed only when the file already on disk matches.
  - Publish order is history, canonical actuals, report, canonical
    reconciliation LAST. The canonical reconciliation is the commit marker:
    every partial failure leaves `canonical_pair_is_complete` False rather
    than a pair that looks published but is not.
  - `canonical_pair_is_complete` never raises. Missing files, garbage bytes,
    a hash mismatch and a `generated_at` mismatch are all False.
  - The report leads with four separate numbers in a fixed order, gates the
    run-rate on maturity and the unit cost on `unit_economics.status`, never
    fabricates 0 for a null, and never phrases observed spend as a reprice.

The fixtures below are built by the real `build_actuals_manifest` /
`reconcile_costs` cores, never hand-written: the emitter validates that
`reconciliation.actuals_ref.sha256` is the canonical hash of the actuals
document it is published with, and only the real cores produce a pair that
genuinely satisfies that.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt", reason="POSIX permission bits do not exist on Windows"
)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import reconciliation_emitter as emitter  # noqa: E402
from cost_actuals import build_actuals_manifest  # noqa: E402
from reconcile import reconcile_costs  # noqa: E402


SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"
RID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-pilot/providers/"
    "Microsoft.App/containerApps/agent"
)
COLUMNS = [
    {"name": "UsageDate", "type": "Number"},
    {"name": "ResourceType", "type": "String"},
    {"name": "PreTaxCost", "type": "Number"},
    {"name": "Currency", "type": "String"},
    {"name": "ResourceId", "type": "String"},
    {"name": "ServiceName", "type": "String"},
]
GENERATED = "2026-08-10T00:00:00Z"
SPEC_SHA256 = hashlib.sha256(b"# SPEC section 14\n").hexdigest()
WINDOW_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 8, tzinfo=timezone.utc)


def cost_page(total=70.0):
    return {
        "properties": {
            "columns": deepcopy(COLUMNS),
            "rows": [
                [
                    20260801,
                    "microsoft.app/containerapps",
                    total,
                    "USD",
                    RID,
                    "Azure Container Apps",
                ]
            ],
            "nextLink": None,
        }
    }


def forecast(total=300.0):
    document = {
        "schema_version": "1.0",
        "price_basis": "retail",
        "resources": [
            {
                "resource_id": RID,
                "resource_kind": "Microsoft.App/containerApps",
                "monthly_cost_usd": total,
            }
        ],
        "recommendations": [],
    }
    if total is not None:
        document["totals"] = {"monthly_cost_current_usd": total}
    return document


def policy():
    return {
        "cost": {
            "maturity_policy": {
                "min_complete_days": 7,
                "min_successful_interactions": 100,
                "min_cost_settlement_age_hours": 48,
                "max_window_end_age_days": 14,
                "min_projection_attribution_coverage_pct": 0.95,
            },
            "success_event": {
                "name": "return_decision_completed",
                "trace_attribute": "decision.outcome",
                "success_values": ["approved"],
            },
            "baseline": {
                "target_cost_per_successful_interaction_usd": 1.0,
                "max_forecast_variance_pct": 0.20,
                "max_token_volume_variance_pct": 0.25,
            },
            "accounting": {
                "actual_cost_basis": "usage-pretax",
                "actual_billing_price_basis": "retail",
                "forecast_price_basis": "retail",
                "allow_basis_mismatch_for_verdict": False,
                "scope_policy": "dedicated_resource_group",
            },
        }
    }


def documents(
    generated_at=GENERATED,
    *,
    total=70.0,
    interaction_counts=(105, 100),
    token_series=None,
    forecast_document=None,
    policy_document=None,
    policy_errors=None,
    provenance=None,
    warnings=None,
    spec_sha256=SPEC_SHA256,
):
    """One genuinely consistent (actuals, reconciliation) pair.

    Both halves come from the real cores, so `actuals_ref.sha256` is the
    canonical hash of the actuals document actually handed to the emitter.
    """
    instant = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    actuals = build_actuals_manifest(
        scope={"subscription_id": SUBSCRIPTION, "resource_group": "rg-pilot"},
        start=WINDOW_START,
        end=WINDOW_END,
        generated_at=instant,
        cost_pages=[cost_page(total)],
        token_series=token_series,
        interaction_counts=interaction_counts,
        provenance={"query_api_version": "2025-03-01"}
        if provenance is None
        else provenance,
        warnings=[] if warnings is None else warnings,
    )
    reconciliation = reconcile_costs(
        forecast() if forecast_document is None else forecast_document,
        actuals,
        policy() if policy_document is None else policy_document,
        policy_errors=[] if policy_errors is None else policy_errors,
        generated_at=generated_at,
        policy_spec_sha256=spec_sha256,
    )
    return actuals, reconciliation


def paths(tmp_path):
    return {
        "report_path": tmp_path / "docs" / "cost-reconciliation.md",
        "actuals_path": tmp_path / "specs" / "cost-actuals-manifest.json",
        "reconciliation_path": (
            tmp_path / "specs" / "cost-reconciliation-manifest.json"
        ),
        "history_root": tmp_path / "specs" / "cost-history",
    }


def emit(tmp_path, actuals=None, reconciliation=None, **overrides):
    if actuals is None and reconciliation is None:
        actuals, reconciliation = documents()
    keywords = paths(tmp_path)
    keywords.update(overrides)
    emitter.emit_reconciliation(
        actuals=actuals, reconciliation=reconciliation, **keywords
    )


def snapshot_dir(tmp_path, generated="2026-08-10T000000Z"):
    return (
        tmp_path
        / "specs"
        / "cost-history"
        / "2026-08-01--2026-08-08"
        / generated
    )


def report_text(tmp_path):
    return (tmp_path / "docs" / "cost-reconciliation.md").read_text(
        encoding="utf-8"
    )


def section(report, heading):
    """Return the body of one `###`/`##` section, excluding its heading."""
    lines = report.splitlines()
    for index, line in enumerate(lines):
        if line.lstrip("#").strip() == heading and line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            body = []
            for candidate in lines[index + 1:]:
                if candidate.startswith("#"):
                    candidate_level = len(candidate) - len(candidate.lstrip("#"))
                    if candidate_level <= level:
                        break
                body.append(candidate)
            return "\n".join(body)
    raise AssertionError(f"no section {heading!r} in report")


def temp_leftovers(tmp_path):
    return [
        str(path.relative_to(tmp_path))
        for path in tmp_path.rglob("*")
        if path.is_file() and path.name.endswith(".tmp")
    ]


# ---------------------------------------------------------------------------
# Canonical pair + immutable history
# ---------------------------------------------------------------------------


def test_writes_canonical_pair_report_and_history_snapshot(tmp_path) -> None:
    emit(tmp_path)
    canonical = tmp_path / "specs" / "cost-actuals-manifest.json"
    assert json.loads(canonical.read_text())["cost"]["period_total_usd"] == 70.0
    assert (tmp_path / "specs" / "cost-reconciliation-manifest.json").is_file()
    assert (tmp_path / "docs" / "cost-reconciliation.md").is_file()
    assert (snapshot_dir(tmp_path) / "actuals.json").is_file()
    assert (snapshot_dir(tmp_path) / "reconciliation.json").is_file()


def test_history_snapshot_is_byte_identical_to_the_canonical_pair(
    tmp_path,
) -> None:
    emit(tmp_path)
    specs = tmp_path / "specs"
    assert (snapshot_dir(tmp_path) / "actuals.json").read_bytes() == (
        specs / "cost-actuals-manifest.json"
    ).read_bytes()
    assert (snapshot_dir(tmp_path) / "reconciliation.json").read_bytes() == (
        specs / "cost-reconciliation-manifest.json"
    ).read_bytes()


def test_same_window_new_collection_creates_new_snapshot(tmp_path) -> None:
    emit(tmp_path)
    actuals, reconciliation = documents("2026-08-11T00:00:00Z")
    emit(tmp_path, actuals, reconciliation)
    history = tmp_path / "specs" / "cost-history" / "2026-08-01--2026-08-08"
    assert sorted(path.name for path in history.iterdir()) == [
        "2026-08-10T000000Z",
        "2026-08-11T000000Z",
    ]


def test_refuses_to_overwrite_a_different_snapshot_payload(tmp_path) -> None:
    emit(tmp_path)
    actuals, reconciliation = documents(total=99.0)
    with pytest.raises(emitter.HistoryConflictError):
        emit(tmp_path, actuals, reconciliation)


def test_history_conflict_leaves_every_destination_untouched(tmp_path) -> None:
    emit(tmp_path)
    before = {
        path: path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    actuals, reconciliation = documents(total=99.0)
    with pytest.raises(emitter.HistoryConflictError):
        emit(tmp_path, actuals, reconciliation)
    after = {
        path: path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_same_payload_is_idempotent(tmp_path) -> None:
    emit(tmp_path)
    emit(tmp_path)
    history = list((tmp_path / "specs" / "cost-history").rglob("actuals.json"))
    assert len(history) == 1


def test_interrupted_history_is_completed_when_the_existing_file_matches(
    tmp_path,
) -> None:
    emit(tmp_path)
    (snapshot_dir(tmp_path) / "reconciliation.json").unlink()
    emit(tmp_path)
    assert (snapshot_dir(tmp_path) / "reconciliation.json").is_file()
    assert emitter.canonical_pair_is_complete(
        tmp_path / "specs" / "cost-actuals-manifest.json",
        tmp_path / "specs" / "cost-reconciliation-manifest.json",
    )


def test_interrupted_history_aborts_when_the_existing_file_differs(
    tmp_path,
) -> None:
    actuals, reconciliation = documents()
    emit(tmp_path, actuals, reconciliation)
    (snapshot_dir(tmp_path) / "reconciliation.json").unlink()
    other_actuals, other_reconciliation = documents(total=99.0)
    with pytest.raises(emitter.HistoryConflictError):
        emit(tmp_path, other_actuals, other_reconciliation)
    assert not (snapshot_dir(tmp_path) / "reconciliation.json").exists()


def test_unparseable_history_entry_is_a_conflict_not_an_overwrite(
    tmp_path,
) -> None:
    emit(tmp_path)
    entry = snapshot_dir(tmp_path) / "actuals.json"
    entry.write_text("{not json", encoding="utf-8")
    with pytest.raises(emitter.HistoryConflictError):
        emit(tmp_path)
    assert entry.read_text(encoding="utf-8") == "{not json"


# ---------------------------------------------------------------------------
# Immutable history is created, never replaced — including under a race
# ---------------------------------------------------------------------------


def racing_history_writer(monkeypatch, entry, payload):
    """Let a competing publisher win the race for `entry`.

    The emitter checks whether a history entry exists BEFORE it stages any
    payload, so a concurrent publisher that creates the same entry in between
    is exactly the window that a blind `os.replace` would silently overwrite.
    Staging is the last hook that still runs inside that window.
    """
    real_stage = emitter._stage

    def stage(destination, text, created):
        temp = real_stage(destination, text, created)
        if Path(destination) == entry and not entry.exists():
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_bytes(payload)
        return temp

    monkeypatch.setattr(emitter, "_stage", stage)


def test_history_entry_created_after_the_check_is_never_overwritten(
    tmp_path, monkeypatch
) -> None:
    """A concurrent publisher's DIFFERENT payload wins the create; this call
    must lose loudly rather than replace evidence it did not write."""
    winner_actuals, _ = documents()
    loser_actuals, loser_reconciliation = documents(total=99.0)
    entry = snapshot_dir(tmp_path) / "actuals.json"
    winner_bytes = json.dumps(winner_actuals, sort_keys=True).encode("utf-8")
    racing_history_writer(monkeypatch, entry, winner_bytes)

    with pytest.raises(emitter.HistoryConflictError):
        emit(tmp_path, loser_actuals, loser_reconciliation)

    assert entry.read_bytes() == winner_bytes


def test_history_entry_created_after_the_check_is_idempotent(
    tmp_path, monkeypatch
) -> None:
    """The same payload from a concurrent publisher is not a conflict — but
    the bytes already on disk are still left exactly as they were found."""
    actuals, reconciliation = documents()
    entry = snapshot_dir(tmp_path) / "actuals.json"
    winner_bytes = json.dumps(actuals, sort_keys=True).encode("utf-8")
    racing_history_writer(monkeypatch, entry, winner_bytes)

    emit(tmp_path, actuals, reconciliation)

    assert entry.read_bytes() == winner_bytes
    assert (snapshot_dir(tmp_path) / "reconciliation.json").is_file()
    assert emitter.canonical_pair_is_complete(
        tmp_path / "specs" / "cost-actuals-manifest.json",
        tmp_path / "specs" / "cost-reconciliation-manifest.json",
    )


def test_history_entries_are_created_by_link_never_by_replace(
    tmp_path, monkeypatch
) -> None:
    """`os.replace` overwrites unconditionally, so it can never be the call
    that puts an immutable entry in place."""
    replaced = []
    real_replace = emitter.os.replace

    def replace(source, destination):
        replaced.append(Path(destination).name)
        return real_replace(source, destination)

    monkeypatch.setattr(emitter.os, "replace", replace)
    emit(tmp_path)
    assert "actuals.json" not in replaced
    assert "reconciliation.json" not in replaced
    assert (snapshot_dir(tmp_path) / "actuals.json").is_file()
    assert (snapshot_dir(tmp_path) / "reconciliation.json").is_file()


def test_history_link_failure_is_reported_as_an_io_error(
    tmp_path, monkeypatch
) -> None:
    """`os.link` exists on Windows but can fail on filesystems that have no
    hard links. That must surface as a legible I/O failure, not as a
    HistoryConflictError claiming somebody else's evidence is in the way."""

    def link(source, destination, **kwargs):
        raise OSError(1, "operation not permitted")

    monkeypatch.setattr(emitter.os, "link", link)
    with pytest.raises(OSError, match="hard link"):
        emit(tmp_path)
    assert not (tmp_path / "specs" / "cost-actuals-manifest.json").exists()


# ---------------------------------------------------------------------------
# Partial-write phases — the commit marker never lies
# ---------------------------------------------------------------------------


def failing_publish(monkeypatch, marker):
    """Fail the publish of whichever destination ends with `marker`.

    Both publish primitives are patched: canonical artifacts are replaced,
    immutable history entries are LINKED (never replaced), so a helper that
    only knew about `os.replace` could no longer reach the history phases.
    """
    real_replace = emitter.os.replace
    real_link = emitter.os.link
    calls = []

    def replace(source, destination):
        calls.append(str(destination))
        if str(destination).endswith(marker):
            raise OSError("simulated replace failure")
        return real_replace(source, destination)

    def link(source, destination):
        calls.append(str(destination))
        if str(destination).endswith(marker):
            raise OSError("simulated link failure")
        return real_link(source, destination)

    monkeypatch.setattr(emitter.os, "replace", replace)
    monkeypatch.setattr(emitter.os, "link", link)
    return calls


def test_partial_write_cannot_publish_a_false_completed_pair(
    tmp_path, monkeypatch
) -> None:
    emit(tmp_path)
    actuals_path = tmp_path / "specs" / "cost-actuals-manifest.json"
    reconciliation_path = tmp_path / "specs" / "cost-reconciliation-manifest.json"
    old_reconciliation = reconciliation_path.read_bytes()
    actuals, reconciliation = documents("2026-08-11T00:00:00Z")

    failing_publish(monkeypatch, "cost-reconciliation-manifest.json")
    with pytest.raises(OSError, match="simulated"):
        emit(tmp_path, actuals, reconciliation)

    assert json.loads(actuals_path.read_text())["generated_at"] == (
        "2026-08-11T00:00:00Z"
    )
    assert reconciliation_path.read_bytes() == old_reconciliation
    assert (
        emitter.canonical_pair_is_complete(actuals_path, reconciliation_path)
        is False
    )


def test_report_failure_also_leaves_the_pair_incomplete(
    tmp_path, monkeypatch
) -> None:
    emit(tmp_path)
    actuals_path = tmp_path / "specs" / "cost-actuals-manifest.json"
    reconciliation_path = tmp_path / "specs" / "cost-reconciliation-manifest.json"
    actuals, reconciliation = documents("2026-08-11T00:00:00Z")

    failing_publish(monkeypatch, "cost-reconciliation.md")
    with pytest.raises(OSError, match="simulated"):
        emit(tmp_path, actuals, reconciliation)

    assert (
        emitter.canonical_pair_is_complete(actuals_path, reconciliation_path)
        is False
    )


def test_canonical_actuals_failure_keeps_the_previous_published_pair(
    tmp_path, monkeypatch
) -> None:
    """Actuals is replaced first, so a failure there leaves BOTH canonical
    files at their previous, mutually consistent revision: still a complete
    pair, just an older one. That is the honest outcome — no half-published
    newer evidence is visible."""
    emit(tmp_path)
    actuals_path = tmp_path / "specs" / "cost-actuals-manifest.json"
    reconciliation_path = tmp_path / "specs" / "cost-reconciliation-manifest.json"
    before = (actuals_path.read_bytes(), reconciliation_path.read_bytes())
    actuals, reconciliation = documents("2026-08-11T00:00:00Z")

    failing_publish(monkeypatch, "cost-actuals-manifest.json")
    with pytest.raises(OSError, match="simulated"):
        emit(tmp_path, actuals, reconciliation)

    assert (actuals_path.read_bytes(), reconciliation_path.read_bytes()) == before
    assert (
        emitter.canonical_pair_is_complete(actuals_path, reconciliation_path)
        is True
    )


def test_history_failure_publishes_no_canonical_file(
    tmp_path, monkeypatch
) -> None:
    actuals, reconciliation = documents()
    failing_publish(monkeypatch, "actuals.json")
    with pytest.raises(OSError, match="simulated"):
        emit(tmp_path, actuals, reconciliation)
    assert not (tmp_path / "specs" / "cost-actuals-manifest.json").exists()
    assert not (
        tmp_path / "specs" / "cost-reconciliation-manifest.json"
    ).exists()
    assert not (tmp_path / "docs" / "cost-reconciliation.md").exists()


@pytest.mark.parametrize(
    "marker",
    [
        "cost-history/2026-08-01--2026-08-08/2026-08-10T000000Z/actuals.json",
        "cost-actuals-manifest.json",
        "cost-reconciliation.md",
        "cost-reconciliation-manifest.json",
    ],
)
def test_every_failed_publish_phase_cleans_up_its_temp_files(
    tmp_path, monkeypatch, marker
) -> None:
    actuals, reconciliation = documents()
    failing_publish(monkeypatch, marker)
    with pytest.raises(OSError, match="simulated"):
        emit(tmp_path, actuals, reconciliation)
    assert temp_leftovers(tmp_path) == []


def test_successful_emission_leaves_no_temp_files(tmp_path) -> None:
    emit(tmp_path)
    assert temp_leftovers(tmp_path) == []


def test_publish_order_is_history_then_actuals_report_reconciliation_last(
    tmp_path, monkeypatch
) -> None:
    events = []
    real_replace = emitter.os.replace
    real_link = emitter.os.link
    real_fsync = emitter.os.fsync

    def replace(source, destination):
        events.append(("publish", Path(destination).name))
        return real_replace(source, destination)

    def link(source, destination):
        events.append(("publish", Path(destination).name))
        return real_link(source, destination)

    def fsync(descriptor):
        kind = (
            "dirsync"
            if stat.S_ISDIR(os.fstat(descriptor).st_mode)
            else "fsync"
        )
        events.append((kind, None))
        return real_fsync(descriptor)

    monkeypatch.setattr(emitter.os, "replace", replace)
    monkeypatch.setattr(emitter.os, "link", link)
    monkeypatch.setattr(emitter.os, "fsync", fsync)
    emit(tmp_path)

    publishes = [name for kind, name in events if kind == "publish"]
    assert publishes == [
        "actuals.json",
        "reconciliation.json",
        "cost-actuals-manifest.json",
        "cost-reconciliation.md",
        "cost-reconciliation-manifest.json",
    ]
    first_publish = next(
        index for index, event in enumerate(events) if event[0] == "publish"
    )
    # Every staged temp file is fsynced before anything is published, and the
    # parent directory is fsynced immediately after each publish.
    before = [event[0] for event in events[:first_publish]]
    assert before.count("fsync") == 5
    assert "publish" not in before
    for index, event in enumerate(events):
        if event[0] == "publish":
            assert events[index + 1][0] == "dirsync"
    assert events[-1][0] == "dirsync"


# ---------------------------------------------------------------------------
# canonical_pair_is_complete — never raises
# ---------------------------------------------------------------------------


def test_canonical_pair_is_complete_after_a_successful_emission(
    tmp_path,
) -> None:
    emit(tmp_path)
    assert (
        emitter.canonical_pair_is_complete(
            tmp_path / "specs" / "cost-actuals-manifest.json",
            tmp_path / "specs" / "cost-reconciliation-manifest.json",
        )
        is True
    )


@pytest.mark.parametrize("missing", ["actuals", "reconciliation", "both"])
def test_canonical_pair_is_false_when_a_file_is_missing(
    tmp_path, missing
) -> None:
    emit(tmp_path)
    actuals_path = tmp_path / "specs" / "cost-actuals-manifest.json"
    reconciliation_path = tmp_path / "specs" / "cost-reconciliation-manifest.json"
    if missing in ("actuals", "both"):
        actuals_path.unlink()
    if missing in ("reconciliation", "both"):
        reconciliation_path.unlink()
    assert (
        emitter.canonical_pair_is_complete(actuals_path, reconciliation_path)
        is False
    )


def test_canonical_pair_is_false_for_garbage_bytes(tmp_path) -> None:
    emit(tmp_path)
    actuals_path = tmp_path / "specs" / "cost-actuals-manifest.json"
    actuals_path.write_text("not json at all", encoding="utf-8")
    assert (
        emitter.canonical_pair_is_complete(
            actuals_path,
            tmp_path / "specs" / "cost-reconciliation-manifest.json",
        )
        is False
    )


def test_canonical_pair_is_false_when_the_actuals_payload_changed(
    tmp_path,
) -> None:
    emit(tmp_path)
    actuals_path = tmp_path / "specs" / "cost-actuals-manifest.json"
    document = json.loads(actuals_path.read_text())
    document["cost"]["period_total_usd"] = 71.0
    actuals_path.write_text(json.dumps(document), encoding="utf-8")
    assert (
        emitter.canonical_pair_is_complete(
            actuals_path,
            tmp_path / "specs" / "cost-reconciliation-manifest.json",
        )
        is False
    )


def test_canonical_pair_is_false_on_generated_at_disagreement(tmp_path) -> None:
    emit(tmp_path)
    reconciliation_path = tmp_path / "specs" / "cost-reconciliation-manifest.json"
    document = json.loads(reconciliation_path.read_text())
    document["generated_at"] = "2026-08-11T00:00:00Z"
    reconciliation_path.write_text(json.dumps(document), encoding="utf-8")
    assert (
        emitter.canonical_pair_is_complete(
            tmp_path / "specs" / "cost-actuals-manifest.json",
            reconciliation_path,
        )
        is False
    )


def test_canonical_pair_is_false_for_a_wrong_schema(tmp_path) -> None:
    emit(tmp_path)
    reconciliation_path = tmp_path / "specs" / "cost-reconciliation-manifest.json"
    document = json.loads(reconciliation_path.read_text())
    document["schema"] = "threadlight-cost-reconciliation/v2"
    reconciliation_path.write_text(json.dumps(document), encoding="utf-8")
    assert (
        emitter.canonical_pair_is_complete(
            tmp_path / "specs" / "cost-actuals-manifest.json",
            reconciliation_path,
        )
        is False
    )


def test_canonical_pair_never_raises_for_a_directory_or_a_json_scalar(
    tmp_path,
) -> None:
    emit(tmp_path)
    scalar = tmp_path / "scalar.json"
    scalar.write_text("42", encoding="utf-8")
    assert emitter.canonical_pair_is_complete(tmp_path, tmp_path) is False
    assert (
        emitter.canonical_pair_is_complete(
            scalar, tmp_path / "specs" / "cost-reconciliation-manifest.json"
        )
        is False
    )


# ---------------------------------------------------------------------------
# Validation before any write
# ---------------------------------------------------------------------------


def assert_nothing_written(tmp_path) -> None:
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize("half", ["actuals", "reconciliation"])
def test_non_mapping_document_is_rejected(tmp_path, half) -> None:
    actuals, reconciliation = documents()
    if half == "actuals":
        actuals = [actuals]
    else:
        reconciliation = [reconciliation]
    with pytest.raises(emitter.EmissionValidationError):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize("half", ["actuals", "reconciliation"])
def test_wrong_schema_is_rejected(tmp_path, half) -> None:
    actuals, reconciliation = documents()
    target = actuals if half == "actuals" else reconciliation
    target["schema"] = "threadlight-something-else/v1"
    with pytest.raises(emitter.EmissionValidationError, match="schema"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


def test_generated_at_disagreement_is_rejected(tmp_path) -> None:
    actuals, reconciliation = documents()
    reconciliation["generated_at"] = "2026-08-11T00:00:00Z"
    with pytest.raises(emitter.EmissionValidationError, match="generated_at"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "value",
    ["2026-08-10T00:00:00+00:00", "2026-08-10 00:00:00Z", "2026-02-30T00:00:00Z", ""],
)
def test_non_canonical_generated_at_is_rejected(tmp_path, value) -> None:
    actuals, reconciliation = documents()
    actuals["generated_at"] = value
    reconciliation["generated_at"] = value
    with pytest.raises(emitter.EmissionValidationError, match="generated_at"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-08-08T00:00:00Z", "2026-08-08T00:00:00Z"),
        ("2026-08-09T00:00:00Z", "2026-08-08T00:00:00Z"),
        ("2026-08-01", "2026-08-08T00:00:00Z"),
    ],
)
def test_invalid_window_is_rejected(tmp_path, start, end) -> None:
    actuals, reconciliation = documents()
    actuals["window"]["start"] = start
    actuals["window"]["end"] = end
    with pytest.raises(emitter.EmissionValidationError, match="window"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


def test_actuals_hash_mismatch_is_rejected(tmp_path) -> None:
    actuals, reconciliation = documents()
    actuals["cost"]["period_total_usd"] = 71.0
    with pytest.raises(emitter.EmissionValidationError, match="actuals_ref"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "ref,key",
    [
        ("forecast_ref", "sha256"),
        ("actuals_ref", "sha256"),
    ],
)
@pytest.mark.parametrize("bad", [None, "", "deadbeef", 64 * "z", 12345])
def test_reference_digests_must_be_64_hex(tmp_path, ref, key, bad) -> None:
    """`forecast_ref.sha256` and `actuals_ref.sha256` are computed by this
    module or by the cores from bytes actually published alongside them, so
    an unusable value there is a caller bug, not degraded evidence: it must
    never publish. `policy_ref.spec_sha256` is exempt from this — see
    `test_policy_ref_*` below."""
    actuals, reconciliation = documents()
    reconciliation[ref][key] = bad
    with pytest.raises(emitter.EmissionValidationError, match=ref):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize("bad", [None, 12345, 3.14, ["not", "a", "string"]])
def test_policy_ref_spec_sha256_must_be_a_string(tmp_path, bad) -> None:
    """The core only ever raises for a caller TYPE error (see
    `reconcile.reconcile_costs`); a non-string `spec_sha256` is exactly that,
    never degraded evidence, so the emitter refuses to publish it too."""
    actuals, reconciliation = documents()
    reconciliation["policy_ref"]["spec_sha256"] = bad
    with pytest.raises(emitter.EmissionValidationError, match="policy_ref"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "bad",
    ["", "TBD", "not-a-digest", "deadbeef", 64 * "z"],
)
def test_policy_ref_invalid_anchor_still_publishes_degraded_evidence(
    tmp_path, bad
) -> None:
    """An invalid/non-64-hex `policy_ref.spec_sha256` is exactly the shape
    `reconcile_costs` deliberately EMITS — the core warns and degrades every
    gated verdict to `not-verified` instead of suppressing the artifact — so
    the emitter MUST still publish it: refusing would hide the very warning
    and observed numbers a consumer needs to see."""
    actuals, reconciliation = documents(spec_sha256=bad)
    assert reconciliation["status"] == emitter.NOT_VERIFIED
    assert any("spec_sha256" in warning for warning in reconciliation["warnings"])

    emit(tmp_path, actuals, reconciliation)

    canonical = tmp_path / "specs" / "cost-reconciliation-manifest.json"
    assert json.loads(canonical.read_text())["policy_ref"]["spec_sha256"] == bad
    assert (tmp_path / "specs" / "cost-actuals-manifest.json").is_file()
    assert (snapshot_dir(tmp_path) / "actuals.json").is_file()
    assert (snapshot_dir(tmp_path) / "reconciliation.json").is_file()

    report = report_text(tmp_path)
    assert bad in report
    policy_section = section(report, "Declared policy")
    assert "not-verified" in report
    assert any(
        "spec_sha256" in warning and warning in report
        for warning in reconciliation["warnings"]
    )
    assert bad in policy_section


@pytest.mark.parametrize("key", ["path", "section", "spec_sha256"])
def test_policy_ref_missing_key_is_rejected(tmp_path, key) -> None:
    actuals, reconciliation = documents()
    del reconciliation["policy_ref"][key]
    with pytest.raises(emitter.EmissionValidationError, match="policy_ref"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


def test_policy_ref_must_be_a_mapping(tmp_path) -> None:
    actuals, reconciliation = documents()
    reconciliation["policy_ref"] = "specs/SPEC.md"
    with pytest.raises(emitter.EmissionValidationError, match="policy_ref"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "key",
    [
        "status",
        "variance_status",
        "maturity",
        "totals",
        "unit_economics",
        "coverage",
        "drivers",
        "policy_snapshot",
        "policy_errors",
        "warnings",
    ],
)
def test_missing_required_reconciliation_key_is_rejected(tmp_path, key) -> None:
    actuals, reconciliation = documents()
    del reconciliation[key]
    with pytest.raises(emitter.EmissionValidationError, match=key):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "key", ["status", "scope", "window", "cost", "usage", "provenance", "warnings"]
)
def test_missing_required_actuals_key_is_rejected(tmp_path, key) -> None:
    actuals, reconciliation = documents()
    del actuals[key]
    with pytest.raises(emitter.EmissionValidationError, match=key):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "fail"),
        ("variance_status", "ok"),
        ("status", None),
    ],
)
def test_unknown_status_value_is_rejected(tmp_path, field, value) -> None:
    actuals, reconciliation = documents()
    reconciliation[field] = value
    with pytest.raises(emitter.EmissionValidationError, match=field):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


def test_unit_economics_status_must_be_a_known_verdict(tmp_path) -> None:
    actuals, reconciliation = documents()
    reconciliation["unit_economics"]["status"] = "great"
    with pytest.raises(emitter.EmissionValidationError, match="unit_economics"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


def test_nan_is_rejected_rather_than_serialized(tmp_path) -> None:
    actuals, reconciliation = documents()
    reconciliation["totals"]["variance_pct"] = float("nan")
    with pytest.raises(emitter.EmissionValidationError):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


def test_non_serializable_payload_is_rejected(tmp_path) -> None:
    actuals, reconciliation = documents()
    reconciliation["warnings"] = [{1, 2}]
    with pytest.raises(emitter.EmissionValidationError):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


# ---------------------------------------------------------------------------
# Credential-shaped evidence never reaches immutable history
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provenance",
    [
        {"query_api_version": "2025-03-01", "token": "abc"},
        {"query_api_version": "2025-03-01", "access_token": "abc"},
        {"query_api_version": "2025-03-01", "refresh_token": "abc"},
        {"query_api_version": "2025-03-01", "bearer_token": "abc"},
        {"query_api_version": "2025-03-01", "Authorization": "abc"},
        {"query_api_version": "2025-03-01", "secret": "abc"},
        {"query_api_version": "2025-03-01", "client_secret": "abc"},
        {"query_api_version": "2025-03-01", "password": "abc"},
        {"nested": {"api_key": "abc"}},
    ],
)
def test_credential_shaped_provenance_key_is_rejected(
    tmp_path, provenance
) -> None:
    actuals, reconciliation = documents(provenance=provenance)
    with pytest.raises(emitter.EmissionValidationError, match="credential"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "warning",
    [
        "retrying with authorization: Bearer eyJhbGciOiJIUzI1NiJ9",
        "az login failed (password=hunter2)",
        "client_secret: s3cr3t-value",
    ],
)
def test_credential_shaped_warning_value_is_rejected(tmp_path, warning) -> None:
    actuals, reconciliation = documents(warnings=[warning])
    with pytest.raises(emitter.EmissionValidationError, match="credential"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "warning",
    [
        "token: eyJhbGciOiJIUzI1NiJ9",
        "token=eyJhbGciOiJIUzI1NiJ9",
        "Token : eyJhbGciOiJIUzI1NiJ9",
        "captured token   =   abc123",
        "https://acct.blob.core.windows.net/c?sv=2025-01-01&sig=Ab%2FcD3",
        "https://acct.blob.core.windows.net/c?SIG=Ab%2FcD3",
        "DefaultEndpointsProtocol=https;AccountKey=Zm9vYmFyYmF6;",
        "accountkey = Zm9vYmFyYmF6",
        "AccountKey=Zm9vYmFyYmF6",
    ],
)
def test_assignment_shaped_credentials_in_values_are_rejected(
    tmp_path, warning
) -> None:
    """A bare `token`/`sig`/`accountkey` immediately followed by an
    assignment is the exact shape of a leaked bearer token, a SAS query
    signature and a storage connection string. History is immutable, so any
    of them must fail the emission rather than be published forever."""
    actuals, reconciliation = documents(warnings=[warning])
    with pytest.raises(emitter.EmissionValidationError, match="credential"):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "warning",
    [
        "max_token_volume_variance_pct was not declared",
        "token volume evidence was not collected for this window",
        "input_tokens: 1200 and output_tokens: 300 were observed",
        "model_token_count: 12345 came from Azure Monitor",
        "design=hub-and-spoke was recorded by the operator",
        "the assignment of tokens to resources is incomplete",
        "token_metrics doc: specs/cost-manifest.json#tokens",
        "no design=x, sizing=y or config=z evidence was collected",
    ],
)
def test_token_volume_prose_and_threshold_fields_still_publish(
    tmp_path, warning
) -> None:
    """The reconciliation core's own warnings discuss token VOLUME, and
    `max_token_volume_variance_pct` is a declared threshold name. A guard
    that refused those would suppress the evidence it exists to protect."""
    actuals, reconciliation = documents(warnings=[warning])
    emit(tmp_path, actuals, reconciliation)
    published = json.loads(
        (tmp_path / "specs" / "cost-actuals-manifest.json").read_text()
    )
    assert warning in published["warnings"]


def test_identifiers_and_token_metrics_are_not_treated_as_credentials(
    tmp_path,
) -> None:
    """IDs are evidence, not secrets, and the reconciliation's own warnings
    legitimately discuss token VOLUME. Neither may be mistaken for a leak."""
    actuals, reconciliation = documents(
        provenance={
            "query_api_version": "2025-03-01",
            "subscription_id": SUBSCRIPTION,
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "correlation_id": "22222222-2222-2222-2222-222222222222",
        },
        warnings=["max_token_volume_variance_pct was not declared"],
    )
    emit(tmp_path, actuals, reconciliation)
    assert (tmp_path / "specs" / "cost-actuals-manifest.json").is_file()


def test_legitimate_token_provenance_keys_are_not_treated_as_credentials(
    tmp_path,
) -> None:
    """`token_doc`, `token_source_resource_id`, `model_token_count` and
    `token_metrics` are real provenance keys the cores populate to describe
    TOKEN VOLUME evidence. The credential guard matches a key's full
    normalized spelling, not any substring, so none of these — despite each
    containing "token" — may be mistaken for a bearer credential."""
    actuals, reconciliation = documents(
        provenance={
            "query_api_version": "2025-03-01",
            "token_doc": "specs/cost-manifest.json#tokens",
            "token_source_resource_id": RID,
            "model_token_count": 12345,
            "token_metrics": {"input_tokens": 100, "output_tokens": 50},
        },
    )
    emit(tmp_path, actuals, reconciliation)
    assert (tmp_path / "specs" / "cost-actuals-manifest.json").is_file()
    published = json.loads(
        (tmp_path / "specs" / "cost-actuals-manifest.json").read_text()
    )
    assert published["provenance"]["token_doc"] == "specs/cost-manifest.json#tokens"
    assert published["provenance"]["token_source_resource_id"] == RID
    assert published["provenance"]["model_token_count"] == 12345
    assert published["provenance"]["token_metrics"] == {
        "input_tokens": 100,
        "output_tokens": 50,
    }


def test_real_reconciliation_warnings_are_never_flagged(tmp_path) -> None:
    """A degraded run emits warnings from the real core (token attribution,
    undefined variance, unusable SPEC anchor). None of them may trip the
    credential guard, and the emitter MUST still publish: an invalid anchor
    is evidence the core deliberately emits, not a reason to suppress the
    artifact."""
    actuals, reconciliation = documents(
        forecast_document=forecast(0.0),
        interaction_counts=None,
        spec_sha256="not-a-digest",
    )
    assert reconciliation["warnings"]
    assert reconciliation["status"] == emitter.NOT_VERIFIED
    emit(tmp_path, actuals, reconciliation)
    assert (tmp_path / "docs" / "cost-reconciliation.md").is_file()
    assert (tmp_path / "specs" / "cost-reconciliation-manifest.json").is_file()
    assert (tmp_path / "specs" / "cost-actuals-manifest.json").is_file()
    assert (snapshot_dir(tmp_path) / "actuals.json").is_file()
    assert (snapshot_dir(tmp_path) / "reconciliation.json").is_file()


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def test_destination_paths_must_be_distinct(tmp_path) -> None:
    actuals, reconciliation = documents()
    with pytest.raises(emitter.EmissionValidationError, match="distinct"):
        emit(
            tmp_path,
            actuals,
            reconciliation,
            report_path=tmp_path / "specs" / "cost-actuals-manifest.json",
        )
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    "alias",
    [
        "COST-ACTUALS-MANIFEST.JSON",
        "Cost-Actuals-Manifest.json",
        "cost-actuals-MANIFEST.json",
    ],
)
def test_case_only_alias_destinations_are_rejected(tmp_path, alias) -> None:
    """On a case-insensitive operator filesystem (macOS, Windows) these two
    spellings are ONE file, so publishing both would silently overwrite one
    artifact with another. The identity is normalized conservatively, so the
    pair is refused on every platform rather than only where it happens to
    collide."""
    actuals, reconciliation = documents()
    with pytest.raises(emitter.EmissionValidationError, match="distinct"):
        emit(
            tmp_path,
            actuals,
            reconciliation,
            reconciliation_path=tmp_path / "specs" / alias,
        )
    assert_nothing_written(tmp_path)


def test_case_only_alias_of_a_history_entry_is_rejected(tmp_path) -> None:
    actuals, reconciliation = documents()
    aliased = (
        tmp_path
        / "specs"
        / "COST-HISTORY"
        / "2026-08-01--2026-08-08"
        / "2026-08-10T000000Z"
        / "actuals.json"
    )
    with pytest.raises(emitter.EmissionValidationError, match="distinct"):
        emit(tmp_path, actuals, reconciliation, actuals_path=aliased)
    assert_nothing_written(tmp_path)


def test_alias_through_a_symlinked_ancestor_is_rejected(tmp_path) -> None:
    """Neither path is itself a symlink and neither parent is, so only
    resolving the whole path reveals that both name the same file."""
    actuals, reconciliation = documents()
    real = tmp_path / "real"
    (real / "docs").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    with pytest.raises(emitter.EmissionValidationError, match="distinct"):
        emit(
            tmp_path,
            actuals,
            reconciliation,
            report_path=real / "docs" / "shared.md",
            actuals_path=tmp_path / "link" / "docs" / "shared.md",
        )
    assert list((real / "docs").iterdir()) == []


def test_distinct_paths_that_merely_share_a_prefix_still_publish(
    tmp_path,
) -> None:
    """The conservative identity must not collapse genuinely different
    files: a normalization that over-matches would refuse valid emissions."""
    emit(
        tmp_path,
        report_path=tmp_path / "docs" / "cost-reconciliation.md",
        actuals_path=tmp_path / "specs" / "cost-actuals-manifest.json",
        reconciliation_path=tmp_path / "specs" / "cost-actuals-manifest2.json",
        history_root=tmp_path / "specs" / "cost-history",
    )
    assert (tmp_path / "specs" / "cost-actuals-manifest.json").is_file()
    assert (tmp_path / "specs" / "cost-actuals-manifest2.json").is_file()


# ---------------------------------------------------------------------------
# Platform durability — directory fsync, permissions, cleanup
# ---------------------------------------------------------------------------


class WindowsOs:
    """`os` as it looks on Windows: everything real except `name`.

    Setting the real `os.name` is not an option — `pathlib` reads it at call
    time to pick `WindowsPath`, which cannot be instantiated on POSIX — so
    the emitter's own module-level `os` reference is swapped for this proxy.
    Every call it makes still reaches the real `os`; only `os.name` lies, and
    that is exactly the branch under test.
    """

    name = "nt"

    def __init__(self, directories):
        self._directories = directories

    def open(self, path, flags, *args, **kwargs):
        if os.path.isdir(path):
            self._directories.append(str(path))
        return os.open(path, flags, *args, **kwargs)

    def __getattr__(self, attribute):
        return getattr(os, attribute)


def windows_os(monkeypatch):
    directories = []
    monkeypatch.setattr(emitter, "os", WindowsOs(directories))
    return directories


def test_directory_fsync_is_a_no_op_on_windows(tmp_path, monkeypatch) -> None:
    """Windows has no file descriptor for a directory, so `os.open` on one
    raises `PermissionError`. The guard must come BEFORE the open, not around
    it, so no descriptor is ever requested."""
    directories = windows_os(monkeypatch)
    emitter._fsync_directory(tmp_path)
    assert directories == []


def test_emission_never_opens_a_directory_on_windows(
    tmp_path, monkeypatch
) -> None:
    """A mid-publish `PermissionError` would abort AFTER some artifacts were
    already renamed into place — the one failure mode this module exists to
    prevent. On Windows the whole emission must simply complete."""
    directories = windows_os(monkeypatch)
    emit(tmp_path)
    assert directories == []
    assert (tmp_path / "specs" / "cost-actuals-manifest.json").is_file()
    assert (tmp_path / "specs" / "cost-reconciliation-manifest.json").is_file()
    assert (tmp_path / "docs" / "cost-reconciliation.md").is_file()
    assert (snapshot_dir(tmp_path) / "actuals.json").is_file()
    assert (snapshot_dir(tmp_path) / "reconciliation.json").is_file()
    assert emitter.canonical_pair_is_complete(
        tmp_path / "specs" / "cost-actuals-manifest.json",
        tmp_path / "specs" / "cost-reconciliation-manifest.json",
    )


def test_newly_created_directories_are_fsynced_before_the_first_publish(
    tmp_path, monkeypatch
) -> None:
    """A directory entry that is not fsynced can vanish in a crash, taking
    the artifact inside it with it. Every directory this call creates is
    persisted BEFORE the first rename, so no directory fsync can fail after
    a canonical file is already visible."""
    if os.name == "nt":
        pytest.skip("directory descriptors do not exist on Windows")
    events = []
    real_fsync_directory = emitter._fsync_directory
    real_replace = emitter.os.replace
    real_link = emitter.os.link

    def fsync_directory(path):
        events.append(("dirsync", str(path)))
        return real_fsync_directory(path)

    def replace(source, destination):
        events.append(("publish", str(destination)))
        return real_replace(source, destination)

    def link(source, destination):
        events.append(("publish", str(destination)))
        return real_link(source, destination)

    monkeypatch.setattr(emitter, "_fsync_directory", fsync_directory)
    monkeypatch.setattr(emitter.os, "replace", replace)
    monkeypatch.setattr(emitter.os, "link", link)
    emit(tmp_path)

    first_publish = next(
        index for index, event in enumerate(events) if event[0] == "publish"
    )
    synced_first = {path for kind, path in events[:first_publish]}
    for created in (
        tmp_path / "specs",
        tmp_path / "docs",
        tmp_path / "specs" / "cost-history",
        tmp_path / "specs" / "cost-history" / "2026-08-01--2026-08-08",
        snapshot_dir(tmp_path),
    ):
        assert str(created) in synced_first


ARTIFACTS = (
    "docs/cost-reconciliation.md",
    "specs/cost-actuals-manifest.json",
    "specs/cost-reconciliation-manifest.json",
    "specs/cost-history/2026-08-01--2026-08-08/2026-08-10T000000Z/actuals.json",
    (
        "specs/cost-history/2026-08-01--2026-08-08/2026-08-10T000000Z/"
        "reconciliation.json"
    ),
)


@POSIX_ONLY
@pytest.mark.parametrize("relative", ARTIFACTS)
def test_published_artifacts_are_group_and_world_readable(
    tmp_path, relative
) -> None:
    """`tempfile` stages at 0600, and a published artifact that inherits that
    mode is unreadable to the CI job, the reviewer and every downstream tool
    that consumes this evidence."""
    emit(tmp_path)
    assert stat.S_IMODE((tmp_path / relative).stat().st_mode) == 0o644
    assert stat.S_IMODE((tmp_path / relative).stat().st_mode) == (
        emitter.ARTIFACT_MODE
    )


def test_cleanup_never_masks_the_original_failure(
    tmp_path, monkeypatch
) -> None:
    """If unlinking a temp file also fails, the caller must still see WHY the
    publish failed, not a secondary bookkeeping error."""
    actuals, reconciliation = documents()
    failing_publish(monkeypatch, "cost-reconciliation-manifest.json")

    def unlink(path):
        raise PermissionError("cleanup is not permitted")

    monkeypatch.setattr(emitter.os, "unlink", unlink)
    with pytest.raises(OSError, match="simulated"):
        emit(tmp_path, actuals, reconciliation)


def test_cleanup_failure_does_not_break_a_successful_emission(
    tmp_path, monkeypatch
) -> None:
    def unlink(path):
        raise PermissionError("cleanup is not permitted")

    monkeypatch.setattr(emitter.os, "unlink", unlink)
    emit(tmp_path)
    assert emitter.canonical_pair_is_complete(
        tmp_path / "specs" / "cost-actuals-manifest.json",
        tmp_path / "specs" / "cost-reconciliation-manifest.json",
    )


def test_symlinked_destination_file_is_rejected(tmp_path) -> None:
    actuals, reconciliation = documents()
    specs = tmp_path / "specs"
    specs.mkdir()
    target = tmp_path / "elsewhere.json"
    target.write_text("{}", encoding="utf-8")
    (specs / "cost-actuals-manifest.json").symlink_to(target)
    with pytest.raises(emitter.EmissionValidationError, match="symlink"):
        emit(tmp_path, actuals, reconciliation)
    assert target.read_text(encoding="utf-8") == "{}"


def test_symlinked_destination_directory_is_rejected(tmp_path) -> None:
    actuals, reconciliation = documents()
    real_docs = tmp_path / "real-docs"
    real_docs.mkdir()
    (tmp_path / "docs").symlink_to(real_docs, target_is_directory=True)
    with pytest.raises(emitter.EmissionValidationError, match="symlink"):
        emit(tmp_path, actuals, reconciliation)
    assert list(real_docs.iterdir()) == []


def test_symlinked_history_root_is_rejected(tmp_path) -> None:
    actuals, reconciliation = documents()
    real_history = tmp_path / "real-history"
    real_history.mkdir()
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "cost-history").symlink_to(real_history, target_is_directory=True)
    with pytest.raises(emitter.EmissionValidationError, match="symlink"):
        emit(tmp_path, actuals, reconciliation)
    assert list(real_history.iterdir()) == []


def test_history_snapshot_path_is_derived_only_from_parsed_instants(
    tmp_path,
) -> None:
    """The snapshot directory names are re-formatted from parsed `datetime`
    values, so a traversal-shaped string in the source documents cannot
    escape `history_root` — it fails validation first."""
    actuals, reconciliation = documents()
    actuals["window"]["start"] = "../../etc"
    with pytest.raises(emitter.EmissionValidationError):
        emit(tmp_path, actuals, reconciliation)
    assert_nothing_written(tmp_path)


# ---------------------------------------------------------------------------
# Determinism and input purity
# ---------------------------------------------------------------------------


def test_emission_never_mutates_its_inputs(tmp_path) -> None:
    actuals, reconciliation = documents()
    actuals_snapshot = deepcopy(actuals)
    reconciliation_snapshot = deepcopy(reconciliation)
    emit(tmp_path, actuals, reconciliation)
    assert actuals == actuals_snapshot
    assert reconciliation == reconciliation_snapshot


def test_emission_is_byte_deterministic(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    actuals, reconciliation = documents()
    emit(first, actuals, reconciliation)
    emit(second, *documents())
    for relative in (
        "docs/cost-reconciliation.md",
        "specs/cost-actuals-manifest.json",
        "specs/cost-reconciliation-manifest.json",
    ):
        assert (first / relative).read_bytes() == (second / relative).read_bytes()


def test_report_rendering_is_deterministic() -> None:
    actuals, reconciliation = documents()
    assert emitter.render_reconciliation_report(
        actuals, reconciliation
    ) == emitter.render_reconciliation_report(actuals, reconciliation)


def test_render_never_mutates_its_inputs() -> None:
    actuals, reconciliation = documents()
    snapshot = (deepcopy(actuals), deepcopy(reconciliation))
    emitter.render_reconciliation_report(actuals, reconciliation)
    assert (actuals, reconciliation) == snapshot


def test_render_rejects_a_foreign_document() -> None:
    actuals, reconciliation = documents()
    with pytest.raises(emitter.EmissionValidationError):
        emitter.render_reconciliation_report(reconciliation, actuals)


# ---------------------------------------------------------------------------
# The report: four headlines, in order, each gated on its own evidence
# ---------------------------------------------------------------------------


HEADLINES = (
    "Projected monthly Azure cost",
    "Observed Azure spend",
    "Observed monthly run-rate",
    "Cost per successful interaction",
)


def test_report_keeps_four_headlines_separate_and_ordered(tmp_path) -> None:
    emit(tmp_path)
    report = report_text(tmp_path)
    positions = [report.index(heading) for heading in HEADLINES]
    assert positions == sorted(positions)
    assert len(set(positions)) == 4


def test_report_never_phrases_observed_spend_as_a_reprice(tmp_path) -> None:
    emit(tmp_path)
    report = report_text(tmp_path).casefold()
    assert "token reprice" not in report
    assert "actual billed cost" not in report
    assert "invoice" not in report


def test_mature_run_rate_and_unit_cost_are_reported_as_authoritative(
    tmp_path,
) -> None:
    emit(tmp_path)
    report = report_text(tmp_path)
    run_rate = section(report, "Observed monthly run-rate")
    unit_cost = section(report, "Cost per successful interaction")
    assert "$300.00" in run_rate
    assert "not-verified" not in run_rate
    assert "$0.7000" in unit_cost
    assert "not-verified" not in unit_cost


def test_immature_run_rate_is_not_verified_but_still_shows_the_observation(
    tmp_path,
) -> None:
    actuals, reconciliation = documents(interaction_counts=None)
    assert reconciliation["maturity"]["status"] == "not-verified"
    emit(tmp_path, actuals, reconciliation)
    run_rate = section(report_text(tmp_path), "Observed monthly run-rate")
    assert "not-verified" in run_rate
    assert "$300.00" in run_rate


def test_immature_unit_cost_is_not_verified(tmp_path) -> None:
    actuals, reconciliation = documents(interaction_counts=None)
    assert reconciliation["unit_economics"]["status"] == "not-verified"
    emit(tmp_path, actuals, reconciliation)
    unit_cost = section(report_text(tmp_path), "Cost per successful interaction")
    assert "not-verified" in unit_cost


def test_observed_spend_headline_always_carries_the_measured_total(
    tmp_path,
) -> None:
    actuals, reconciliation = documents(interaction_counts=None)
    emit(tmp_path, actuals, reconciliation)
    observed = section(report_text(tmp_path), "Observed Azure spend")
    assert "$70.00" in observed


def test_null_forecast_is_never_rendered_as_zero(tmp_path) -> None:
    actuals, reconciliation = documents(forecast_document={"price_basis": "retail"})
    assert reconciliation["totals"]["forecast_monthly_usd"] is None
    emit(tmp_path, actuals, reconciliation)
    projected = section(report_text(tmp_path), "Projected monthly Azure cost")
    assert "$0.00" not in projected
    assert emitter.NOT_MEASURED in projected


def test_a_genuine_zero_forecast_is_rendered_as_zero(tmp_path) -> None:
    actuals, reconciliation = documents(forecast_document=forecast(0.0))
    emit(tmp_path, actuals, reconciliation)
    projected = section(report_text(tmp_path), "Projected monthly Azure cost")
    assert "$0.00" in projected
    assert emitter.NOT_MEASURED not in projected


def test_report_carries_every_evidence_section(tmp_path) -> None:
    actuals, reconciliation = documents(
        token_series=[
            {
                "model": "gpt-4o",
                "deployment": "chat",
                "input_tokens": 1000,
                "output_tokens": 250,
            }
        ]
    )
    emit(tmp_path, actuals, reconciliation)
    report = report_text(tmp_path)
    for heading in (
        "Cost variance against the projection",
        "Maturity checks",
        "Unit economics",
        "Resource attribution",
        "Coverage",
        "Interaction and model evidence",
        "PAYG/PTU driver",
        "Declared policy",
        "Warnings",
        "Provenance",
    ):
        assert f"## {heading}" in report


def test_variance_section_states_the_threshold_it_was_judged_against(
    tmp_path,
) -> None:
    emit(tmp_path)
    variance = section(report_text(tmp_path), "Cost variance against the projection")
    assert "$0.00" in variance
    assert "0.00%" in variance
    assert "`pass`" in variance
    assert "20.00%" in variance


def test_maturity_section_lists_every_declared_check(tmp_path) -> None:
    actuals, reconciliation = documents()
    emit(tmp_path, actuals, reconciliation)
    maturity = section(report_text(tmp_path), "Maturity checks")
    for entry in reconciliation["maturity"]["checks"]:
        assert str(entry["id"]) in maturity


def test_unit_economics_section_reports_target_and_target_status(
    tmp_path,
) -> None:
    emit(tmp_path)
    unit = section(report_text(tmp_path), "Unit economics")
    assert "$1.0000" in unit
    assert "target" in unit.casefold()
    assert "`pass`" in unit


def test_attribution_section_names_matched_unmodeled_and_not_observed(
    tmp_path,
) -> None:
    unmodeled = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-pilot/providers/"
        "Microsoft.Storage/storageAccounts/unmodeled"
    )
    page = cost_page(70.0)
    page["properties"]["rows"].append(
        [
            20260802,
            "microsoft.storage/storageaccounts",
            5.0,
            "USD",
            unmodeled,
            "Storage",
        ]
    )
    instant = datetime(2026, 8, 10, tzinfo=timezone.utc)
    actuals = build_actuals_manifest(
        scope={"subscription_id": SUBSCRIPTION},
        start=WINDOW_START,
        end=WINDOW_END,
        generated_at=instant,
        cost_pages=[page],
        token_series=None,
        interaction_counts=(105, 100),
        provenance={"query_api_version": "2025-03-01"},
        warnings=[],
    )
    forecast_document = forecast()
    forecast_document["resources"].append(
        {
            "resource_id": (
                f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-pilot/"
                "providers/Microsoft.Search/searchServices/never-seen"
            ),
            "resource_kind": "Microsoft.Search/searchServices",
            "monthly_cost_usd": 90.0,
        }
    )
    reconciliation = reconcile_costs(
        forecast_document,
        actuals,
        policy(),
        policy_errors=[],
        generated_at=GENERATED,
        policy_spec_sha256=SPEC_SHA256,
    )
    emit(tmp_path, actuals, reconciliation)
    attribution = section(report_text(tmp_path), "Resource attribution")
    assert "unmodeled" in attribution
    assert "never-seen" in attribution
    assert "$5.00" in attribution


def test_coverage_section_keeps_both_measures_distinct_and_defined(
    tmp_path,
) -> None:
    emit(tmp_path)
    coverage = section(report_text(tmp_path), "Coverage")
    assert "projection_attribution_coverage_pct" in coverage
    assert "source_resource_id_coverage_pct" in coverage
    assert "mapped to a projected resource" in coverage
    assert "carrying a resource ID" in coverage


def test_usage_section_distinguishes_a_null_count_from_zero(tmp_path) -> None:
    actuals, reconciliation = documents(interaction_counts=None)
    emit(tmp_path, actuals, reconciliation)
    usage = section(report_text(tmp_path), "Interaction and model evidence")
    assert "`not-verified`" in usage
    assert emitter.NOT_MEASURED in usage
    assert "0" not in usage.replace("2026", "").replace("v1", "")


def test_usage_section_lists_observed_models(tmp_path) -> None:
    actuals, reconciliation = documents(
        token_series=[
            {
                "model": "gpt-4o",
                "deployment": "chat",
                "input_tokens": 1000,
                "output_tokens": 250,
            }
        ]
    )
    emit(tmp_path, actuals, reconciliation)
    usage = section(report_text(tmp_path), "Interaction and model evidence")
    assert "gpt-4o" in usage
    assert "chat" in usage
    assert "1,000" in usage


def test_policy_errors_are_rendered_verbatim_but_markdown_escaped(
    tmp_path,
) -> None:
    error = "cost.baseline | broken `code` <b>html</b> *emphasis* [link]"
    actuals, reconciliation = documents(policy_errors=[error])
    emit(tmp_path, actuals, reconciliation)
    report = report_text(tmp_path)
    policy_section = section(report, "Declared policy")
    assert "cost.baseline" in policy_section
    assert "broken" in policy_section
    assert "html" in policy_section
    assert "<b>" not in report
    assert "\\|" in policy_section
    assert "\\`code\\`" in policy_section


def test_warnings_from_both_documents_are_rendered(tmp_path) -> None:
    actuals, reconciliation = documents(
        warnings=["cost rows were collected in two pages"],
        forecast_document=forecast(0.0),
    )
    assert reconciliation["warnings"]
    emit(tmp_path, actuals, reconciliation)
    warnings = section(report_text(tmp_path), "Warnings")
    assert "two pages" in warnings
    assert "variance_pct" in warnings


def test_provenance_section_pins_every_reference(tmp_path) -> None:
    actuals, reconciliation = documents()
    emit(tmp_path, actuals, reconciliation)
    provenance = section(report_text(tmp_path), "Provenance")
    assert reconciliation["actuals_ref"]["sha256"] in provenance
    assert reconciliation["forecast_ref"]["sha256"] in provenance
    assert SPEC_SHA256 in provenance
    assert "2025-03-01" in provenance
    assert SUBSCRIPTION in provenance


def test_header_states_window_source_and_statuses(tmp_path) -> None:
    emit(tmp_path)
    report = report_text(tmp_path)
    assert report.startswith("# Cost reconciliation\n")
    header = report.split("## ", 1)[0]
    assert "2026-08-01" in header
    assert "2026-08-08" in header
    assert GENERATED in header
    assert "usage-pretax" in header
    assert "`pass`" in header


# ---------------------------------------------------------------------------
# Code spans survive backticks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "plain",
        "with `one` backtick pair",
        "``double``",
        "```triple```",
        "`leading",
        "trailing`",
        "`",
        "``",
        "",
        "   ",
    ],
)
def test_code_span_is_a_closed_span_for_any_backtick_content(value) -> None:
    """A code span cannot be escaped from the inside — a backslash is literal
    there — so a backtick in the content must be fenced by a LONGER run of
    backticks, not escaped. Otherwise the span closes early and the rest of
    the table row is parsed as Markdown."""
    rendered = emitter._code(value)
    fence = rendered[: len(rendered) - len(rendered.lstrip("`"))]
    assert fence, f"{rendered!r} is not a code span"
    assert rendered.endswith(fence)
    body = rendered[len(fence): len(rendered) - len(fence)]
    assert fence not in body
    assert "\\`" not in rendered
    for run in ("`" * length for length in range(1, 6)):
        if run in body:
            assert len(run) < len(fence)


@pytest.mark.parametrize("value", ["`a`", "a`b", "``", "", " "])
def test_code_span_content_is_recoverable(value) -> None:
    """Whatever the fence length, the original text is still there to read —
    padded by at most one space on each side, per CommonMark's stripping
    rule."""
    rendered = emitter._code(value)
    fence = rendered[: len(rendered) - len(rendered.lstrip("`"))]
    body = rendered[len(fence): len(rendered) - len(fence)]
    assert value.strip() in body


def test_pipes_inside_a_code_span_are_still_escaped_for_the_table() -> None:
    """GFM resolves `\\|` before inline parsing, so an unescaped pipe breaks
    the cell even inside a code span."""
    assert "\\|" in emitter._code("a|b")


def test_backticked_provenance_key_cannot_break_out_of_its_cell(
    tmp_path,
) -> None:
    actuals, reconciliation = documents(
        provenance={
            "query_api_version": "2025-03-01",
            "``odd`key``": "value",
        }
    )
    emit(tmp_path, actuals, reconciliation)
    provenance = section(report_text(tmp_path), "Provenance")
    row = next(
        line for line in provenance.splitlines() if "odd" in line
    )
    assert row.count("|") == 3
    assert "\\`" not in row
