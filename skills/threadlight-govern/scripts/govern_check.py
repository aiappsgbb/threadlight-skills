#!/usr/bin/env python3
"""govern_check.py — PROTECT-leg validator for threadlight-govern.

Verifies that the Microsoft Agent Governance Toolkit (AGT) is actually
*wired* into a deployed pilot — not just documented — and emits a manifest
that `threadlight-production-ready` pillars 2 (agent-governance) and 7
(responsible-ai) consume to flip those findings from "remediate" to
"verified".

Capability keys mirror the pillar-02 capability-detection table exactly so
the two skills always agree:

    policy_artefact_present
    policy_versioned
    middleware_wired_at_boundary
    verifier_artefact_present
    verifier_fresh
    rai_policy_present
    asi_reference_present
    sidecar_pattern

stdlib-only. No third-party deps. Gracefully degrading — a capability that
cannot be checked is reported as ``not-verified``, never a crash.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys

VERSION = "0.1.0"
MANIFEST_SCHEMA = "threadlight-govern-manifest/v1"

# ── detection corpora ────────────────────────────────────────────────────
ENTRY_POINTS = (
    "src/app.py",
    "src/main.py",
    "src/agent/main.py",
    "src/agent/app.py",
    "container.py",
    "src/container.py",
    "src/index.ts",
    "src/agent/index.ts",
    "app.py",
    "main.py",
)
POLICY_GLOBS = (
    "agt-policy.yaml", "agt-policy.yml", "agt-policy.json",
    "policy.yaml", "policy.yml", "policy.json",
    "policies/governance.yaml", "policies/governance.yml",
)
VERIFIER_GLOBS = (
    "docs/agt-verifier-report.md",
    "tests/agt-verifier.json",
)
VERIFIER_PATTERNS = (
    re.compile(r"agt-?verifier.*\.(md|json)$", re.I),
    re.compile(r"verifier.*\.json$", re.I),
)
MIDDLEWARE_IMPORTS = re.compile(
    r"(from\s+agt\s+import|import\s+agt\b|from\s+agent_os|"
    r"apply_governance|create_governance_middleware|AgentGovernance|"
    r"@foundry/agt|applyGovernance)",
)
RAI_MARKERS = re.compile(
    r"(content[_-]?filter|prompt[_-]?shield|jailbreak|indirect[_-]?attack|"
    r"pii[_-]?redact|pii[_-]?deny|responsible[_-]?ai|prompt_shields)",
    re.I,
)
ASI_MARKERS = re.compile(
    r"(owasp[_\s-]*asi|agentic[_\s-]*security[_\s-]*initiative|asi[_\s-]*2026|"
    r"agentic[_\s-]*top[_\s-]*10|owasp[_\s-]*agentic)",
    re.I,
)
VERSION_FIELD = re.compile(
    r"^\s*(version|policy_version|agent_control_specification_version)\s*:\s*"
    r"['\"]?(?P<v>[^'\"\n]+)",
    re.I | re.M,
)
SIDECAR_MARKERS = re.compile(r"agt[-_]?sidecar", re.I)

TEXT_EXT = (".py", ".ts", ".yaml", ".yml", ".json", ".toml", ".md", ".bicep")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".azure"}
MAX_BYTES = 512 * 1024


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(MAX_BYTES)
    except OSError:
        return ""


def _walk(root: str):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(TEXT_EXT):
                yield os.path.join(base, name)


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _first_policy(root: str) -> str | None:
    for g in POLICY_GLOBS:
        cand = os.path.join(root, g)
        if os.path.isfile(cand):
            return cand
    # fall back to any *policy*.yaml under the tree (not under docs/)
    for path in _walk(root):
        rel = _rel(root, path)
        if rel.startswith("docs/"):
            continue
        base = os.path.basename(path).lower()
        if "policy" in base and base.endswith((".yaml", ".yml", ".json")):
            return path
    return None


def _verifier_artefact(root: str) -> str | None:
    for g in VERIFIER_GLOBS:
        cand = os.path.join(root, g)
        if os.path.isfile(cand):
            return cand
    for path in _walk(root):
        rel = _rel(root, path)
        if rel.startswith("docs/") and "fixture" not in rel:
            pass
        if any(p.search(os.path.basename(path)) for p in VERIFIER_PATTERNS):
            if "fixture" not in rel and "node_modules" not in rel:
                return path
    return None


def _mtime_days(path: str) -> float | None:
    try:
        age = _dt.datetime.now() - _dt.datetime.fromtimestamp(os.path.getmtime(path))
        return round(age.total_seconds() / 86400.0, 1)
    except OSError:
        return None


# ── capability evaluation ────────────────────────────────────────────────
def evaluate(root: str, freshness_days: int) -> dict:
    caps: dict[str, dict] = {}

    def cap(key, status, evidence=None, hint=None):
        caps[key] = {"status": status, "evidence": evidence, "hint": hint}

    # entry-point + middleware wiring
    entry = next((os.path.join(root, e) for e in ENTRY_POINTS
                  if os.path.isfile(os.path.join(root, e))), None)
    wired_file = None
    if entry and MIDDLEWARE_IMPORTS.search(_read(entry)):
        wired_file = _rel(root, entry)
    if wired_file is None:
        for path in _walk(root):
            rel = _rel(root, path)
            if rel.startswith(("src/", "")) and path.endswith((".py", ".ts")):
                if "fixture" in rel or "/tests/" in rel:
                    continue
                if MIDDLEWARE_IMPORTS.search(_read(path)):
                    wired_file = rel
                    break
    if wired_file:
        cap("middleware_wired_at_boundary", "pass", wired_file)
    elif entry:
        cap("middleware_wired_at_boundary", "must-fix", _rel(root, entry),
            "agent entry-point found but no AGT middleware import — wire "
            "create_governance_middleware() / apply_governance() at the boundary")
    else:
        cap("middleware_wired_at_boundary", "not-verified", None,
            "no recognised agent entry-point found to inspect")

    # policy artefact
    policy = _first_policy(root)
    policy_text = _read(policy) if policy else ""
    if policy:
        cap("policy_artefact_present", "pass", _rel(root, policy))
        m = VERSION_FIELD.search(policy_text)
        if m and m.group("v").strip().lower() not in ("latest", ""):
            cap("policy_versioned", "pass", f"version: {m.group('v').strip()}")
        else:
            cap("policy_versioned", "should-fix", None,
                "policy artefact has no pinned version (or is 'latest')")
        if RAI_MARKERS.search(policy_text):
            cap("rai_policy_present", "pass",
                "content-filter / prompt-shield / PII block detected in policy")
        else:
            cap("rai_policy_present", "should-fix", None,
                "no content-filter / prompt-shield / PII-redaction block in policy "
                "(pillar 7 RAI)")
    else:
        cap("policy_artefact_present", "must-fix", None,
            "no AGT policy artefact (agt-policy.yaml / policy.yaml) found")
        cap("policy_versioned", "must-fix", None, "no policy artefact to version")
        cap("rai_policy_present", "must-fix", None, "no policy artefact for RAI block")

    # verifier artefact + freshness
    verifier = _verifier_artefact(root)
    if verifier:
        cap("verifier_artefact_present", "pass", _rel(root, verifier))
        days = _mtime_days(verifier)
        if days is None:
            cap("verifier_fresh", "not-verified", None, "could not stat verifier file")
        elif days <= freshness_days:
            cap("verifier_fresh", "pass", f"{days}d old (<= {freshness_days}d)")
        else:
            cap("verifier_fresh", "should-fix", f"{days}d old",
                f"verifier artefact stale (> {freshness_days}d) — re-run `agt verify`")
    else:
        cap("verifier_artefact_present", "should-fix", None,
            "no committed `agt verify` evidence (docs/agt-verifier-report.md / "
            "tests/agt-verifier.json)")
        cap("verifier_fresh", "not-verified", None, "no verifier artefact to age")

    # ASI reference (search policy + any governance doc)
    asi_hit = bool(policy_text and ASI_MARKERS.search(policy_text))
    if not asi_hit:
        for g in ("docs/agt-verifier-report.md", "SECURITY.md", "docs/governance.md"):
            p = os.path.join(root, g)
            if os.path.isfile(p) and ASI_MARKERS.search(_read(p)):
                asi_hit = True
                break
    cap("asi_reference_present", "pass" if asi_hit else "should-fix",
        "OWASP ASI 2026 reference found" if asi_hit else None,
        None if asi_hit else "no OWASP ASI 2026 anchor — add to policy or verifier report")

    # sidecar (informational)
    sidecar = False
    for g in ("azure.yaml", "infra/main.bicep"):
        p = os.path.join(root, g)
        if os.path.isfile(p) and SIDECAR_MARKERS.search(_read(p)):
            sidecar = True
            break
    cap("sidecar_pattern", "pass" if sidecar else "not-applicable",
        "agt-sidecar detected" if sidecar else "in-process (Path A) — no sidecar")

    return caps


def manifest(root: str, caps: dict, profile: str, freshness_days: int) -> dict:
    order = {"must-fix": 0, "should-fix": 1, "not-verified": 2,
             "pass": 3, "not-applicable": 4}
    must = [k for k, v in caps.items() if v["status"] == "must-fix"]
    should = [k for k, v in caps.items() if v["status"] == "should-fix"]
    notv = [k for k, v in caps.items() if v["status"] == "not-verified"]
    if must:
        verdict = "not-wired"
    elif should or notv:
        verdict = "partial"
    else:
        verdict = "wired"
    return {
        "schema": MANIFEST_SCHEMA,
        "tool_version": VERSION,
        "captured_at": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0).isoformat(),
        "agt_profile": profile,
        "freshness_window_days": freshness_days,
        "verdict": verdict,
        "must_fix": must,
        "should_fix": should,
        "not_verified": notv,
        "capabilities": dict(sorted(
            caps.items(), key=lambda kv: order.get(kv[1]["status"], 9))),
    }


def render(man: dict) -> str:
    icon = {"pass": "✅", "must-fix": "❌", "should-fix": "🟠",
            "not-verified": "⚪", "not-applicable": "➖"}
    lines = [
        "# Agent governance (AGT) — wiring report",
        "",
        f"> Verdict: **{man['verdict'].upper()}** · profile `{man['agt_profile']}` "
        f"· captured {man['captured_at']}",
        "",
        "| Capability | Status | Evidence / hint |",
        "|---|---|---|",
    ]
    for key, v in man["capabilities"].items():
        detail = v.get("evidence") or v.get("hint") or ""
        lines.append(f"| `{key}` | {icon.get(v['status'], '?')} {v['status']} | {detail} |")
    lines += ["", "Consumed by `threadlight-production-ready` pillars 2 "
              "(agent-governance) + 7 (responsible-ai).", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="threadlight-govern AGT wiring validator")
    ap.add_argument("--target", default=".", help="pilot repo root (default cwd)")
    ap.add_argument("--profile", default="auto",
                    choices=["auto", "v3_7", "v4_preview", "none"])
    ap.add_argument("--freshness-days", type=int, default=90)
    ap.add_argument("--emit", action="store_true",
                    help="write specs/govern-manifest.json + docs/agt-governance-report.md")
    ap.add_argument("--gate", action="store_true",
                    help="exit 2 when any capability is must-fix")
    ap.add_argument("--json", action="store_true", help="print manifest JSON to stdout")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.target)
    if args.profile == "none":
        print("agt-profile=none → governance pillar not applicable")
        return 0

    caps = evaluate(root, args.freshness_days)
    man = manifest(root, caps, args.profile, args.freshness_days)

    if args.emit:
        os.makedirs(os.path.join(root, "specs"), exist_ok=True)
        os.makedirs(os.path.join(root, "docs"), exist_ok=True)
        with open(os.path.join(root, "specs", "govern-manifest.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(man, fh, indent=2)
            fh.write("\n")
        with open(os.path.join(root, "docs", "agt-governance-report.md"), "w",
                  encoding="utf-8") as fh:
            fh.write(render(man))

    if args.json:
        print(json.dumps(man, indent=2))
    else:
        print(render(man))

    if args.gate and man["must_fix"]:
        print(f"\nGATE: {len(man['must_fix'])} must-fix capability(ies): "
              f"{', '.join(man['must_fix'])}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
