#!/usr/bin/env python3
"""Deterministic finding taxonomy for the threadlight-router-bench learnings digest.

Scans harvested CI log lines and classifies anomalies into a fixed taxonomy. The
LLM recommendations turn (driven by SKILL.md) consumes these *structured*
findings — never the raw logs — so recommendations stay grounded.

PRECISION (learned the hard way against real logs — see the design spec):
  * Primary input is `gh run view <id> --log-failed` (failing steps only). On
    real runs 28435017341 + 28389162228 this gave 100% precision; a naive full
    `--log` scan of the green run 28437323962 was 10/10 false positives.
  * `is_noise()` drops command-echo lines: GitHub's "Run" block echoes the step
    script source in cyan-bold (the literal `[36;1m` ANSI token), and `##[group]`
    /`##[command]` are control lines. Without this the scanner matches the
    workflow's OWN defensive comments + grep-based error detectors + prompt text.
  * Findings are deduped by category (with a `count`), since one root cause (e.g.
    a rate limit) emits the same line many times.

Taxonomy is ordered: the first matching rule wins, so specific signatures
(dependency, wire_protocol) precede generic ones (retry).
"""
from __future__ import annotations

import re
from typing import Any

# (category, severity, pattern) — ORDER MATTERS, first match wins.
# Tuned against real failed logs (dependency drift + rate-limit cascade).
_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("dependency",       "high",   re.compile(r"ResolutionImpossible|conflicting dependencies|Cannot install .*because|no matching distribution", re.I)),
    ("skill_loader",     "medium", re.compile(r"not in the built-in catalog|skill .*not found|unknown skill", re.I)),
    ("wire_protocol",    "high",   re.compile(r"operation unsupported|\b400\b.*unsupported", re.I)),
    ("rate_limit",       "medium", re.compile(r"exceeded rate limit|rate.?limit|CAPIError|Too Many Requests|\b429\b", re.I)),
    ("model_unavailable","high",   re.compile(r"Failed to get response from the AI model|transient API error", re.I)),
    ("auth",             "high",   re.compile(r"\b401\b|\b403\b|unauthorized|forbidden|AADSTS\d+", re.I)),
    ("quota",            "medium", re.compile(r"quota exceeded|insufficient capacity|capacity exceeded|SkuNotAvailable", re.I)),
    ("deploy",           "high",   re.compile(r"azd (up|down)[^\n]*fail|deployment (error|failed)|DeploymentFailed", re.I)),
    ("tool_failure",     "medium", re.compile(r"tool (call )?failed|tool error", re.I)),
    ("router_fallback",  "low",    re.compile(r"router .*fallback|fell back to", re.I)),
    ("retry",            "low",    re.compile(r"retried \d+ times|Retrying\.\.\.|sleeping \d+s|Attempt \d+ failed", re.I)),
    ("slow_turn",        "low",    re.compile(r"took \d{4,}\s?ms|slow turn", re.I)),
]

# Command-echo / control markers that are step SOURCE, not runtime output.
_NOISE = re.compile(r"\[36;1m|##\[(group|command|section|endgroup)\]")


def is_noise(line: str) -> bool:
    """True for command-echo / GA-control lines that must not be classified."""
    return bool(_NOISE.search(line))


def classify_line(text: str) -> tuple[str, str] | None:
    """Return (category, severity) for the first matching rule, else None."""
    for category, severity, pat in _RULES:
        if pat.search(text):
            return category, severity
    return None


def _message(line: str) -> str:
    """Strip the gh `<job>\\t<step>\\t<ISO>Z ` prefix + ANSI, return the message."""
    tail = line.split("\t")[-1]
    tail = re.sub(r"^\S*Z\s", "", tail)                 # drop leading ISO timestamp
    tail = re.sub(r"(\x1b|\^)\[[0-9;]*m", "", tail)     # drop ANSI (real ESC or literal ^[)
    return tail.strip()


def scan_lines(lines: list[str], run_id: int, phase: str,
               source: str = "") -> list[dict[str, Any]]:
    """Classify lines, skipping noise, deduped by category (with count + first evidence)."""
    order: list[str] = []
    agg: dict[str, dict[str, Any]] = {}
    for i, line in enumerate(lines, start=1):
        if is_noise(line):
            continue
        hit = classify_line(line)
        if hit is None:
            continue
        category, severity = hit
        if category not in agg:
            order.append(category)
            agg[category] = {"category": category, "severity": severity,
                             "run_id": run_id, "phase": phase, "count": 0,
                             "evidence": {"file": source, "line": i,
                                          "excerpt": _message(line)[:200]}}
        agg[category]["count"] += 1
    out: list[dict[str, Any]] = []
    for n, category in enumerate(order, start=1):
        f = agg[category]
        f["id"] = f"F-{run_id}-{n:03d}"
        out.append(f)
    return out
