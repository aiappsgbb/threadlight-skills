"""
Tests for scripts/onepager.py — the shareable seller one-pager.

The post-deploy skill emits `cost-projection.md` (an engineering scorecard) and
`cost-manifest.json` (a CI gate input). Neither is something a seller forwards
to a sales peer to *start a conversation*. The one-pager is: a self-contained
HTML page (best-effort PDF) that frames every figure as an estimate, carries an
internal-vs-customer classification, and tells the seller how to open the
discussion.

Discipline under test:
  * EVERY cost is framed as an estimate (the word appears; no bare "$X/mo" sold
    as fact).
  * audience=internal -> classification strip + seller talk-track present.
  * audience=customer -> NO internal classification, NO seller talk-track.
  * discount caveat surfaces whenever a discount was applied.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from onepager import render_onepager, write_onepager  # noqa: E402


def _phased_manifest():
    return {
        "schema_version": "1.1",
        "pre_sales": True,
        "generated_at": "2026-06-22T12:00:00+00:00",
        "currency": "USD",
        "customer": "Generic Pilot",
        "price_basis": "ea",
        "discount": {
            "basis": "ea",
            "multiplier": 0.85,
            "applied": True,
            "caveats": ["Discounted figures apply a flat 15% EA multiplier — an ESTIMATE, not a quote."],
        },
        "benchmark": {"metric": "queries_per_day", "value": 5000},
        "current_phase": "expansion",
        "phases": [
            {
                "id": "poc",
                "label": "Phase 1 - Proof of concept",
                "posture": "demo",
                "totals": {
                    "monthly_cost_current_usd": 420.0,
                    "monthly_cost_current_discounted_usd": 357.0,
                },
            },
            {
                "id": "expansion",
                "label": "Phase 2 - Expansion",
                "posture": "production",
                "totals": {
                    "monthly_cost_current_usd": 1850.0,
                    "monthly_cost_current_discounted_usd": 1572.5,
                },
            },
            {
                "id": "business-wide",
                "label": "Phase 3 - Business-wide",
                "posture": "production-hardened",
                "totals": {
                    "monthly_cost_current_usd": 9200.0,
                    "monthly_cost_current_discounted_usd": 7820.0,
                },
            },
        ],
        "totals": {
            "monthly_cost_current_usd": 1850.0,
            "monthly_cost_current_discounted_usd": 1572.5,
        },
    }


# ---------------------------------------------------------------------------
# render_onepager
# ---------------------------------------------------------------------------

def test_render_is_html_document():
    html = render_onepager(_phased_manifest(), audience="internal")
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "<html" in html and "</html>" in html


def test_every_phase_is_rendered():
    html = render_onepager(_phased_manifest(), audience="internal")
    for label in ("Proof of concept", "Expansion", "Business-wide"):
        assert label in html


def test_costs_are_framed_as_estimates():
    html = render_onepager(_phased_manifest(), audience="internal").lower()
    assert "estimate" in html
    # The headline numbers appear.
    assert "1,850" in render_onepager(_phased_manifest(), audience="internal")


def test_internal_audience_has_classification_and_talktrack():
    html = render_onepager(_phased_manifest(), audience="internal")
    low = html.lower()
    assert "internal" in low
    assert "do not share" in low or "not for the customer" in low
    # Seller talk-track only for internal.
    assert "talk track" in low or "how to open" in low


def test_customer_audience_omits_internal_material():
    html = render_onepager(_phased_manifest(), audience="customer")
    low = html.lower()
    assert "do not share" not in low
    assert "talk track" not in low
    assert "how to open" not in low
    # Still an estimate-framed document.
    assert "estimate" in low


def test_discount_caveat_surfaces_when_applied():
    html = render_onepager(_phased_manifest(), audience="internal").lower()
    assert "not a quote" in html or "ea multiplier" in html


def test_no_discount_section_when_not_applied():
    manifest = _phased_manifest()
    manifest["discount"] = {"basis": "retail", "multiplier": 1.0, "applied": False, "caveats": []}
    manifest["price_basis"] = "retail"
    html = render_onepager(manifest, audience="internal")
    assert "discounted" not in html.lower()


def test_html_escapes_customer_name():
    manifest = _phased_manifest()
    manifest["customer"] = "Acme <script>alert(1)</script>"
    html = render_onepager(manifest, audience="internal")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# write_onepager
# ---------------------------------------------------------------------------

def test_write_onepager_writes_html(tmp_path):
    out_path = tmp_path / "onepager.html"
    result = write_onepager(_phased_manifest(), out_path, audience="internal", pdf=False)
    assert out_path.exists()
    assert result["html_path"] == str(out_path)
    assert result["pdf_path"] is None
    assert out_path.read_text().lower().startswith("<!doctype html>")


def test_write_onepager_pdf_best_effort_never_raises(tmp_path):
    """pdf=True must succeed-or-skip, never raise, even with no Chromium."""
    out_path = tmp_path / "onepager.html"
    result = write_onepager(_phased_manifest(), out_path, audience="internal", pdf=True)
    assert out_path.exists()
    # Either a PDF was produced, or a friendly skip reason was recorded.
    assert (result["pdf_path"] is not None) or (result["pdf_skipped_reason"])


def _manifest_with_shared_hardening():
    m = _phased_manifest()
    bw = next(p for p in m["phases"] if p["id"] == "business-wide")
    bw["totals"]["monthly_cost_hardening_shared_usd"] = 3644.0
    return m


def test_onepager_surfaces_estate_billed_shared_platform():
    """The one-pager is the artefact a seller forwards. When a phase total
    silently contains estate-shared platform (Defender/Sentinel/DDoS), the
    one-pager must say so — otherwise it overstates the workload's marginal
    cost with no annotation."""
    html = render_onepager(_manifest_with_shared_hardening(), audience="internal").lower()
    assert "estate" in html or "shared platform" in html
    assert "3,644" in render_onepager(_manifest_with_shared_hardening(), audience="internal")
