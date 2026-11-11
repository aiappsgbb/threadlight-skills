"""Customer-handoff compliance evidence bundle (compliance_handoff.py).

The bundle maps evidence the pilot already produced (readiness scorecard, safe-check,
Foundry eval run, red-team, identity, governance validation) to EU AI Act obligations
and to the Microsoft vs customer split for a PaaS agent. It must never claim
compliance: missing evidence shows as an open item, and applicability depends on a
classification only the customer's reviewer can record.
"""
import importlib.util
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("compliance_handoff", ROOT / "scripts" / "compliance_handoff.py")
m = importlib.util.module_from_spec(_spec)
sys.modules["compliance_handoff"] = m
_spec.loader.exec_module(m)

NOW = "2026-11-11T00:00:00Z"
EURLEX_ACT = "https://eur-lex.europa.eu/eli/reg/2024/1689/oj"
EURLEX_OMNIBUS = "https://eur-lex.europa.eu/eli/reg/2026/1744/oj"
MS_AGENT_SPLIT = "https://learn.microsoft.com/azure/security/fundamentals/shared-responsibility-ai-agent"
MS_TN = "https://learn.microsoft.com/azure/foundry/responsible-ai/agents/transparency-note"


def _repo(**files):
    root = pathlib.Path(tempfile.mkdtemp())
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return root


def _scorecard(**status):
    return {"schema_version": "1.0", "pillars": [
        {"pillar": k.replace("_", "-"), "status_with_waivers": v, "status_raw": v} for k, v in status.items()]}


def _by_id(bundle):
    return {o["id"]: o for o in bundle["obligations"]}


def _all_text(out: pathlib.Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(out.glob("customer-handoff.*")))


def test_empty_pilot_shows_every_obligation_and_question_as_open():
    root = _repo()
    bundle = m.build(root, now=NOW)
    assert bundle["obligations"], "no obligations mapped"
    assert {o["state"] for o in bundle["obligations"]} == {"open"}
    assert all(o["open_item"] for o in bundle["obligations"])
    assert {q["state"] for q in bundle["classification"]} == {"open"}
    assert bundle["summary"]["evidence_present"] == 0


def test_bundle_cites_regulation_amendment_and_microsoft_docs():
    root = _repo()
    out = root / "docs" / "compliance"
    assert m.main(["--root", str(root), "--out", str(out), "--now", NOW]) == 0
    text = _all_text(out)
    for url in (EURLEX_ACT, EURLEX_OMNIBUS, MS_AGENT_SPLIT, MS_TN):
        assert url in text, url
    for fact in ("2 December 2027", "2 August 2028", "Article 50", "Article 26(6)", "six months"):
        assert fact in text, fact
    # The underlying article map ships in the same bundle.
    for name in ("ai-act-evidence.json", "annex-iv-technical-file.md", "fria-scaffold.md"):
        assert (out / name).is_file(), name


def test_bundle_never_claims_compliance():
    root = _repo(**{
        "tests/production-readiness-manifest.json": _scorecard(hitl_audit="green", observability="green"),
        "agent-identity.json": {"summary": {"subject_count": 1, "owned": 1}},
        "specs/evals-manifest.json": {"runs": [1]},
        "specs/redteam-manifest.json": {"runs": [1]},
    })
    out = root / "out"
    m.main(["--root", str(root), "--out", str(out), "--now", NOW])
    text = _all_text(out)
    assert not re.search(r"\bcompliant\b|\bcertif(y|ied)\b(?! compliance)", text, re.I), \
        re.findall(r".{0,60}(?:compliant|certif).{0,60}", text, re.I)
    assert "COVERED" not in text and '"covered"' not in text
    assert "does not state or certify compliance" in text


def test_existing_evidence_is_mapped_with_its_source():
    root = _repo(**{
        "tests/production-readiness-manifest.json": _scorecard(hitl_audit="green", observability="green"),
        "agent-identity.json": {"summary": {"subject_count": 1, "owned": 1}},
        "specs/evals-manifest.json": {"runs": [1]},
        "specs/redteam-manifest.json": {"runs": [1]},
        "tests/postdeploy-manifest.json": {"phase": "post-deploy", "gaps": [], "checked_at": "2026-11-10T09:00:00Z"},
    })
    ob = _by_id(m.build(root, now=NOW))
    assert ob["human-oversight"]["state"] == "evidence-present"
    assert ob["logging"]["state"] == "evidence-present"
    assert ob["accuracy-robustness"]["state"] == "evidence-present"
    paths = {e["path"] for e in ob["accuracy-robustness"]["evidence"] if e["present"]}
    assert {"specs/evals-manifest.json", "specs/redteam-manifest.json", "tests/postdeploy-manifest.json"} <= paths
    # Log retention under deployer control is never inferable offline.
    assert ob["log-retention"]["state"] == "open"
    # Transparency to people interacting with the agent needs a recorded answer.
    assert ob["transparency-interaction"]["state"] == "open"


def test_malformed_evidence_is_open_not_present():
    root = _repo(**{"specs/evals-manifest.json": "{not json", "tests/postdeploy-manifest.json": "[]"})
    ob = _by_id(m.build(root, now=NOW))
    assert ob["accuracy-robustness"]["state"] == "open"


def test_reviewer_classification_answers_are_echoed_not_inferred():
    answers = {
        "schema": "threadlight.ai-act-classification/v1",
        "reviewed_by": "Customer DPO office", "reviewed_at": "2026-11-01",
        "answers": {"role": "provider-and-deployer", "annex_iii_use_case": "none",
                    "interacts_with_natural_persons": "yes",
                    "ai_disclosure_location": "Greeting text in the case workspace"},
    }
    root = _repo(**{"specs/ai-act-classification.json": answers})
    bundle = m.build(root, now=NOW)
    qs = {q["id"]: q for q in bundle["classification"]}
    assert qs["role"]["state"] == "recorded" and qs["role"]["answer"] == "provider-and-deployer"
    assert qs["role"]["recorded_by"] == "Customer DPO office"
    assert qs["prohibited-practices"]["state"] == "open"  # unanswered stays open
    ob = _by_id(bundle)
    assert ob["transparency-interaction"]["state"] == "recorded-by-reviewer"
    # A recorded "not high-risk" answer is shown, never turned into a verdict.
    assert "reviewer" in ob["human-oversight"]["applicability"].lower()


def test_voice_front_end_adds_synthetic_audio_item():
    root = _repo(**{"specs/SPEC.md": "## 11e\n```yaml\nvoice_frontend: hosted-voice\n```\n"})
    ob = _by_id(m.build(root, now=NOW))
    assert "synthetic-content" in ob
    assert ob["synthetic-content"]["state"] == "open"
    root2 = _repo(**{"specs/SPEC.md": "voice_frontend: none\n"})
    assert "synthetic-content" not in _by_id(m.build(root2, now=NOW))


def test_shared_responsibility_follows_microsoft_paas_agent_matrix():
    split = {r["area"]: r for r in m.build(_repo(), now=NOW)["shared_responsibility"]}
    assert split["Human-in-the-loop approval for high-impact actions"]["paas"] == "C"
    assert split["Agent runtime and orchestrator platform"]["paas"] == "M"
    assert split["Action audit logging and monitoring"]["paas"] == "S"
    assert all(r["source"] == MS_AGENT_SPLIT for r in split.values())


def test_output_is_deterministic_for_same_inputs_and_time():
    root = _repo(**{"tests/production-readiness-manifest.json": _scorecard(hitl_audit="amber")})
    assert json.dumps(m.build(root, now=NOW), sort_keys=True) == json.dumps(m.build(root, now=NOW), sort_keys=True)


def test_unusable_root_exits_2():
    assert m.main(["--root", "/nonexistent/threadlight", "--now", NOW]) == 2
