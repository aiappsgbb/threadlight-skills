#!/usr/bin/env python3
"""Render the threadlight-router-bench learnings digest (single-run cold-path).

`build_digest` assembles the structured `threadlight-router-learnings/v1` object
from harvested signals; `render_markdown` turns it into the human + LLM-readable
brief that the SKILL.md recommendations turn reasons over. The LLM never sees raw
logs — only this digest — so learnings stay grounded and reproducible.
"""
from __future__ import annotations

from typing import Any

SCHEMA = "threadlight-router-learnings/v1"
_SEV_RANK = {"high": 0, "medium": 1, "low": 2}


def build_digest(run_id: int, conclusion: str, branch: str, title: str,
                 window: dict[str, str], phase_parity: dict[str, str],
                 legs: dict[str, Any], findings: list[dict[str, Any]],
                 model_deployment: str = "") -> dict[str, Any]:
    """Assemble the learnings/v1 digest from harvested single-run signals."""
    summary = {"high": 0, "medium": 0, "low": 0, "total": len(findings)}
    for f in findings:
        sev = f.get("severity", "low")
        if sev in summary:
            summary[sev] += 1
    ordered = sorted(findings, key=lambda f: (_SEV_RANK.get(f.get("severity"), 3),
                                              f.get("id", "")))
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "conclusion": conclusion,
        "branch": branch,
        "title": title,
        "model_deployment": model_deployment,
        "window": window,
        "phase_parity": phase_parity,
        "legs": legs,
        "findings": ordered,
        "summary": summary,
    }


def render_markdown(digest: dict[str, Any]) -> str:
    """Render the digest as a Markdown brief."""
    run_id = digest["run_id"]
    out: list[str] = [f"# Router-bench learnings — run {run_id}", ""]
    dep = digest.get("model_deployment") or "(unknown)"
    out += [
        f"- **Conclusion:** {digest['conclusion']}",
        f"- **Branch:** {digest['branch']}  •  **Title:** {digest['title']}",
        f"- **Model deployment:** {dep}",
        f"- **Window:** {digest['window'].get('start')} → {digest['window'].get('end')}",
        "",
    ]

    # Phase parity
    parity = digest.get("phase_parity") or {}
    if parity:
        out.append("## Phase parity")
        for phase, concl in parity.items():
            mark = "✅" if concl in ("success", "skipped") else "❌"
            out.append(f"- {mark} `{phase}` — {concl}")
        out.append("")

    # Findings
    s = digest["summary"]
    out.append("## Findings")
    out.append(f"_high={s['high']} · medium={s['medium']} · low={s['low']} · total={s['total']}_")
    out.append("")
    if not digest["findings"] or s["high"] == 0 and digest["conclusion"] == "success":
        out.append("No high-severity findings — run looks clean.")
        out.append("")
    for f in digest["findings"]:
        ev = f.get("evidence", {})
        excerpt = (ev.get("excerpt") or "").replace("`", "'")
        out.append(f"- **[{f['severity']}] {f['category']}** ({f['id']}, ×{f.get('count', 1)}, "
                   f"phase={f.get('phase', '?')})")
        if excerpt:
            out.append(f"  > `{excerpt}`")
    out.append("")

    # Phase-5 legs
    legs = digest.get("legs") or {}
    wired = {k: v for k, v in legs.items() if v}
    if wired:
        out.append("## Phase-5 KPI legs")
        for leg, manifest in wired.items():
            verdict = manifest.get("verdict") or manifest.get("schema") or "recorded"
            out.append(f"- `{leg}` — {verdict}")
        out.append("")

    return "\n".join(out)


def render_validation_matrix(cards: list[dict[str, Any]]) -> str:
    """Render a markdown matrix (workload x arm x axes) + headline verdicts."""
    lines = ["# Router validation scorecard", ""]
    for card in cards:
        lines.append(f"## {card['workload']}")
        lines.append("")
        lines.append("| arm | phases | rounds | rubric | cost (USD) | verdict |")
        lines.append("|-----|--------|--------|--------|-----------|---------|")
        for arm in ("mini", "router", "strong"):
            a = card["arms"].get(arm)
            if not a:
                continue
            ph = "pass" if a["phases_ok"] else "FAIL"
            lines.append(
                f"| {arm} | {ph} | {a['rounds']} | {a['rubric']:.2f} | "
                f"${a['cost_usd']:.2f} | {a['verdict']} |")
        rv = card.get("router_verdict")
        if rv:
            lines.append("")
            lines.append(f"**Router verdict:** {rv}")
        lines.append("")
    return "\n".join(lines)
