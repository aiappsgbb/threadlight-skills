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


def _extract_runtime(dest: Path) -> Path:
    """Extract the vendored ``cost-runtime.zip`` from the committed outer bundle
    and return the directory the importable cost engine lives in."""
    bundle = _extract(dest)
    runtime = dest / "runtime"
    with zipfile.ZipFile(bundle / "vendor" / "cost-runtime.zip") as rt:
        rt.extractall(runtime)
    return runtime


def _run_inner_cost_api(runtime_dir: Path, body: str) -> subprocess.CompletedProcess:
    """Import ``cost_api`` from the extracted runtime dir in a fresh interpreter.

    Running the check in a subprocess whose cwd is the extracted runtime dir
    (and with an empty PYTHONPATH) guarantees the assertions exercise the cost
    engine that shipped INSIDE the committed ``cost-runtime.zip`` — never the
    in-repo ``threadlight-consumption-iq/scripts`` copy — so the test is a true
    inspection of the published artifact.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    script = "import cost_api\n" + body + "\nprint('INNER_OK')\n"
    return subprocess.run(  # noqa: S603 - fixed, trusted argv
        [sys.executable, "-c", script],
        cwd=runtime_dir,
        env=env,
        capture_output=True,
        text=True,
    )


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


def test_inner_cost_api_fails_closed_on_advisory_fallback(tmp_path):
    """The committed inner ``cost_api`` must carry the fail-closed fallback
    contract: a resource priced only from a region-blind ``price_source =
    'fallback'`` constant is advisory, so the manifest goes ``partial`` with
    ``totals.complete = False`` and NO per-transaction cost. A stale bundle
    (pre-rebase inner engine) treats the fallback figure as a priced/known line
    and reports ``complete`` — this test fails against that stale artifact.
    """
    runtime = _extract_runtime(tmp_path)
    body = (
        "m = cost_api.build_cost_manifest(\n"
        "    resources=[{'kind': 'Microsoft.App/containerApps', 'name': 'aca',\n"
        "                'monthly_cost_usd': 123.45, 'price_source': 'fallback'}],\n"
        "    meters=[], load_profile={}, transaction_unit='interaction',\n"
        "    monthly_transactions=1000, pricing=None)\n"
        "assert m['status'] == 'partial', m['status']\n"
        "assert m['totals']['complete'] is False, m['totals']\n"
        "assert m['totals']['cost_per_transaction_usd'] is None, m['totals']\n"
        "line = m['resources'][0]\n"
        "assert line['pricing_status'] == 'not-priceable', line\n"
        "assert line['verified'] is False, line\n"
        "assert isinstance(line.get('reason'), str) and line['reason'].strip(), line\n"
        "assert m['meter_coverage']['status'] != 'complete', m['meter_coverage']\n"
    )
    proc = _run_inner_cost_api(runtime, body)
    assert proc.returncode == 0 and "INNER_OK" in proc.stdout, (
        "committed cost-runtime.zip does not fail closed on advisory fallback "
        f"(rebuild scripts/build-cowork-zips.sh):\nstdout={proc.stdout!r}\n"
        f"stderr={proc.stderr!r}"
    )


def test_inner_cost_api_rejects_fractional_ptu_units(tmp_path):
    """The committed inner ``cost_api`` must reject a fractional ``ptu_units``
    (PTU capacity is provisioned in whole units). An integral float normalises
    to an int; a fractional value raises ``ValueError`` before any output. A
    stale bundle preserved the fractional value instead — this test fails
    against that stale artifact.
    """
    runtime = _extract_runtime(tmp_path)
    body = (
        "class _P:\n"
        "    def get_price(self, *a, **k):\n"
        "        return {'unit_price_usd': 1.0}\n"
        "raised = False\n"
        "try:\n"
        "    cost_api.build_ptu_scenarios({'ptu_units': 2.5}, _P())\n"
        "except ValueError:\n"
        "    raised = True\n"
        "assert raised, 'fractional ptu_units was not rejected'\n"
        "# An integral float still normalises to a whole-unit int.\n"
        "s = cost_api.build_ptu_scenarios({'ptu_units': 2.0}, _P())\n"
        "assert s is not None and s['ptu_units'] == 2 and isinstance(s['ptu_units'], int), s\n"
    )
    proc = _run_inner_cost_api(runtime, body)
    assert proc.returncode == 0 and "INNER_OK" in proc.stdout, (
        "committed cost-runtime.zip does not reject fractional ptu_units "
        f"(rebuild scripts/build-cowork-zips.sh):\nstdout={proc.stdout!r}\n"
        f"stderr={proc.stderr!r}"
    )


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
