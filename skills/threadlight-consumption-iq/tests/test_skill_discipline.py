"""
Discipline test for SKILL.md — pre-sales estimate-framing + classification.

A pre-sales cost number is dangerous precisely because it looks authoritative.
The skill therefore has to teach two non-negotiable disciplines, and this test
asserts they are *documented* (and survive future edits):

  1. ESTIMATE-FRAMING — every figure is a planning estimate at public list
     prices, never a quote. The skill must carry the rationalization table +
     red-flags construct (the writing-skills bulletproofing pattern) so an
     agent under "just give them a number" pressure still frames it correctly.

  2. CLASSIFICATION — internal seller enablement vs customer-safe output. The
     internal one-pager carries a "do not share" strip + seller talk-track; the
     customer one omits both. The skill must say so.

It also pins the version bump + the pre-sales discovery triggers so the
description actually routes pre-sales questions here.
"""
from __future__ import annotations

from pathlib import Path

SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"


def _text() -> str:
    return SKILL_MD.read_text()


def _lower() -> str:
    return _text().lower()


# ---------------------------------------------------------------------------
# Version + routing
# ---------------------------------------------------------------------------

def test_version_bumped_to_0_3_0():
    assert 'version: "0.3.0"' in _text()


def test_description_carries_presales_triggers():
    low = _lower()
    for trigger in ("pre-sales", "phased estimate", "rollout profile", "one-pager"):
        assert trigger in low, f"missing pre-sales trigger: {trigger!r}"


def test_ea_mca_discount_no_longer_out_of_scope():
    """EA/MCA discount + phased estimate are now IN scope — drop the v1 exclusions."""
    low = _lower()
    assert "ea / mca discount modelling (out of scope" not in low
    assert "deferred to v2" not in low or "reservations" in low  # reservations may still be v2


# ---------------------------------------------------------------------------
# Pre-sales section
# ---------------------------------------------------------------------------

def test_has_presales_mode_section():
    low = _lower()
    assert "pre-sales phased estimate" in low


def test_presales_section_names_the_artefacts():
    low = _lower()
    for artefact in ("cost-estimate.md", "cost-estimate-manifest.json", "rollout"):
        assert artefact in low, f"pre-sales section must name {artefact!r}"


# ---------------------------------------------------------------------------
# Estimate-framing discipline
# ---------------------------------------------------------------------------

def test_estimate_framing_is_explicit():
    low = _lower()
    assert "estimate" in low
    assert "not a quote" in low


def test_has_rationalization_table():
    """The bulletproofing pattern: a table mapping excuses -> reality."""
    low = _lower()
    assert "rationaliz" in low or "excuse" in low
    # A red-flags self-check construct.
    assert "red flag" in low


def test_red_flags_cover_quote_and_classification_leaks():
    low = _lower()
    # The two specific failure modes a pre-sales agent will hit.
    assert "quote" in low
    assert "do not share" in low or "not for the customer" in low


# ---------------------------------------------------------------------------
# Classification discipline
# ---------------------------------------------------------------------------

def test_classification_internal_vs_customer():
    low = _lower()
    assert "internal" in low and "customer" in low
    assert "talk track" in low or "how to open" in low
