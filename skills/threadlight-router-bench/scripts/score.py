#!/usr/bin/env python3
"""Cost rollup + counterfactual scorecard for the bench cost axis.

The honest efficiency question is NOT "is the router always cheaper" — on the
hard agentic workload model-router routed entirely to gpt-5.4/gpt-5.5 (zero to
gpt-5.4-mini), so it paid a PREMIUM for per-turn right-sizing. The scorecard
therefore reports candidate actual $ against a counterfactual: the same token
volume priced entirely at the baseline model. Verdict states premium vs savings
plainly so the efficiency story to customers is honest.
"""
from __future__ import annotations

from typing import Any

SCHEMA = "threadlight-router-scorecard/v1"


def cost_of(usage: dict[str, dict[str, int]],
            prices: dict[str, dict[str, float]]) -> float:
    """USD cost of per-model usage given a $/1M-token price table."""
    total = 0.0
    for model, toks in usage.items():
        rate = prices.get(model)
        if rate is None:
            continue
        total += toks.get("input", 0) / 1_000_000 * rate["input"]
        total += toks.get("output", 0) / 1_000_000 * rate["output"]
    return total


def _totals(usage: dict[str, dict[str, int]]) -> dict[str, int]:
    return {"input": sum(t.get("input", 0) for t in usage.values()),
            "output": sum(t.get("output", 0) for t in usage.values())}


def scorecard(candidate_usage: dict[str, dict[str, int]], baseline_model: str,
              prices: dict[str, dict[str, float]],
              baseline_usage: dict[str, dict[str, int]] | None = None) -> dict[str, Any]:
    """Compare candidate actual cost to the baseline-model counterfactual.

    counterfactual = candidate's TOTAL tokens, repriced entirely at baseline_model.
    If `baseline_usage` (a real baseline run) is given, also report its actual cost.
    """
    candidate_cost = round(cost_of(candidate_usage, prices), 4)
    totals = _totals(candidate_usage)
    counterfactual = round(cost_of({baseline_model: totals}, prices), 4)
    delta = round(candidate_cost - counterfactual, 4)
    if delta < -1e-9:
        verdict = "router-savings"
    elif delta > 1e-9:
        verdict = "router-premium"
    else:
        verdict = "neutral"
    card: dict[str, Any] = {
        "schema": SCHEMA,
        "baseline_model": baseline_model,
        "candidate_usage": candidate_usage,
        "candidate_total_tokens": totals,
        "candidate_cost_usd": candidate_cost,
        "counterfactual_baseline_usd": counterfactual,
        "delta_usd": delta,
        "verdict": verdict,
    }
    if baseline_usage is not None:
        card["baseline_usage"] = baseline_usage
        card["baseline_actual_usd"] = round(cost_of(baseline_usage, prices), 4)
    return card


def render_scorecard(card: dict[str, Any], quality: dict[str, Any] | None = None) -> str:
    """Render the cost scorecard (+ optional quality diff) as Markdown."""
    out = ["# Router-bench cost scorecard", ""]
    t = card["candidate_total_tokens"]
    out += [
        f"- **Candidate actual:** ${card['candidate_cost_usd']:.4f} "
        f"({t['input']:,} in / {t['output']:,} out tokens)",
        f"- **Counterfactual (all @ {card['baseline_model']}):** "
        f"${card['counterfactual_baseline_usd']:.4f}",
        f"- **Delta:** ${card['delta_usd']:.4f}  →  **{card['verdict']}**",
    ]
    if "baseline_actual_usd" in card:
        out.append(f"- **Baseline run actual:** ${card['baseline_actual_usd']:.4f}")
    out.append("")
    out.append("## Per-model routing")
    for model, toks in card["candidate_usage"].items():
        out.append(f"- `{model}` — {toks.get('input', 0):,} in / {toks.get('output', 0):,} out")
    out.append("")
    if quality:
        out.append("## Quality parity")
        for leg, verdict in quality.items():
            out.append(f"- `{leg}` — {verdict}")
        out.append("")
    return "\n".join(out)


VALIDATION_SCHEMA = "threadlight-router-validation/v1"

# Verdict thresholds (from the design spec §4).
RUBRIC_FLOOR = 0.8
ROUNDS_FACTOR = 1.5
RUBRIC_CLOSE = 0.1   # router rubric within this of strong == "matches"
COST_CLOSE = 0.20    # cost within +/-20% == "approximately equal"


def _arm_index(arms: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {a["arm"]: a for a in arms}


def validation_scorecard(workload: str,
                         arms: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a threadlight-router-validation/v1 scorecard for one workload.

    Each arm dict: {arm, phases_ok: bool, rounds: int, rubric: float,
    cost_usd: float}. Verdicts per design spec: an arm "keeps-up" iff
    phases_ok AND rubric >= RUBRIC_FLOOR AND rounds <= ROUNDS_FACTOR * strong
    rounds; else "falls-behind" with reasons. Router verdict compares the
    router arm to strong.
    """
    idx = _arm_index(arms)
    strong = idx.get("strong")
    strong_rounds = strong["rounds"] if strong else None

    out_arms: dict[str, Any] = {}
    for a in arms:
        reasons: list[str] = []
        if not a.get("phases_ok", False):
            reasons.append("phase")
        if a.get("rubric", 0.0) < RUBRIC_FLOOR:
            reasons.append("rubric")
        if (strong_rounds is not None and a["arm"] != "strong"
                and a.get("rounds", 0) > ROUNDS_FACTOR * strong_rounds):
            reasons.append("rounds")
        verdict = "keeps-up" if not reasons else "falls-behind"
        out_arms[a["arm"]] = {**a, "verdict": verdict, "reasons": reasons}

    router_verdict = None
    router = idx.get("router")
    if router and strong:
        rubric_matches = abs(router["rubric"] - strong["rubric"]) <= RUBRIC_CLOSE
        cheaper = router["cost_usd"] < strong["cost_usd"]
        cost_similar = (abs(router["cost_usd"] - strong["cost_usd"])
                        <= COST_CLOSE * strong["cost_usd"])
        if rubric_matches and cheaper:
            router_verdict = "closes-the-gap"
        elif cost_similar and not rubric_matches:
            router_verdict = "not-worth-it"
        else:
            router_verdict = "mixed"

    return {
        "schema": VALIDATION_SCHEMA,
        "workload": workload,
        "arms": out_arms,
        "router_verdict": router_verdict,
        "thresholds": {"rubric_floor": RUBRIC_FLOOR,
                       "rounds_factor": ROUNDS_FACTOR,
                       "rubric_close": RUBRIC_CLOSE,
                       "cost_close": COST_CLOSE},
    }
