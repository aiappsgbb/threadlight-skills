from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

TEST_DIR = Path(__file__).resolve().parent
SCRIPT = TEST_DIR.parent / "scripts" / "production_ready.py"

sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402


def _write_postdeploy(path: Path, *, checked_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "checked_at": checked_at,
            "phase": "post-deploy",
            "gaps": [],
        }),
        encoding="utf-8",
    )


def test_load_postdeploy_accepts_lowercase_z_and_offsets(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)

    lower_z = tmp_path / "lower-z.json"
    _write_postdeploy(lower_z, checked_at=(now - timedelta(hours=1)).isoformat().replace("+00:00", "z"))
    data, warnings = pr._load_postdeploy(lower_z, False, 24)
    assert data["phase"] == "post-deploy"
    assert warnings == []

    offset = tmp_path / "offset.json"
    _write_postdeploy(offset, checked_at=(now - timedelta(hours=1)).isoformat())
    data, warnings = pr._load_postdeploy(offset, False, 24)
    assert data["phase"] == "post-deploy"
    assert warnings == []


def test_load_postdeploy_rejects_exact_24_hour_boundary(tmp_path):
    manifest = tmp_path / "boundary.json"
    checked_at = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _write_postdeploy(manifest, checked_at=checked_at)

    try:
        pr._load_postdeploy(manifest, False, 24)
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected exact-24h postdeploy manifest to be rejected as stale")


def test_load_postdeploy_rejects_future_checked_at(tmp_path):
    manifest = tmp_path / "future.json"
    checked_at = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat()
    _write_postdeploy(manifest, checked_at=checked_at)

    try:
        pr._load_postdeploy(manifest, False, 24)
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected future postdeploy manifest to be rejected")


def test_load_postdeploy_rejects_missing_or_malformed_checked_at_without_override(tmp_path):
    missing = tmp_path / "missing.json"
    _write_postdeploy(missing, checked_at="2026-08-06T08:00:00Z")
    data = json.loads(missing.read_text(encoding="utf-8"))
    del data["checked_at"]
    missing.write_text(json.dumps(data), encoding="utf-8")

    malformed = tmp_path / "malformed.json"
    _write_postdeploy(malformed, checked_at="2026-08-06 08:00:00")

    for manifest in (missing, malformed):
        try:
            pr._load_postdeploy(manifest, False, 24)
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"expected {manifest.name} to be rejected without --accept-stale-safe-check")


def test_load_postdeploy_rejects_unresolved_gaps(tmp_path):
    manifest = tmp_path / "gaps.json"
    _write_postdeploy(
        manifest,
        checked_at=(datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat(),
    )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["gaps"] = [{"id": "missing-proof"}]
    manifest.write_text(json.dumps(data), encoding="utf-8")

    try:
        pr._load_postdeploy(manifest, False, 24)
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected postdeploy manifest with unresolved gaps to be rejected")


def test_validate_manifest_binding_rejects_missing_embedded_snapshot_without_override() -> None:
    manifest = {
        "deployment_manifest": {
            "subscription_id": "sub-1",
            "resource_group": "rg-1",
        }
    }
    postdeploy = {
        "phase": "post-deploy",
        "checked_at": "2026-08-06T08:00:00Z",
        "gaps": [],
    }

    try:
        pr._validate_manifest_binding(manifest, postdeploy, False)
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected missing embedded deployment snapshot to fail binding validation")


def test_validate_manifest_binding_rejects_missing_embedded_snapshot_even_with_accept_stale() -> None:
    manifest = {
        "deployment_manifest": {
            "subscription_id": "sub-1",
            "resource_group": "rg-1",
        }
    }
    postdeploy = {
        "phase": "post-deploy",
        "checked_at": "2026-08-06T08:00:00Z",
        "gaps": [],
    }

    try:
        pr._validate_manifest_binding(manifest, postdeploy, True)
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected missing embedded deployment snapshot to fail even with --accept-stale-safe-check")


def test_validate_manifest_binding_rejects_cross_deployment_drift_even_with_accept_stale() -> None:
    manifest = {
        "deployment_manifest": {
            "subscription_id": "sub-1",
            "resource_group": "rg-1",
        }
    }
    postdeploy = {
        "phase": "post-deploy",
        "checked_at": "2026-08-06T08:00:00Z",
        "gaps": [],
        "deployment_manifest": {
            "subscription_id": "sub-2",
            "resource_group": "rg-2",
        },
    }

    try:
        pr._validate_manifest_binding(manifest, postdeploy, True)
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected cross-deployment drift to fail even with --accept-stale-safe-check")
