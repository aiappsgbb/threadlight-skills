"""
Shareable seller one-pager (`onepager.py`).

The post-deploy skill emits `cost-projection.md` (engineering scorecard) and
`cost-manifest.json` (CI gate input). Neither is something a seller forwards to
a peer to *start a conversation*. This module renders a self-contained HTML
one-pager (best-effort PDF) from a pre-sales phased manifest.

Discipline baked in:
  * Every figure is framed as a planning ESTIMATE (banner + per-row tag).
  * audience=internal -> red classification strip + seller talk-track.
  * audience=customer -> neither (a customer-safe artefact).
  * The discount caveat surfaces whenever a discount was applied.

Stdlib only for HTML. PDF is opt-in and best-effort: if Playwright/Chromium is
present we render one, otherwise we record a friendly skip reason and never
raise — PDF is never a hard dependency.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "references" / "onepager-template.html"

_CLASSIFICATION_TEXT = "Microsoft internal · sales enablement · do not share with the customer"

_BANNER = (
    "All figures on this page are planning <strong>estimates</strong> at public "
    "list prices for a single generic pilot — not a quote. They exist to frame a "
    "conversation, not to commit a number."
)

_TALKTRACK_ITEMS = (
    "Lead with Phase 1 (proof of concept) — the smallest, cheapest commitment.",
    "Never quote a single Phase 3 number in isolation; show the phased ramp so the step-change is expected.",
    "Call out that production-hardening (private networking, SecOps, DR, non-prod) is a deliberate posture choice, not a surprise.",
    "Qualify any EA figure before sharing — it is your assumption, not a contractual rate.",
)


def render_onepager(manifest: dict[str, Any], audience: str = "internal") -> str:
    """Render the one-pager HTML for `audience` ∈ {internal, customer}."""
    template = _TEMPLATE_PATH.read_text()
    internal = audience != "customer"
    customer = html.escape(str(manifest.get("customer", "Generic Pilot")))
    discount = manifest.get("discount") or {}
    applied = bool(discount.get("applied"))

    title = "Azure consumption — phased estimate"
    if internal:
        title += " (internal)"

    subtitle = f"{customer} · pre-sales cost framing across adoption phases"
    generated = (
        f"Generated {html.escape(str(manifest.get('generated_at', 'n/a')))} · "
        f"currency {html.escape(str(manifest.get('currency', 'USD')))} · "
        f"price basis {html.escape(str(manifest.get('price_basis', 'retail')))}"
    )

    body = _render_phase_table(manifest, applied)
    if applied:
        body += _render_discount_caveat(discount)
    body += _render_benchmark(manifest)

    classification = (
        f'<div class="classification">{html.escape(_CLASSIFICATION_TEXT)}</div>'
        if internal
        else ""
    )
    talktrack = _render_talktrack() if internal else ""
    footer = (
        "Threadlight Consumption IQ · pre-sales phased estimate. Estimates only; "
        "validate against the Azure Pricing Calculator and the customer's agreement "
        "before sharing externally."
    )

    return (
        template
        .replace("{{TITLE}}", html.escape(title))
        .replace("{{SUBTITLE}}", subtitle)
        .replace("{{GENERATED}}", generated)
        .replace("{{BANNER}}", _BANNER)
        .replace("{{CLASSIFICATION}}", classification)
        .replace("{{BODY}}", body)
        .replace("{{TALKTRACK}}", talktrack)
        .replace("{{FOOTER}}", html.escape(footer))
    )


def write_onepager(
    manifest: dict[str, Any],
    path: str | Path,
    audience: str = "internal",
    pdf: bool = False,
) -> dict[str, Any]:
    """Write the HTML one-pager; optionally render a best-effort PDF alongside.

    Returns {html_path, pdf_path, pdf_skipped_reason}. Never raises for PDF.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    page = render_onepager(manifest, audience=audience)
    path.write_text(page)

    result: dict[str, Any] = {
        "html_path": str(path),
        "pdf_path": None,
        "pdf_skipped_reason": None,
    }
    if not pdf:
        return result

    pdf_path = path.with_suffix(".pdf")
    try:
        _render_pdf(path, pdf_path)
        result["pdf_path"] = str(pdf_path)
    except Exception as exc:  # noqa: BLE001 — PDF is best-effort by contract
        result["pdf_skipped_reason"] = (
            f"PDF not rendered ({type(exc).__name__}: {exc}). HTML is the "
            "authoritative artefact; install Playwright + Chromium to enable PDF."
        )
    return result


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_phase_table(manifest: dict[str, Any], applied: bool) -> str:
    phases = manifest.get("phases") or []
    current_phase = manifest.get("current_phase")
    header = ["<h2>Cost by adoption phase</h2>", "<table>", "<tr>",
             "<th>Phase</th><th>Posture</th><th>Est. monthly cost</th>"]
    if applied:
        header.append("<th>EA est.</th>")
    header.append("</tr>")
    rows = ["".join(header)]
    for ph in phases:
        totals = ph.get("totals") or {}
        current = totals.get("monthly_cost_current_usd", 0.0) or 0.0
        marker = " <span class=\"pill\">current</span>" if ph.get("id") == current_phase else ""
        cells = [
            f"<td>{html.escape(str(ph.get('label', ph.get('id', '?'))))}{marker}</td>",
            f"<td>{html.escape(str(ph.get('posture', '?')))}</td>",
            f"<td class=\"num\">${current:,.0f} <span class=\"est\">(estimate)</span></td>",
        ]
        if applied:
            disc = totals.get("monthly_cost_current_discounted_usd")
            disc_cell = f"${disc:,.0f}" if disc is not None else "—"
            cells.append(f"<td class=\"num\">{disc_cell}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    rows.append("</table>")
    shared_notes = [
        (ph.get("label", ph.get("id", "?")),
         float((ph.get("totals") or {}).get("monthly_cost_hardening_shared_usd") or 0.0))
        for ph in phases
    ]
    shared_notes = [(lbl, amt) for lbl, amt in shared_notes if amt > 0]
    if shared_notes:
        parts = "; ".join(
            f"{html.escape(str(lbl))} ${amt:,.0f}/mo" for lbl, amt in shared_notes
        )
        rows.append(
            "<p class=\"note\"><strong>Includes shared platform billed "
            "estate-wide</strong> (e.g. Defender, Sentinel, DDoS) — amortised "
            "across the whole estate, not charged wholly to this workload, so "
            f"treat the phase totals as an upper bound: {parts}.</p>"
        )
    return "\n".join(rows)


def _render_discount_caveat(discount: dict[str, Any]) -> str:
    caveats = discount.get("caveats") or []
    text = " ".join(html.escape(str(c)) for c in caveats) or (
        "Discounted figures are an internal EA assumption — not a quote."
    )
    return f'<div class="caveat">{text}</div>'


def _render_benchmark(manifest: dict[str, Any]) -> str:
    bench = manifest.get("benchmark")
    if not bench:
        return ""
    metric = html.escape(str(bench.get("metric", "benchmark")))
    value = bench.get("value")
    value_str = f"{value:,}" if isinstance(value, (int, float)) else html.escape(str(value))
    return (
        f'<p class="meta">Anchored to the customer benchmark '
        f"<strong>{metric} = {value_str}</strong> (estimate basis).</p>"
    )


def _render_talktrack() -> str:
    items = "".join(f"<li>{html.escape(i)}</li>" for i in _TALKTRACK_ITEMS)
    return (
        '<div class="talktrack"><h2>For the seller — how to open the conversation</h2>'
        f"<ul>{items}</ul></div>"
    )


def _render_pdf(html_path: Path, pdf_path: Path) -> None:
    """Render HTML -> PDF via Playwright Chromium. Raises if unavailable."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri())
        page.emulate_media(media="print")
        page.pdf(path=str(pdf_path), format="A4", print_background=True,
                 margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
        browser.close()
