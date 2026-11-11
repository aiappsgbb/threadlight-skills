#!/usr/bin/env python3
"""Customer-handoff compliance evidence bundle for a Threadlight pilot.

Assembles evidence the pilot already produced into one bundle a customer's legal,
risk and compliance reviewers can work from:

* EU AI Act classification questions (role, prohibited practices, high-risk tests,
  interaction with people) - open until the customer's reviewer records an answer
  in ``specs/ai-act-classification.json``;
* obligations relevant to providers and deployers (risk management, technical file,
  logging and log retention, transparency, human oversight, accuracy and robustness,
  deployer duties, FRIA, AI literacy), each with the evidence found, its source path
  and SHA-256, and an open item when evidence is missing;
* the Microsoft vs customer split for a PaaS agent (Microsoft Foundry Agent Service),
  from Microsoft's AI agent shared responsibility model.

It reuses ``ai_act_evidence.py`` (article map, governance validation, identity,
SBOM, evals, red team) and ``pilot_value.py`` (Foundry eval run, showcase scenarios,
safe-check post-deploy result) instead of re-reading those sources its own way.

Offline, read-only on its inputs, never calls Azure. It presents evidence and open
items; it does not state or certify compliance and is not legal advice.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "threadlight.compliance-handoff/v1"
GENERATOR = "threadlight-production-ready/compliance_handoff"
HERE = Path(__file__).resolve().parent

EURLEX_ACT = "https://eur-lex.europa.eu/eli/reg/2024/1689/oj"
EURLEX_OMNIBUS = "https://eur-lex.europa.eu/eli/reg/2026/1744/oj"
MS_AGENT_SPLIT = "https://learn.microsoft.com/azure/security/fundamentals/shared-responsibility-ai-agent"
MS_AI_SPLIT = "https://learn.microsoft.com/azure/security/fundamentals/shared-responsibility-ai"
MS_TN = "https://learn.microsoft.com/azure/foundry/responsible-ai/agents/transparency-note"
MS_PURVIEW = "https://learn.microsoft.com/purview/compliance-manager-assessments#assessments-for-ai-regulations"

DISCLAIMER = (
    "This bundle presents evidence the pilot produced and the items still open. It does "
    "not state or certify compliance with Regulation (EU) 2024/1689 and is not legal "
    "advice. Whether the system is in scope, its risk class and the customer's role "
    "(provider, deployer or both) are decided by the customer's qualified reviewer."
)

LEGAL_BASIS = (
    f"Regulation (EU) 2024/1689 (Artificial Intelligence Act, {EURLEX_ACT}) as amended by "
    f"Regulation (EU) 2026/1744 (Digital Omnibus on AI, {EURLEX_OMNIBUS}). After the "
    "amendment, the high-risk requirements and obligations in Chapter III, Sections 1 to 3 "
    "(Articles 6 to 27, except Article 6(5)) apply from 2 December 2027 for Annex III systems and from "
    "2 August 2028 for Annex I systems; Article 50 transparency applies from the general "
    "date of 2 August 2026; Articles 4 and 5 apply since 2 February 2025."
)

CLASSIFICATION_FILE = "specs/ai-act-classification.json"
SAFE_CHECK_FILE = "tests/postdeploy-manifest.json"
INSTRUCTIONS_FILE = "docs/instructions-for-use.md"
SPEC_FILE = "specs/SPEC.md"

HIGH_RISK_DATES = ("Applies if the system is high-risk (Article 6): from 2 December 2027 for "
                   "Annex III systems, 2 August 2028 for Annex I systems.")

# id, question, legal reference, answer key in specs/ai-act-classification.json
QUESTIONS = (
    ("ai-system", "Is the agent an AI system as defined in Article 3(1)?", "Article 3(1)", "ai_system"),
    ("role", "Is the customer the provider (Article 3(3)), the deployer (Article 3(4)) or both? "
     "Building the agent and putting it into service under its own name points to provider.",
     "Article 3(3), 3(4)", "role"),
    ("prohibited-practices", "Has the use case been screened against the prohibited practices "
     "in Article 5?", "Article 5", "prohibited_practices_screened"),
    ("annex-i", "Is the agent a safety component of, or itself, a product under the Union "
     "harmonisation legislation in Annex I?", "Article 6(1), Annex I", "annex_i_safety_component"),
    ("annex-iii", "Does the use case fall under an Annex III area? Name the point, or 'none'.",
     "Article 6(2), Annex III", "annex_iii_use_case"),
    ("derogation", "If Annex III applies, was the Article 6(3) assessment documented before "
     "putting it into service (Article 6(4))?", "Article 6(3), 6(4)", "art_6_3_assessment"),
    ("profiling", "Does the agent profile natural persons? An Annex III system that profiles is "
     "always high-risk.", "Article 6(3)", "profiling_natural_persons"),
    ("interaction", "Does the agent interact directly with natural persons?", "Article 50(1)",
     "interacts_with_natural_persons"),
    ("fria-scope", "Is the deployer a body governed by public law, a private entity providing "
     "public services, or using an Annex III point 5(b) or 5(c) system?", "Article 27(1)",
     "fria_required"),
)

# Microsoft AI agent shared responsibility model, PaaS agent column
# (Microsoft Foundry Agent Service). C = customer, M = Microsoft, S = shared.
SPLIT = (
    ("Customer data (including grounding and memory contents)", "C", "Data sources in SPEC § 5 and § 7"),
    ("Identities and users", "C", "agent-identity.json"),
    ("Access management (RBAC, MFA, Conditional Access)", "C", "Readiness identity pillar"),
    ("Client devices and endpoints", "C", "Not assessed by Threadlight"),
    ("Base model hosting and weights", "M", "Microsoft Foundry model deployment"),
    ("Model input/output content safety", "S", "Foundry guardrails plus red-team manifest"),
    ("Physical infrastructure (hosts, network, datacenter)", "M", "Microsoft"),
    ("Agent instructions, system prompt, and scope", "C", "AGENTS.md and skills generated from the SPEC"),
    ("Tool, plugin, and connector selection", "C", "SPEC § 6 tool contracts, mcp-sbom.json"),
    ("Per-tool permissions (least privilege)", "C", "SPEC § 11a governance contract"),
    ("Agent identity and delegated token management", "S", "agent-identity.json"),
    ("Per-action authorization checks", "S", "Governance binding evidence (specs/governance-manifest.json)"),
    ("Human-in-the-loop approval for high-impact actions", "C", "SPEC § 8, governance approvals, hitl-audit pillar"),
    ("Orchestration guardrails (loop, step, and cost limits)", "S", "Agent configuration; cost manifests"),
    ("Multi-agent trust-boundary controls", "S", "SPEC § 11e agent_topology"),
    ("Memory design, isolation, and poisoning defense", "S", "Readiness model-lifecycle pillar (thread retention)"),
    ("Tool and action sandboxing and egress control", "S", "Readiness network-posture pillar"),
    ("Action audit logging and monitoring", "S", "Readiness observability pillar, governance audit"),
    ("Agent runtime and orchestrator platform", "M", "Microsoft Foundry hosted agent runtime"),
    ("Acceptable-use policy and accountability for actions", "C", "Customer policy; not produced by Threadlight"),
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_tl_handoff_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(root: Path, rel: str) -> tuple[str | None, Any]:
    """Return (text, parsed JSON or None). Missing or unreadable -> (None, None)."""
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None
    try:
        return text, json.loads(text)
    except ValueError:
        return text, None


def _ref(root: Path, rel: str, *, present: bool | None = None, note: str = "") -> dict[str, Any]:
    text, _ = _read(root, rel)
    ok = text is not None if present is None else present
    return {"path": rel, "present": bool(ok), "sha256": _sha256(text) if ok and text is not None else None,
            "note": note}


def _answered(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in {"", "unknown", "open", "tbd"}


def _classification(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _, data = _read(root, CLASSIFICATION_FILE)
    data = data if isinstance(data, dict) else {}
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    by = data.get("reviewed_by") if _answered(data.get("reviewed_by")) else None
    at = data.get("reviewed_at") if _answered(data.get("reviewed_at")) else None
    out = []
    for qid, question, ref, key in QUESTIONS:
        value = answers.get(key)
        recorded = _answered(value) and by is not None
        out.append({
            "id": qid, "question": question, "reference": ref, "answer_key": key,
            "state": "recorded" if recorded else "open",
            "answer": value.strip() if recorded else None,
            "recorded_by": by if recorded else None, "recorded_at": at if recorded else None,
        })
    meta = {"answers": answers if by else {}, "reviewed_by": by, "reviewed_at": at,
            "source": _ref(root, CLASSIFICATION_FILE)}
    return meta, out


def _high_risk_applicability(meta: dict[str, Any]) -> str:
    a = meta["answers"]
    recorded = [f"{label} = {a[k]}" for k, label in (
        ("annex_i_safety_component", "Annex I safety component"),
        ("annex_iii_use_case", "Annex III use case"),
        ("art_6_3_assessment", "Article 6(3) assessment"),
        ("profiling_natural_persons", "profiling")) if _answered(a.get(k))]
    if recorded:
        return (f"{HIGH_RISK_DATES} Reviewer {meta['reviewed_by']} recorded: {'; '.join(recorded)}. "
                "The reviewer confirms whether this obligation applies.")
    return f"{HIGH_RISK_DATES} Classification not recorded: open."


def _safe_check(root: Path) -> tuple[str, dict[str, Any]]:
    text, data = _read(root, SAFE_CHECK_FILE)
    if not isinstance(data, dict):
        return "absent", _ref(root, SAFE_CHECK_FILE, present=False)
    if data.get("phase") == "post-deploy" and data.get("gaps") == [] and data.get("checked_at"):
        return "pass", _ref(root, SAFE_CHECK_FILE, note=f"post-deploy check passed {data['checked_at']}")
    return "partial", _ref(root, SAFE_CHECK_FILE, note="post-deploy check not run or has open gaps")


def _art_sources(art: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"path": s["path"] or s["artifact"], "present": s["present"], "sha256": s["sha256"],
             "note": f"{s['artifact']} via ai_act_evidence"} for s in art["sources"]]


_STATE = {"covered": "evidence-present", "partial": "partial", "gap": "open", "scaffold": "open"}


def _ob(oid, title, refs, who, applicability, state, evidence, open_item, deployer_action=""):
    return {"id": oid, "title": title, "references": refs, "who": who, "applicability": applicability,
            "state": state, "evidence": evidence, "open_item": open_item if state != "evidence-present" else "",
            "deployer_action": deployer_action}


def _obligations(root: Path, arts: dict[str, dict], value: dict[str, Any], meta: dict[str, Any],
                 spec: str) -> list[dict[str, Any]]:
    hr = _high_risk_applicability(meta)
    items = {i["id"]: i for s in value["sections"] for i in s["items"]}
    obs = []

    a9 = arts["art-9-risk-management"]
    obs.append(_ob("risk-management", "Risk management system", ["Article 9"], "provider", hr,
                   _STATE[a9["coverage"]], _art_sources(a9),
                   "Live governance binding evidence (pass, live) is missing or stale; it covers the bound "
                   "tools only, not the whole agent. Run threadlight-govern and the deployed collector."))

    a11 = arts["art-11-annex-iv"]
    obs.append(_ob("technical-documentation", "Technical documentation", ["Article 11", "Annex IV"], "provider", hr,
                   _STATE[a11["coverage"]], _art_sources(a11),
                   "Readiness scorecard or MCP SBOM missing. Run threadlight-production-ready and mcp_sbom.py."))

    a12 = arts["art-12-records"]
    obs.append(_ob("logging", "Automatic recording of events (logs)", ["Article 12"], "provider", hr,
                   _STATE[a12["coverage"]], _art_sources(a12),
                   "Observability pillar not green or agent identity missing; logs must tie events to the agent."))

    obs.append(_ob("log-retention", "Deployer keeps logs for at least six months", ["Article 26(6)"], "deployer", hr,
                   "open", [_ref(root, "tests/production-readiness-manifest.json",
                                 note="the scorecard does not read workspace retention")],
                   "Record the Log Analytics / Application Insights retention of the agent's logs (at least six "
                   "months unless other law applies) and who controls it. Not inferable offline."))

    instr = (root / INSTRUCTIONS_FILE).is_file()
    obs.append(_ob("transparency-deployers", "Instructions for use for deployers", ["Article 13"], "provider", hr,
                   "partial" if instr else "open",
                   [_ref(root, INSTRUCTIONS_FILE, note="content not checked by this tool"),
                    {"path": MS_TN, "present": True, "sha256": None,
                     "note": "Microsoft Transparency Note: platform capabilities and limits, not the customer's agent"}],
                   f"Write {INSTRUCTIONS_FILE}: intended purpose, accuracy metrics from the eval run, known "
                   "limits, human oversight measures and log handling, then have the reviewer check it."))

    a14 = arts["art-14-oversight"]
    recommend_only = '"action_mode": "recommend-only"' in spec or "action_mode: recommend-only" in spec
    has_s8 = bool(re.search(r"^##\s*8\.\s*Human Interaction", spec, re.M))
    state = _STATE[a14["coverage"]]
    if state == "open" and (recommend_only or has_s8):
        state = "partial"
    ev = _art_sources(a14) + [_ref(root, SPEC_FILE, present=bool(spec) and (recommend_only or has_s8),
                                   note="SPEC § 8 human interaction points"
                                   + ("; recommend-only: a person records every decision" if recommend_only else ""))]
    obs.append(_ob("human-oversight", "Human oversight", ["Article 14", "Article 26(2)"], "provider and deployer", hr,
                   state, ev, "hitl-audit pillar not green: show who approves, how a person overrides or stops "
                   "the agent, and the audit of each approval (threadlight-hitl-patterns).",
                   deployer_action="Name the people assigned to oversight, with the competence, training and "
                   "authority Article 26(2) requires."))

    a15 = arts["art-15-accuracy-robustness"]
    safe, safe_ref = _safe_check(root)
    eval_item = items.get("eval", {})
    foundry_eval = eval_item.get("kind") == "measured"
    ev = _art_sources(a15) + [safe_ref, {
        "path": eval_item.get("source") or "specs/foundry-package-manifest.json", "present": foundry_eval,
        "sha256": None, "note": f"Foundry eval run: {eval_item.get('value')}" if foundry_eval else "Foundry eval run not recorded"}]
    scen = items.get("scenarios") or next((s["item"] for s in value["answers"]
                                           if s["question"].startswith("Did the showcase")), {})
    ev.append({"path": "docs/invoke-results.md", "present": scen.get("kind") == "measured", "sha256": None,
               "note": f"Showcase scenarios: {scen.get('value')}"})
    if a15["coverage"] == "covered" and safe == "pass":
        state = "evidence-present"
    elif a15["coverage"] != "gap" or safe != "absent" or foundry_eval:
        state = "partial"
    else:
        state = "open"
    obs.append(_ob("accuracy-robustness", "Accuracy, robustness and cybersecurity", ["Article 15"], "provider", hr,
                   state, ev, "Needs an eval manifest, a red-team manifest and a passed post-deploy safe-check "
                   "(threadlight-evals, threadlight-redteam, threadlight-safe-check). Their thresholds are "
                   "reviewed by the customer, not by this tool."))

    a26 = arts["art-26-deployer"]
    obs.append(_ob("deployer-owner", "Deployer operates as instructed, with a responsible owner",
                   ["Article 26(1)", "Article 26(5)"], "deployer", hr, _STATE[a26["coverage"]], _art_sources(a26),
                   "agent-identity.json missing or not every identity has an owner (agent_identity.py)."))

    obs.append(_ob("fria", "Fundamental rights impact assessment", ["Article 27"], "deployer",
                   f"{hr} Only for the deployers named in Article 27(1).", "open",
                   [{"path": "fria-scaffold.md", "present": True, "sha256": None, "note": "template only"}],
                   "A person completes fria-scaffold.md when Article 27(1) applies."))

    disclosure = meta["answers"].get("ai_disclosure_location")
    obs.append(_ob("transparency-interaction", "People are told they are interacting with an AI system",
                   ["Article 50(1)", "Article 50(5)"], "provider",
                   "Applies from 2 August 2026 to systems that interact directly with natural persons, high-risk "
                   "or not, unless it is obvious from the context.",
                   "recorded-by-reviewer" if _answered(disclosure) and meta["reviewed_by"] else "open",
                   [meta["source"] | {"note": f"disclosure: {disclosure}" if _answered(disclosure) else "no disclosure recorded"}],
                   f"Record in {CLASSIFICATION_FILE} (ai_disclosure_location) where users are told, at the "
                   "latest at the first interaction, that they talk to an AI system."))

    if re.search(r"voice_frontend:\s*hosted-voice|agent_type:\s*voice", spec):
        obs.append(_ob("synthetic-content", "Synthetic audio is marked as AI-generated",
                       ["Article 50(2)"], "provider",
                       "The SPEC selects a voice front-end that generates synthetic audio. Systems placed on the "
                       "market before 2 August 2026 have until 2 December 2026 (Regulation (EU) 2026/1744).",
                       "open", [_ref(root, SPEC_FILE, note="voice front-end selected")],
                       "The reviewer decides who is the provider of the audio output and how it is marked as "
                       "artificially generated; Threadlight does not verify audio marking."))

    literacy = meta["answers"].get("ai_literacy_measures")
    obs.append(_ob("ai-literacy", "AI literacy of staff operating the agent", ["Article 4"], "provider and deployer",
                   "Applies since 2 February 2025 (wording amended by Regulation (EU) 2026/1744).",
                   "recorded-by-reviewer" if _answered(literacy) and meta["reviewed_by"] else "open",
                   [meta["source"] | {"note": f"measures: {literacy}" if _answered(literacy) else "no measures recorded"}],
                   f"Record the training given to the operators and approvers ({CLASSIFICATION_FILE}, "
                   "ai_literacy_measures)."))
    return obs


def build(root, *, now: str | None = None) -> dict[str, Any]:
    root = Path(root)
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ai_act = _load("ai_act_evidence")
    pilot_value = _load("pilot_value")
    evidence, articles = ai_act.assess(root, now=now)
    arts = {a["id"]: a for a in articles}
    value = pilot_value.build_report(root, generated_at=now)
    spec, _ = _read(root, SPEC_FILE)
    meta, questions = _classification(root)
    obligations = _obligations(root, arts, value, meta, spec or "")
    counts = {k: sum(1 for o in obligations if o["state"] == k)
              for k in ("evidence-present", "partial", "recorded-by-reviewer", "open")}
    return {
        "schema": SCHEMA, "generator": GENERATOR, "generated_at": now,
        "pilot": value["pilot"], "disclaimer": DISCLAIMER, "legal_basis": LEGAL_BASIS,
        "classification_source": meta["source"], "classification": questions,
        "obligations": obligations,
        "shared_responsibility": [{"area": a, "paas": r, "threadlight_evidence": e, "source": MS_AGENT_SPLIT}
                                  for a, r, e in SPLIT],
        "microsoft_resources": [
            {"title": "AI agent shared responsibility model", "url": MS_AGENT_SPLIT},
            {"title": "AI shared responsibility model", "url": MS_AI_SPLIT},
            {"title": "Transparency Note for Foundry Agent Service", "url": MS_TN},
            {"title": "Microsoft Purview Compliance Manager: EU AI Act assessment template (premium)", "url": MS_PURVIEW},
        ],
        "article_map": "ai-act-evidence.json",
        "summary": {k.replace("-", "_"): v for k, v in counts.items()} | {"obligations_total": len(obligations)},
    }


_LABEL = {"evidence-present": "Evidence present (review)", "partial": "Partial evidence",
          "recorded-by-reviewer": "Recorded by reviewer", "open": "Open"}


def _md(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def render_markdown(b: dict[str, Any]) -> str:
    s = b["summary"]
    lines = [
        f"# EU AI Act evidence handoff: {b['pilot']['name']}", "",
        f"_Generated {b['generated_at']} by {b['generator']}._", "",
        f"> {b['disclaimer']}", "",
        f"**Legal basis.** {b['legal_basis']}", "",
        f"**Summary.** {s['obligations_total']} obligations: {s['evidence_present']} with evidence present for review, "
        f"{s['partial']} partial, {s['recorded_by_reviewer']} recorded by the reviewer, {s['open']} open.", "",
        "## 1. Classification questions for the customer's reviewer", "",
        f"Answers come only from `{CLASSIFICATION_FILE}`, written by the customer's reviewer "
        "(`reviewed_by`, `reviewed_at`, `answers`). Threadlight does not infer them.", "",
        "| Question | Reference | State | Answer |", "| --- | --- | --- | --- |",
    ]
    for q in b["classification"]:
        ans = f"{q['answer']} ({q['recorded_by']}, {q['recorded_at'] or 'date not recorded'})" if q["answer"] else "—"
        lines.append(f"| {_md(q['question'])} | {q['reference']} | {'Recorded' if q['state'] == 'recorded' else 'Open'} | {_md(ans)} |")
    lines += ["", "## 2. Obligations and the evidence found", "",
              "| Obligation | Articles | Who | State |", "| --- | --- | --- | --- |"]
    for o in b["obligations"]:
        lines.append(f"| {o['title']} | {', '.join(o['references'])} | {o['who']} | {_LABEL[o['state']]} |")
    for o in b["obligations"]:
        lines += ["", f"### {o['title']} ({', '.join(o['references'])})", "",
                  f"- **State:** {_LABEL[o['state']]}", f"- **Applicability:** {o['applicability']}"]
        lines.append("- **Evidence:**")
        for e in o["evidence"]:
            mark = "present" if e["present"] else "missing"
            sha = f", sha256 `{e['sha256'][:12]}…`" if e.get("sha256") else ""
            note = f": {e['note']}" if e.get("note") else ""
            lines.append(f"  - `{e['path']}` ({mark}{sha}){note}")
        if o["open_item"]:
            lines.append(f"- **Open item:** {o['open_item']}")
        if o["deployer_action"]:
            lines.append(f"- **Deployer action:** {o['deployer_action']}")
    lines += ["", "## 3. Microsoft and customer responsibilities (PaaS agent)", "",
              f"From Microsoft's [AI agent shared responsibility model]({MS_AGENT_SPLIT}), PaaS agent column "
              "(Microsoft Foundry Agent Service). C = customer, M = Microsoft, S = shared. Microsoft states the "
              "matrix is illustrative governance guidance, not a legal conclusion, and does not change any "
              "agreement.", "",
              "| Responsibility area | PaaS agent | Where Threadlight evidence sits |", "| --- | --- | --- |"]
    for r in b["shared_responsibility"]:
        lines.append(f"| {r['area']} | {r['paas']} | {r['threadlight_evidence']} |")
    lines += ["", "## 4. Open items", ""]
    open_items = [o for o in b["obligations"] if o["open_item"]]
    lines += [f"- **{o['title']}** ({', '.join(o['references'])}): {o['open_item']}" for o in open_items] or ["- None."]
    lines += [f"- **Classification:** {sum(1 for q in b['classification'] if q['state'] == 'open')} questions open."]
    lines += ["", "## 5. Related files and Microsoft resources", "",
              f"- `{b['article_map']}`, `annex-iv-technical-file.md`, `fria-scaffold.md`: the article map this "
              "bundle reuses. Its `covered` label means an inventory predicate was met, not that an obligation is satisfied."]
    lines += [f"- [{r['title']}]({r['url']})" for r in b["microsoft_resources"]]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write the EU AI Act customer-handoff evidence bundle.")
    ap.add_argument("--root", default=".", help="Pilot repository root.")
    ap.add_argument("--out", default="docs/compliance", help="Output directory (relative paths resolve against --root).")
    ap.add_argument("--now", default=None, help="Evaluation time (ISO 8601); defaults to the current UTC time.")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"error: root not a directory: {root}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out = out if out.is_absolute() else root / out
    bundle = build(root, now=args.now)
    ai_act = _load("ai_act_evidence")
    evidence, _ = ai_act.assess(root, now=bundle["generated_at"])
    sources = ai_act.discover(root)
    try:
        out.mkdir(parents=True, exist_ok=True)
        (out / "customer-handoff.json").write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        (out / "customer-handoff.md").write_text(render_markdown(bundle), encoding="utf-8")
        (out / "ai-act-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        (out / "annex-iv-technical-file.md").write_text(ai_act.render_annex_iv(evidence, sources), encoding="utf-8")
        (out / "fria-scaffold.md").write_text(ai_act.render_fria(evidence), encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write to {out}: {exc}", file=sys.stderr)
        return 2
    s = bundle["summary"]
    print(f"compliance-handoff: {s['evidence_present']} evidence present · {s['partial']} partial · "
          f"{s['recorded_by_reviewer']} recorded · {s['open']} open (of {s['obligations_total']}) -> {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
