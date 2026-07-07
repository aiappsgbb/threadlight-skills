#!/usr/bin/env python3
"""govern_check.py — PROTECT-leg validator for threadlight-govern.

Scores whether the Microsoft Agent Governance Toolkit (AGT) is actually
*governing* a pilot — a committed, schema-valid policy that CI gates with
``agt lint-policy`` / ``agt verify`` (OWASP ASI 2026) and an attestation — and
emits a manifest that ``threadlight-production-ready`` pillars 2
(agent-governance) and 7 (responsible-ai) consume to flip those findings from
"remediate" to "verified".

The checks mirror the *real* toolkit (``pip install agent-governance-toolkit``,
CLI ``agt``): governance is proven at build / CI time via a committed policy +
attestation, not asserted by a runtime "middleware" shim. Capability keys mirror
the pillar-02 capability-detection table exactly so the two skills always agree:

    policy_artefact_present
    policy_schema_valid
    policy_versioned
    policy_default_deny
    sensitive_action_rules_present
    policy_tests_present
    ci_gate_present
    attestation_present
    attestation_fresh
    asi_reference_present

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

VERSION = "0.2.0"
MANIFEST_SCHEMA = "threadlight-govern-manifest/v2"

CAP_KEYS = (
    "policy_artefact_present",
    "policy_schema_valid",
    "policy_versioned",
    "policy_default_deny",
    "sensitive_action_rules_present",
    "policy_tests_present",
    "ci_gate_present",
    "attestation_present",
    "attestation_fresh",
    "asi_reference_present",
)

# ── detection corpora ────────────────────────────────────────────────────
POLICY_GLOBS = (
    "agt-policy.yaml", "agt-policy.yml", "agt-policy.json",
    "policy.yaml", "policy.yml", "policy.json",
    "policies/governance.yaml", "policies/governance.yml",
)
ATTESTATION_GLOBS = (
    "docs/agt-verifier-report.md",
    "docs/agt-governance-report.md",
    "tests/agt-verifier.json",
)
ATTESTATION_PATTERNS = (
    re.compile(r"agt-?verifier.*\.(md|json)$", re.I),
    re.compile(r"attestation.*\.(md|json)$", re.I),
)
# real governance-attestation/v1 marker emitted by `agt verify`
ATTESTATION_CONTENT = re.compile(r"governance-attestation|OWASP[_\s-]*ASI", re.I)

# top-level required fields (what `agt lint-policy` REQUIRED_FIELDS enforces)
TOP_VERSION = re.compile(r"^version\s*:", re.M)
TOP_NAME = re.compile(r"^name\s*:", re.M)
TOP_RULES = re.compile(r"^rules\s*:", re.M)
LIST_ITEM = re.compile(r"^\s+-\s", re.M)
DEFAULT_DENY = re.compile(
    r"^\s*deny_by_default\s*:\s*true\b"
    r"|^\s*default_action\s*:\s*['\"]?deny\b"
    r"|defaults\s*:\s*\n\s+action\s*:\s*['\"]?deny\b",
    re.I | re.M,
)
SENSITIVE_ACTION = re.compile(
    r"^\s*action\s*:\s*['\"]?(deny|block|escalate)\b", re.I | re.M)

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
# a workflow step that actually runs AGT governance
CI_GATE = re.compile(
    r"agt\s+(verify|lint-policy|test)\b|agent-governance-toolkit/action", re.I)
# an `agt test` fixture: a replay fixture with an expected verdict
FIXTURE_MARKER = re.compile(r"expected_verdict|expected_action", re.I)

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
    for path in _walk(root):
        rel = _rel(root, path)
        if rel.startswith("docs/") or "/fixtures/" in ("/" + rel):
            continue
        base = os.path.basename(path).lower()
        if "policy" in base and base.endswith((".yaml", ".yml", ".json")):
            return path
    return None


def _schema_valid(text: str) -> bool:
    """Proxy for `agt lint-policy`: top-level version + name + rules[] present."""
    return bool(
        TOP_VERSION.search(text)
        and TOP_NAME.search(text)
        and TOP_RULES.search(text)
        and LIST_ITEM.search(text)
    )


def _policy_tests(root: str) -> str | None:
    """A committed `agt test` fixture (input + expected_verdict/action)."""
    for path in _walk(root):
        rel = _rel(root, path)
        if not path.endswith((".yaml", ".yml", ".json")):
            continue
        if "fixture" in rel.lower() and FIXTURE_MARKER.search(_read(path)):
            return rel
    # fall back: any non-policy yaml/json carrying replay-fixture markers
    for path in _walk(root):
        if not path.endswith((".yaml", ".yml", ".json")):
            continue
        rel = _rel(root, path)
        if "policy" in os.path.basename(path).lower():
            continue
        if FIXTURE_MARKER.search(_read(path)):
            return rel
    return None


def _ci_gate(root: str) -> str | None:
    """A CI workflow that runs `agt verify` / `lint-policy` / `test`."""
    for path in _walk(root):
        rel = _rel(root, path)
        if "/.github/workflows/" not in ("/" + rel) and \
                not rel.startswith(".github/workflows/"):
            continue
        if path.endswith((".yml", ".yaml")) and CI_GATE.search(_read(path)):
            return rel
    return None


def _attestation(root: str) -> str | None:
    for g in ATTESTATION_GLOBS:
        cand = os.path.join(root, g)
        if os.path.isfile(cand):
            return cand
    for path in _walk(root):
        rel = _rel(root, path)
        if "fixture" in rel and "sample-" not in rel:
            continue
        base = os.path.basename(path)
        if any(p.search(base) for p in ATTESTATION_PATTERNS):
            return path
        if path.endswith((".md", ".json")) and not rel.startswith("policy") \
                and ATTESTATION_CONTENT.search(_read(path)):
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

    # policy artefact + schema/version/posture/sensitive-rules
    policy = _first_policy(root)
    policy_text = _read(policy) if policy else ""
    if policy:
        cap("policy_artefact_present", "pass", _rel(root, policy))
        if _schema_valid(policy_text):
            cap("policy_schema_valid", "pass",
                "top-level version + name + rules[] (agt lint-policy shape)")
        else:
            cap("policy_schema_valid", "must-fix", _rel(root, policy),
                "policy is missing a required top-level field (version / name / "
                "rules[]) — it will fail `agt lint-policy`")
        m = VERSION_FIELD.search(policy_text)
        if m and m.group("v").strip().lower() not in ("latest", ""):
            cap("policy_versioned", "pass", f"version: {m.group('v').strip()}")
        else:
            cap("policy_versioned", "should-fix", None,
                "policy ruleset has no pinned version (or is 'latest')")
        if DEFAULT_DENY.search(policy_text):
            cap("policy_default_deny", "pass",
                "default-deny posture declared (deny_by_default / default_action)")
        else:
            cap("policy_default_deny", "should-fix", None,
                "no default-deny posture (add deny_by_default: true or "
                "default_action: deny)")
        if SENSITIVE_ACTION.search(policy_text):
            cap("sensitive_action_rules_present", "pass",
                "deny/block/escalate rule(s) over sensitive actions detected")
        else:
            cap("sensitive_action_rules_present", "should-fix", None,
                "no deny/block/escalate rule over a sensitive or state-changing "
                "action (pillar 7 RAI)")
    else:
        cap("policy_artefact_present", "must-fix", None,
            "no AGT policy artefact (agt-policy.yaml / policy.yaml) found")
        cap("policy_schema_valid", "must-fix", None, "no policy artefact to validate")
        cap("policy_versioned", "must-fix", None, "no policy artefact to version")
        cap("policy_default_deny", "should-fix", None, "no policy artefact")
        cap("sensitive_action_rules_present", "must-fix", None,
            "no policy artefact for sensitive-action rules")

    # policy test fixtures (`agt test`)
    fixture = _policy_tests(root)
    if fixture:
        cap("policy_tests_present", "pass", fixture)
    else:
        cap("policy_tests_present", "should-fix", None,
            "no `agt test` fixtures committed ({input, expected_verdict}) — "
            "policy behaviour is unverified")

    # CI gate (governance actually runs)
    gate = _ci_gate(root)
    if gate:
        cap("ci_gate_present", "pass", gate)
    else:
        cap("ci_gate_present", "should-fix", None,
            "no CI workflow runs `agt verify` / `agt lint-policy` / `agt test` "
            "(governance is documented but never enforced)")

    # attestation artefact + freshness
    att = _attestation(root)
    if att:
        cap("attestation_present", "pass", _rel(root, att))
        days = _mtime_days(att)
        if days is None:
            cap("attestation_fresh", "not-verified", None, "could not stat attestation")
        elif days <= freshness_days:
            cap("attestation_fresh", "pass", f"{days}d old (<= {freshness_days}d)")
        else:
            cap("attestation_fresh", "should-fix", f"{days}d old",
                f"attestation stale (> {freshness_days}d) — re-run `agt verify`")
    else:
        cap("attestation_present", "should-fix", None,
            "no committed `agt verify` attestation (governance-attestation/v1)")
        cap("attestation_fresh", "not-verified", None, "no attestation to age")

    # ASI reference (policy + attestation + governance docs)
    asi_hit = bool(policy_text and ASI_MARKERS.search(policy_text))
    if not asi_hit and att:
        asi_hit = bool(ASI_MARKERS.search(_read(att)))
    if not asi_hit:
        for g in ("SECURITY.md", "docs/governance.md"):
            p = os.path.join(root, g)
            if os.path.isfile(p) and ASI_MARKERS.search(_read(p)):
                asi_hit = True
                break
    cap("asi_reference_present", "pass" if asi_hit else "should-fix",
        "OWASP ASI 2026 reference found" if asi_hit else None,
        None if asi_hit else "no OWASP ASI 2026 anchor — add to policy or attestation")

    return caps


def manifest(root: str, caps: dict, profile: str, freshness_days: int) -> dict:
    order = {"must-fix": 0, "should-fix": 1, "not-verified": 2,
             "pass": 3, "not-applicable": 4}
    must = [k for k, v in caps.items() if v["status"] == "must-fix"]
    should = [k for k, v in caps.items() if v["status"] == "should-fix"]
    notv = [k for k, v in caps.items() if v["status"] == "not-verified"]
    if must:
        verdict = "ungoverned"
    elif should or notv:
        verdict = "partial"
    else:
        verdict = "governed"
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
        "# Agent governance (AGT) — governance report",
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
    ap = argparse.ArgumentParser(description="threadlight-govern AGT governance validator")
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

    try:
        caps = evaluate(root, args.freshness_days)
        man = manifest(root, caps, args.profile, args.freshness_days)
    except Exception as exc:  # graceful top-level degradation
        caps = {key: {"status": "not-verified", "evidence": None,
                      "hint": f"validator could not complete: {exc}"}
                for key in CAP_KEYS}
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
