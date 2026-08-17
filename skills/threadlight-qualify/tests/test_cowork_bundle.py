"""End-to-end test of the BUILT + extracted Cowork qualify bundle.

Runs ``scripts/build-cowork-zips.sh``'s output (``docs/downloads/threadlight-
qualify.zip``) from a directory OUTSIDE the repo tree, with an empty
``PYTHONPATH``, so ``qualify.py`` can only resolve the shared cost engine from
the vendored ``cost-runtime.zip`` — never the in-repo ``threadlight-consumption-
iq/scripts``. This proves the offline dated pricing fixtures ride along inside
the runtime zip (the packaged ``pricing_fixtures`` resource) and that the sample
profile prices to a complete bill with a per-transaction cost and PTU scenarios.

The test consumes the built artifact rather than rebuilding it, so running the
suite never mutates the committed ``docs/downloads/`` zips. Re-run
``bash scripts/build-cowork-zips.sh`` first if the runtime source changed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ZIP = REPO_ROOT / "docs" / "downloads" / "threadlight-qualify.zip"
PROFILE = (
    REPO_ROOT
    / "skills"
    / "threadlight-qualify"
    / "references"
    / "fixtures"
    / "sample-qualification"
    / "profile.json"
)
PINNED = "2026-06-12T12:00:00+00:00"

EXPECTED_OUTER_MEMBERS = [
    "SKILL.md",
    "references/citadel-sizing.json",
    "references/sizing-manifest.schema.json",
    "scripts/qualify.py",
    "vendor/cost-runtime.zip",
    "vendor/model-catalog.json",
]

pytestmark = pytest.mark.skipif(
    not ZIP.exists(),
    reason="run scripts/build-cowork-zips.sh first to produce the Cowork bundle",
)


def _extract(dest: Path) -> Path:
    bundle = dest / "bundle"
    with zipfile.ZipFile(ZIP) as archive:
        archive.extractall(bundle)
    return bundle


def test_outer_archive_contract_is_six_members_five_companions(tmp_path):
    bundle = _extract(tmp_path)
    members = sorted(
        p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()
    )
    assert members == EXPECTED_OUTER_MEMBERS
    companions = [m for m in members if m != "SKILL.md"]
    assert len(companions) == 5


def test_runtime_zip_ships_packaged_pricing_fixtures(tmp_path):
    bundle = _extract(tmp_path)
    with zipfile.ZipFile(bundle / "vendor" / "cost-runtime.zip") as rt:
        names = rt.namelist()
    assert "pricing_fixtures/__init__.py" in names
    # The dated rates PricingClient needs offline: meter rates + AOAI SKU/PTU.
    assert "pricing_fixtures/meters.json" in names
    assert "pricing_fixtures/microsoft-cognitiveservices-accounts-deployments.json" in names


def test_extracted_bundle_prices_offline_without_repo_pythonpath(tmp_path):
    bundle = _extract(tmp_path)
    out = tmp_path / "out"

    # Scrub the repo from the child's import path: an empty PYTHONPATH and a cwd
    # that is NOT inside the repo skills tree, so the only importable cost engine
    # is the vendored cost-runtime.zip. Also clear the offline env flag to prove
    # qualification is offline by construction (offline=True in _make_pricing).
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    env.pop("THREADLIGHT_PRICING_OFFLINE", None)

    proc = subprocess.run(  # noqa: S603 - fixed, trusted argv
        [
            sys.executable,
            str(bundle / "scripts" / "qualify.py"),
            "--profile",
            str(PROFILE),
            "--output-dir",
            str(out),
            "--generated-at",
            PINNED,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    manifest = json.loads(
        (out / "qualification" / "sizing-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "complete", manifest["status"]

    apps = [s for s in manifest["sizings"] if s["kind"] == "threadlight-application"]
    assert {s["stage"] for s in apps} == {"mvp", "production"}
    for sizing in apps:
        cm = sizing["cost_manifest"]
        totals = cm["totals"]
        assert totals["complete"] is True, sizing["stage"]
        assert totals["cost_per_transaction_usd"] is not None, sizing["stage"]
        assert totals["monthly_cost_current_usd"] is not None, sizing["stage"]
        assert cm["meter_coverage"]["not_priceable"] == 0, sizing["stage"]

        # PTU scenarios are derived from the packaged AOAI PTU rate — present only
        # because the fixture rode along inside the runtime zip.
        ptu = cm.get("ptu_scenarios")
        assert ptu is not None, f"{sizing['stage']} missing ptu_scenarios"
        assert [s["commitment"] for s in ptu["scenarios"]] == [
            "hourly",
            "one-month",
            "one-year",
        ]
