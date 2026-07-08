"""Unit tests for the EU AI Act evidence-pack aggregator (ai_act_evidence.py).

stdlib only; bare ``test_`` functions + ``assert`` (matches sibling tests).
The aggregator is offline + deterministic: it maps artifacts the customer
already produces onto EU AI Act articles and emits a tenant-local evidence
pack. These tests pin the honesty contract (no false ``covered``), the
article-mapping rules, determinism, provenance, and the CLI.
"""
import importlib.util
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "ai_act_evidence", ROOT / "scripts" / "ai_act_evidence.py"
)
m = importlib.util.module_from_spec(_spec)
sys.modules["ai_act_evidence"] = m
_spec.loader.exec_module(m)

FIXED_NOW = "2026-07-07T00:00:00Z"


# ---------------------------------------------------------------- fixtures
def _write_repo(**files: str) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp())
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _scorecard(**pillar_status) -> str:
    """A production-readiness-manifest.json with the given pillar statuses.

    Keyword keys use underscores for dashless ids; dashed ids (``hitl-audit``)
    are passed via a trailing ``_`` translation the caller does with **{}.
    """
    pillars = [
        {"pillar": pid.replace("_", "-"),
         "title": pid,
         "status_with_waivers": status,
         "status_raw": status,
         "findings": []}
        for pid, status in pillar_status.items()
    ]
    return json.dumps({
        "schema_version": "1.0",
        "tool": "threadlight-production-ready",
        "tool_version": "0.8.0",
        "posture": "standard-ai-gateway",
        "score": {"raw_percent": 90, "with_waivers_percent": 90},
        "would_fail_hard_gate": False,
        "pillars": pillars,
    })


def _mcp_sbom() -> str:
    return json.dumps({
        "schema_version": "1.0",
        "generator": "threadlight-production-ready/mcp_sbom",
        "generator_version": "0.6.0",
        "servers": [{"id": "srv", "version": "1.2.3"}],
        "summary": {"server_count": 1, "must_fix": 0, "should_fix": 0},
    })


def _agent_identity(subject_count=2, owned=2) -> str:
    return json.dumps({
        "schema": "threadlight.agent-identity/v1",
        "generator": "threadlight-production-ready/agent_identity",
        "generator_version": "0.7.0",
        "subjects": [{"id": f"s{i}"} for i in range(subject_count)],
        "summary": {"subject_count": subject_count, "passwordless": subject_count,
                    "owned": owned, "over_privileged": 0,
                    "must_fix": 0, "should_fix": 0},
    })


def _govern() -> str:
    return json.dumps({"schema_version": "1.0", "policy": "agt-policy",
                       "owasp_asi": "2026", "findings": []})


def _evals() -> str:
    return json.dumps({"schema_version": "1.0", "scenarios": [{"id": "acc"}]})


def _redteam() -> str:
    return json.dumps({"schema_version": "1.0", "attacks": [{"id": "jb"}]})


def _full_repo() -> pathlib.Path:
    return _write_repo(**{
        "tests/production-readiness-manifest.json": _scorecard(**{
            "agent-governance": "green", "observability": "green",
            "hitl-audit": "green", "supply-chain": "green"}),
        "mcp-sbom.json": _mcp_sbom(),
        "agent-identity.json": _agent_identity(2, 2),
        "govern-manifest.json": _govern(),
        "specs/evals-manifest.json": _evals(),
        "specs/redteam-manifest.json": _redteam(),
    })


def _articles_by_id(articles):
    return {a["id"]: a for a in articles}


# ---------------------------------------------------------------- basics
def test_version():
    assert m.EVIDENCE_VERSION == "0.8.0"


def test_schema_and_generator():
    _, articles = m.assess(_full_repo(), now=FIXED_NOW)
    ev = m.build_evidence(articles, m.discover(_full_repo()), now=FIXED_NOW)
    assert ev["schema"] == "threadlight.ai-act-evidence/v1"
    assert ev["generator"] == "threadlight-production-ready/ai_act_evidence"
    assert ev["generator_version"] == "0.8.0"
    assert ev["tenant_local"] is True
    assert ev["generated_at"] == FIXED_NOW


# ---------------------------------------------------------------- discover
def test_discover_finds_artifacts():
    root = _full_repo()
    src = m.discover(root)
    assert src["mcp_sbom"]["present"] is True
    assert src["mcp_sbom"]["sha256"]
    assert src["agent_identity"]["present"] is True
    assert src["scorecard"]["present"] is True


def test_discover_missing_is_absent():
    src = m.discover(_write_repo())
    for key in ("scorecard", "mcp_sbom", "agent_identity", "govern",
                "evals", "redteam"):
        assert src[key]["present"] is False
        assert src[key]["sha256"] is None


def test_discover_malformed_is_absent():
    root = _write_repo(**{"mcp-sbom.json": "{ this is not json"})
    src = m.discover(root)
    assert src["mcp_sbom"]["present"] is False


# ---------------------------------------------------------------- article map
def test_art11_covered_with_scorecard_and_sbom():
    _, arts = m.assess(_full_repo(), now=FIXED_NOW)
    assert _articles_by_id(arts)["art-11-annex-iv"]["coverage"] == "covered"


def test_art11_partial_without_sbom():
    root = _write_repo(**{
        "tests/production-readiness-manifest.json": _scorecard(**{"agent-governance": "green"})})
    _, arts = m.assess(root, now=FIXED_NOW)
    assert _articles_by_id(arts)["art-11-annex-iv"]["coverage"] == "partial"


def test_art11_gap_without_scorecard():
    _, arts = m.assess(_write_repo(), now=FIXED_NOW)
    assert _articles_by_id(arts)["art-11-annex-iv"]["coverage"] == "gap"


def test_art12_covered_needs_observability_and_identity():
    root = _write_repo(**{
        "tests/production-readiness-manifest.json": _scorecard(**{"observability": "green"}),
        "agent-identity.json": _agent_identity(1, 1)})
    _, arts = m.assess(root, now=FIXED_NOW)
    assert _articles_by_id(arts)["art-12-records"]["coverage"] == "covered"


def test_art12_partial_with_only_one_signal():
    root = _write_repo(**{
        "tests/production-readiness-manifest.json": _scorecard(**{"observability": "green"})})
    _, arts = m.assess(root, now=FIXED_NOW)
    assert _articles_by_id(arts)["art-12-records"]["coverage"] == "partial"


def test_art14_from_hitl_pillar_status():
    for status, expect in (("green", "covered"), ("amber", "partial"), ("red", "gap")):
        root = _write_repo(**{
            "tests/production-readiness-manifest.json": _scorecard(**{"hitl-audit": status})})
        _, arts = m.assess(root, now=FIXED_NOW)
        assert _articles_by_id(arts)["art-14-oversight"]["coverage"] == expect


def test_art15_partial_evals_only():
    root = _write_repo(**{"specs/evals-manifest.json": _evals()})
    _, arts = m.assess(root, now=FIXED_NOW)
    assert _articles_by_id(arts)["art-15-accuracy-robustness"]["coverage"] == "partial"


def test_art15_covered_with_both():
    root = _write_repo(**{
        "specs/evals-manifest.json": _evals(),
        "specs/redteam-manifest.json": _redteam()})
    _, arts = m.assess(root, now=FIXED_NOW)
    assert _articles_by_id(arts)["art-15-accuracy-robustness"]["coverage"] == "covered"


def test_art26_owned_all_vs_some():
    root_all = _write_repo(**{"agent-identity.json": _agent_identity(3, 3)})
    _, arts = m.assess(root_all, now=FIXED_NOW)
    assert _articles_by_id(arts)["art-26-deployer"]["coverage"] == "covered"
    root_some = _write_repo(**{"agent-identity.json": _agent_identity(3, 1)})
    _, arts2 = m.assess(root_some, now=FIXED_NOW)
    assert _articles_by_id(arts2)["art-26-deployer"]["coverage"] == "partial"


def test_art27_always_scaffold():
    _, arts = m.assess(_write_repo(), now=FIXED_NOW)
    assert _articles_by_id(arts)["art-27-fria"]["coverage"] == "scaffold"


# ---------------------------------------------------------------- honesty
def test_no_false_covered_on_empty_repo():
    _, arts = m.assess(_write_repo(), now=FIXED_NOW)
    assert not any(a["coverage"] == "covered" for a in arts)


def test_disclaimer_present_everywhere():
    root = _full_repo()
    ev, arts = m.assess(root, now=FIXED_NOW)
    ev = m.build_evidence(arts, m.discover(root), now=FIXED_NOW)
    assert ev["disclaimer"]
    assert "legal advice" in m.render_annex_iv(ev, m.discover(root)).lower()
    assert "legal advice" in m.render_fria(ev).lower()


def test_provenance_sha256_on_present_sources():
    root = _full_repo()
    _, arts = m.assess(root, now=FIXED_NOW)
    art11 = _articles_by_id(arts)["art-11-annex-iv"]
    present = [s for s in art11["sources"] if s.get("present")]
    assert present and all(s["sha256"] for s in present)


# ---------------------------------------------------------------- manifest
def test_summary_counts_match_articles():
    root = _full_repo()
    _, arts = m.assess(root, now=FIXED_NOW)
    ev = m.build_evidence(arts, m.discover(root), now=FIXED_NOW)
    s = ev["summary"]
    assert s["articles_total"] == len(arts)
    tally = s["covered"] + s["partial"] + s["gap"] + s["scaffold"] + s["not_applicable"]
    assert tally == len(arts)


def test_determinism():
    root = _full_repo()
    a = m.build_evidence(*_assess_pair(root))
    b = m.build_evidence(*_assess_pair(root))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _assess_pair(root):
    _, arts = m.assess(root, now=FIXED_NOW)
    return arts, m.discover(root)


# ---------------------------------------------------------------- renderers
def test_render_annex_iv_sections_and_gap_marker():
    root = _write_repo(**{
        "tests/production-readiness-manifest.json": _scorecard(**{"agent-governance": "green"})})
    ev, arts = m.assess(root, now=FIXED_NOW)
    ev = m.build_evidence(arts, m.discover(root), now=FIXED_NOW)
    out = m.render_annex_iv(ev, m.discover(root))
    assert "Annex IV" in out
    assert "Article 11" in out or "Art 11" in out
    assert "GAP" in out  # at least one absent source flagged


def test_render_fria_scaffold_markers():
    ev, arts = m.assess(_write_repo(), now=FIXED_NOW)
    ev = m.build_evidence(arts, m.discover(_write_repo()), now=FIXED_NOW)
    out = m.render_fria(ev)
    assert "Article 27" in out or "Fundamental Rights" in out


# ---------------------------------------------------------------- CLI
def test_main_emits_three_files():
    root = _full_repo()
    out = pathlib.Path(tempfile.mkdtemp()) / "compliance"
    rc = m.main(["--root", str(root), "--out", str(out)])
    assert rc == 0
    assert (out / "ai-act-evidence.json").is_file()
    assert (out / "annex-iv-technical-file.md").is_file()
    assert (out / "fria-scaffold.md").is_file()


def test_main_check_exits_3_on_must_have_gap():
    root = _write_repo()
    out = pathlib.Path(tempfile.mkdtemp()) / "c"
    rc = m.main(["--root", str(root), "--out", str(out), "--check"])
    assert rc == 3


def test_main_check_exits_0_when_covered():
    root = _full_repo()
    out = pathlib.Path(tempfile.mkdtemp()) / "c"
    rc = m.main(["--root", str(root), "--out", str(out), "--check"])
    assert rc == 0


# ---------------------------------------------------------------- robustness
def test_wrongshape_scorecard_degrades_not_crashes():
    # valid JSON, wrong shape: pillars is null / a scalar / a string / a dict.
    for bad in ("null", "5", "true", '"notalist"', '{"pillars": null}',
                '{"pillars": 5}', '{"pillars": {"observability": "green"}}'):
        root = _write_repo(**{"tests/production-readiness-manifest.json":
                              bad if bad.startswith("{") else '{"pillars": %s}' % bad})
        ev, arts = m.assess(root, now=FIXED_NOW)  # must not raise
        by = _articles_by_id(arts)
        assert by["art-12-records"]["coverage"] != "covered"
        assert by["art-14-oversight"]["coverage"] != "covered"


def test_wrongshape_identity_degrades_not_crashes():
    for bad in ("[1,2,3]", '{"summary": null}', '{"summary": [1,2]}',
                '{"summary": "x"}', '{"summary": {"subject_count": "2", "owned": "2"}}'):
        root = _write_repo(**{"agent-identity.json": bad})
        _, arts = m.assess(root, now=FIXED_NOW)  # must not raise
        assert _articles_by_id(arts)["art-26-deployer"]["coverage"] != "covered"


def test_empty_stub_is_not_covered():
    # Parseable but content-free artifacts must never grade `covered`.
    for stub in ("{}", "[]"):
        root = _write_repo(**{
            "tests/production-readiness-manifest.json": stub,
            "mcp-sbom.json": stub,
            "specs/evals-manifest.json": stub,
            "specs/redteam-manifest.json": stub})
        _, arts = m.assess(root, now=FIXED_NOW)
        by = _articles_by_id(arts)
        assert by["art-11-annex-iv"]["coverage"] != "covered"
        assert by["art-15-accuracy-robustness"]["coverage"] != "covered"


def test_main_survives_wrongshape_and_writes_files():
    root = _write_repo(**{
        "tests/production-readiness-manifest.json": '{"pillars": null}',
        "agent-identity.json": "[1,2,3]"})
    out = pathlib.Path(tempfile.mkdtemp()) / "c"
    rc = m.main(["--root", str(root), "--out", str(out)])  # must not raise
    assert rc == 0
    assert (out / "ai-act-evidence.json").is_file()
