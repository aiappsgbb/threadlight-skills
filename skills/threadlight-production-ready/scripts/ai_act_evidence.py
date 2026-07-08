#!/usr/bin/env python3
"""EU AI Act evidence-pack aggregator — map the artifacts a customer already
produces onto EU AI Act articles and emit a tenant-local evidence pack.

Pure standard library. Offline, deterministic, and read-only: this is the
terminal aggregator of the A -> B -> C runtime-governance arc. It consumes the
production-readiness scorecard manifest, the MCP SBOM (``mcp-sbom.json``), the
agent-identity AI-BOM (``agent-identity.json``), the govern manifest, and the
evals / red-team manifests. It NEVER calls Azure and NEVER fabricates coverage:
a missing or malformed source degrades to ``gap`` / ``partial`` with a
remediation pointer at a Microsoft platform skill.

Outputs (default dir ``docs/compliance/``):
  * ``ai-act-evidence.json``       — machine-readable article-by-article map.
  * ``annex-iv-technical-file.md`` — human-readable Annex IV / Art 11 file.
  * ``fria-scaffold.md``           — Art 27 fundamental-rights impact scaffold.

This amplifies the platform: it turns Foundry's own eval / red-team /
observability / identity outputs into regulator-facing evidence. It is not
legal advice and does not replace a conformity assessment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Tracks the threadlight-production-ready skill version (see SKILL.md frontmatter).
EVIDENCE_VERSION = "0.8.0"

SCHEMA = "threadlight.ai-act-evidence/v1"
GENERATOR = "threadlight-production-ready/ai_act_evidence"

DISCLAIMER = (
    "Tenant-local evidence generated from artifacts already in this repository. "
    "This is an engineering aid, not legal advice, and does not by itself "
    "constitute an EU AI Act conformity assessment. Have a qualified reviewer "
    "confirm scope, risk classification, and completeness."
)

# Candidate on-disk locations per source. discover() takes the first that
# exists and parses; standalone producers write to the repo root, the assessor
# writes the sidecars next to its report under docs/.
_CANDIDATES = {
    "scorecard": ["tests/production-readiness-manifest.json",
                  "production-readiness-manifest.json",
                  "docs/production-readiness-manifest.json"],
    "mcp_sbom": ["mcp-sbom.json", "docs/mcp-sbom.json"],
    "agent_identity": ["agent-identity.json", "docs/agent-identity.json"],
    "govern": ["govern-manifest.json", "specs/govern-manifest.json",
               "docs/govern-manifest.json"],
    "evals": ["specs/evals-manifest.json", "evals-manifest.json"],
    "redteam": ["specs/redteam-manifest.json", "redteam-manifest.json"],
}

# Articles whose gap makes --check fail (the load-bearing evidence obligations).
MUST_HAVE = ("art-11-annex-iv", "art-12-records", "art-15-accuracy-robustness")


# ------------------------------------------------------------------ discovery
def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover(root) -> dict:
    """Locate every source artifact under *root*.

    Returns ``{key: {"present", "path", "sha256", "data"}}``. A source is
    ``present`` only when a candidate file exists AND parses as JSON — a
    malformed artifact is treated as absent (never as evidence).
    """
    root = Path(root)
    out: dict[str, dict] = {}
    for key, rels in _CANDIDATES.items():
        entry = {"present": False, "path": None, "sha256": None, "data": None}
        for rel in rels:
            p = root / rel
            if not p.is_file():
                continue
            try:
                raw = p.read_text(encoding="utf-8")
                data = json.loads(raw)
            except (OSError, ValueError):
                continue
            entry = {"present": True, "path": rel,
                     "sha256": _sha256(raw), "data": data}
            break
        out[key] = entry
    return out


def _pillar_status(scorecard: dict | None, pillar_id: str):
    """Return the with-waivers status (green/amber/red) for a pillar, or None.

    Defensive against wrong-shape input: a scorecard that is not a dict, or whose
    ``pillars`` is not a list (e.g. ``null`` / a scalar), degrades to ``None``
    rather than raising — honoring discover()'s "malformed source is not
    evidence" contract for valid-JSON-but-wrong-shape artifacts.
    """
    if not isinstance(scorecard, dict):
        return None
    pillars = scorecard.get("pillars")
    if not isinstance(pillars, list):
        return None
    for p in pillars:
        if isinstance(p, dict) and p.get("pillar") == pillar_id:
            return p.get("status_with_waivers") or p.get("status_raw")
    return None


def _src_ref(sources: dict, key: str) -> dict:
    """A provenance stub for one source, safe to embed in an article."""
    s = sources.get(key, {})
    return {"artifact": key, "path": s.get("path"),
            "present": bool(s.get("present")), "sha256": s.get("sha256")}


def _has_content(sources: dict, key: str) -> bool:
    """True only when a source is present AND carries real (non-empty) content.

    A parseable-but-empty stub (``{}`` / ``[]``) is present for provenance but is
    NOT evidence — grading ``covered`` requires genuine content so an empty file
    can never fabricate coverage.
    """
    s = sources.get(key, {})
    if not s.get("present"):
        return False
    data = s.get("data")
    if isinstance(data, (dict, list)):
        return len(data) > 0
    return False


# ------------------------------------------------------------------ mapping
def _coverage_art9(src, sc):
    present = src["govern"]["present"]
    if _has_content(src, "govern") and _pillar_status(sc, "agent-governance") == "green":
        return "covered"
    if present:
        return "partial"
    return "gap"


def _coverage_art11(src, sc):
    if _has_content(src, "scorecard") and _has_content(src, "mcp_sbom"):
        return "covered"
    if src["scorecard"]["present"]:
        return "partial"
    return "gap"


def _coverage_art12(src, sc):
    obs = _pillar_status(sc, "observability") == "green"
    ident_content = _has_content(src, "agent_identity")
    if obs and ident_content:
        return "covered"
    if obs or src["agent_identity"]["present"]:
        return "partial"
    return "gap"


def _coverage_art14(src, sc):
    hitl = _pillar_status(sc, "hitl-audit")
    if hitl == "green":
        return "covered"
    if hitl == "amber":
        return "partial"
    return "gap"


def _coverage_art15(src, sc):
    if _has_content(src, "evals") and _has_content(src, "redteam"):
        return "covered"
    if src["evals"]["present"] or src["redteam"]["present"]:
        return "partial"
    return "gap"


def _coverage_art26(src, sc):
    ident = src["agent_identity"]
    if not ident["present"]:
        return "gap"
    data = ident["data"]
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, dict):
        return "partial"
    count = summary.get("subject_count")
    owned = summary.get("owned")
    if (isinstance(count, int) and not isinstance(count, bool)
            and isinstance(owned, int) and not isinstance(owned, bool)
            and count > 0 and owned == count):
        return "covered"
    return "partial"


# id, title, obligation, source keys, remediation skill, coverage fn.
ARTICLE_MAP = [
    ("art-9-risk-management", "Article 9 — Risk management system",
     "Establish and document a continuous risk-management process.",
     ["govern", "scorecard"], "threadlight-govern", _coverage_art9),
    ("art-11-annex-iv", "Article 11 + Annex IV — Technical documentation",
     "Maintain a technical file describing the system, its design and controls.",
     ["scorecard", "mcp_sbom"], "threadlight-production-ready", _coverage_art11),
    ("art-12-records", "Article 12 — Record-keeping (logging)",
     "Automatically record events over the system's lifetime for traceability.",
     ["scorecard", "agent_identity"], "foundry-observability", _coverage_art12),
    ("art-14-oversight", "Article 14 — Human oversight",
     "Enable effective oversight by natural persons during operation.",
     ["scorecard"], "threadlight-hitl-patterns", _coverage_art14),
    ("art-15-accuracy-robustness",
     "Article 15 — Accuracy, robustness and cybersecurity",
     "Demonstrate appropriate accuracy, robustness and cyber-resilience.",
     ["evals", "redteam", "mcp_sbom"], "threadlight-evals", _coverage_art15),
    ("art-26-deployer", "Article 26 — Deployer obligations",
     "Assign a responsible owner and operate the system as instructed.",
     ["agent_identity"], "foundry-agt", _coverage_art26),
    ("art-27-fria", "Article 27 — Fundamental-rights impact assessment",
     "Assess the impact on fundamental rights before putting into use.",
     [], "threadlight-govern", None),
]

_REMEDIATION = {
    "gap": "Source evidence is absent. Run the mapped skill to produce it, "
           "then re-run this aggregator.",
    "partial": "Some evidence is present but incomplete. Close the mapped "
               "pillar / add the missing artifact, then re-run.",
    "scaffold": "Template only — a human must complete this assessment; the "
                "tool cannot infer fundamental-rights impact.",
}


def assess(root, *, now=None, governance=None):
    """Map every article for the repo at *root*.

    Returns ``(evidence, articles)`` where *articles* is the per-article list
    and *evidence* is the full manifest from :func:`build_evidence`.
    """
    sources = discover(root)
    sc = sources["scorecard"]["data"]
    articles = []
    for aid, title, obligation, keys, skill, fn in ARTICLE_MAP:
        coverage = "scaffold" if fn is None else fn(sources, sc)
        art = {
            "id": aid,
            "title": title,
            "obligation": obligation,
            "coverage": coverage,
            "sources": [_src_ref(sources, k) for k in keys],
            "remediation_skill": skill,
        }
        if coverage in _REMEDIATION:
            art["remediation"] = _REMEDIATION[coverage]
        articles.append(art)
    evidence = build_evidence(articles, sources, now=now)
    return evidence, articles


def build_evidence(articles, sources, *, now=None) -> dict:
    """Assemble the deterministic ``ai-act-evidence.json`` manifest."""
    generated_at = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = {"covered": 0, "partial": 0, "gap": 0,
              "scaffold": 0, "not_applicable": 0}
    for a in articles:
        key = a["coverage"].replace("-", "_")
        if key in counts:
            counts[key] += 1
    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "generator_version": EVIDENCE_VERSION,
        "generated_at": generated_at,
        "tenant_local": True,
        "disclaimer": DISCLAIMER,
        "articles": articles,
        "summary": {"articles_total": len(articles), **counts},
    }


# ------------------------------------------------------------------ renderers
_COVER_MARK = {"covered": "COVERED", "partial": "PARTIAL",
               "gap": "GAP", "scaffold": "SCAFFOLD",
               "not-applicable": "N/A"}


def _mark(coverage: str) -> str:
    return _COVER_MARK.get(coverage, coverage.upper())


def render_annex_iv(evidence, sources) -> str:
    lines = [
        "# Annex IV — Technical documentation (EU AI Act, Article 11)",
        "",
        f"_Generated {evidence['generated_at']} · "
        f"{evidence['generator']} v{evidence['generator_version']}._",
        "",
        f"> {evidence['disclaimer']}",
        "",
        "## Coverage summary",
        "",
        "| Article | Obligation | Coverage | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for a in evidence["articles"]:
        srcs = ", ".join(
            f"`{s['artifact']}`" + ("" if s["present"] else " (GAP)")
            for s in a["sources"]) or "—"
        lines.append(
            f"| {a['title']} | {a['obligation']} | {_mark(a['coverage'])} | {srcs} |")
    lines += ["", "## Article detail", ""]
    for a in evidence["articles"]:
        lines.append(f"### {a['title']}")
        lines.append("")
        lines.append(f"- **Coverage:** {_mark(a['coverage'])}")
        lines.append(f"- **Obligation:** {a['obligation']}")
        if a["sources"]:
            lines.append("- **Evidence artifacts:**")
            for s in a["sources"]:
                if s["present"]:
                    lines.append(
                        f"  - `{s['artifact']}` — `{s['path']}` "
                        f"(sha256 `{s['sha256'][:12]}…`)")
                else:
                    lines.append(f"  - `{s['artifact']}` — **GAP** (not found)")
        if a.get("remediation"):
            lines.append(
                f"- **Remediation:** {a['remediation']} "
                f"See `{a['remediation_skill']}`.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_fria(evidence) -> str:
    art27 = next((a for a in evidence["articles"] if a["id"] == "art-27-fria"), None)
    lines = [
        "# Fundamental Rights Impact Assessment — scaffold (EU AI Act, Article 27)",
        "",
        f"_Generated {evidence['generated_at']} · "
        f"{evidence['generator']} v{evidence['generator_version']}._",
        "",
        f"> {evidence['disclaimer']}",
        "",
        "This is a **template**. The tool cannot infer fundamental-rights "
        "impact; a qualified reviewer must complete every section before the "
        "system is put into use.",
        "",
        "## 1. System description and deployment context",
        "_Describe the intended purpose, deployer, and the context of use._",
        "",
        "## 2. Categories of natural persons and groups affected",
        "_Who is affected, including vulnerable groups._",
        "",
        "## 3. Reasonably foreseeable risks to fundamental rights",
        "_Enumerate risks and their likelihood / severity._",
        "",
        "## 4. Human-oversight measures",
        "_How Article 14 oversight mitigates the risks above "
        "(link to hitl-audit evidence)._",
        "",
        "## 5. Measures on risk materialisation and governance",
        "_Complaint handling, escalation, and periodic review._",
        "",
    ]
    if art27:
        lines.append(f"_Coverage state: {_mark(art27['coverage'])}._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Emit an EU AI Act evidence pack from repo artifacts.")
    ap.add_argument("--root", default=".", help="Repository root to scan.")
    ap.add_argument("--out", default="docs/compliance",
                    help="Output directory for the evidence pack.")
    ap.add_argument("--check", action="store_true",
                    help="Exit 3 if a load-bearing article (Art 11/12/15) is a gap.")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: root not a directory: {root}", file=sys.stderr)
        return 2

    evidence, articles = assess(root)
    sources = discover(root)
    out = Path(args.out)
    try:
        out.mkdir(parents=True, exist_ok=True)
        (out / "ai-act-evidence.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        (out / "annex-iv-technical-file.md").write_text(
            render_annex_iv(evidence, sources), encoding="utf-8")
        (out / "fria-scaffold.md").write_text(
            render_fria(evidence), encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write to {out}: {exc}", file=sys.stderr)
        return 2

    s = evidence["summary"]
    print(f"ai-act-evidence: {s['covered']} covered · {s['partial']} partial · "
          f"{s['gap']} gap · {s['scaffold']} scaffold "
          f"(of {s['articles_total']} articles) -> {out}")

    if args.check:
        gaps = [a["id"] for a in articles
                if a["id"] in MUST_HAVE and a["coverage"] == "gap"]
        if gaps:
            print("check: load-bearing article gap(s): " + ", ".join(gaps),
                  file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
