"""CLI tests for `estimate --from-profile` — the stable no-discovery path."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import consumption_iq  # noqa: E402


def _profile_file(tmp_path: Path) -> Path:
    profile = {
        "generated_at": "2026-06-12T12:00:00+00:00",
        "transaction_unit": "document",
        "monthly_transactions": 10000,
        "load_profile": {
            "workload_class": "batch",
            "business_hours_only": False,
            "pages_per_month": 10000,
            "embedding_tokens_per_month": 5_000_000,
        },
        "resources": [],
        "selectors": {
            "content_understanding": {"enabled": True, "tier": "standard"},
            "embeddings": {"enabled": True, "model": "text-embedding-3-small"},
        },
    }
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(profile), encoding="utf-8")
    return p


def test_from_profile_skips_discover_resources(tmp_path):
    profile = _profile_file(tmp_path)
    manifest_path = tmp_path / "cost-manifest.json"
    report_path = tmp_path / "cost.md"
    cache = tmp_path / "cache.json"

    with patch("consumption_iq.discover_resources") as mock_discover:
        rc = consumption_iq.main(
            [
                "estimate",
                "--from-profile",
                str(profile),
                "--manifest",
                str(manifest_path),
                "--report",
                str(report_path),
                "--cache",
                str(cache),
            ]
        )

    assert rc == 0
    mock_discover.assert_not_called()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == "2.0"
    assert manifest["transaction_unit"] == "document"
    # deterministic: pinned generated_at flowed through
    assert manifest["generated_at"] == "2026-06-12T12:00:00+00:00"


def test_from_profile_writes_report(tmp_path):
    profile = _profile_file(tmp_path)
    manifest_path = tmp_path / "cost-manifest.json"
    report_path = tmp_path / "cost.md"
    rc = consumption_iq.main(
        [
            "estimate",
            "--from-profile",
            str(profile),
            "--manifest",
            str(manifest_path),
            "--report",
            str(report_path),
            "--cache",
            str(tmp_path / "cache.json"),
        ]
    )
    assert rc == 0
    assert report_path.exists()
    assert "no discovery" in report_path.read_text().lower()


def test_estimate_requires_rollout_or_profile(tmp_path, capsys):
    rc = consumption_iq.main(
        [
            "estimate",
            "--cache",
            str(tmp_path / "cache.json"),
            "--manifest",
            str(tmp_path / "m.json"),
            "--report",
            str(tmp_path / "r.md"),
        ]
    )
    assert rc == 2
