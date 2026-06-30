from __future__ import annotations
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import report  # noqa: E402


def _findings():
    return [
        {"id": "F-1-001", "category": "dependency", "severity": "high", "count": 3,
         "phase": "install", "evidence": {"line": 2, "excerpt": "ResolutionImpossible: agent-framework"}},
        {"id": "F-1-002", "category": "rate_limit", "severity": "medium", "count": 7,
         "phase": "invoke", "evidence": {"line": 9, "excerpt": "exceeded rate limit"}},
    ]


def test_build_digest_shape_and_summary():
    d = report.build_digest(
        run_id=1, conclusion="failure", branch="main", title="e2e",
        window={"start": "2026-06-30T10:21:05Z", "end": "2026-06-30T11:05:04Z"},
        phase_parity={"install": "failure", "design": "skipped"},
        legs={"govern": {}, "evals": {}, "redteam": {}},
        findings=_findings())
    assert d["schema"] == "threadlight-router-learnings/v1"
    assert d["run_id"] == 1 and d["conclusion"] == "failure"
    assert d["summary"] == {"high": 1, "medium": 1, "low": 0, "total": 2}
    assert d["phase_parity"]["install"] == "failure"


def test_green_run_with_no_findings_summarizes_zero():
    d = report.build_digest(
        run_id=2, conclusion="success", branch="main", title="e2e",
        window={"start": "a", "end": "b"},
        phase_parity={"design": "success"}, legs={}, findings=[])
    assert d["summary"]["total"] == 0
    assert d["conclusion"] == "success"


def test_render_markdown_contains_key_sections():
    d = report.build_digest(
        run_id=42, conclusion="failure", branch="topic", title="router e2e",
        window={"start": "a", "end": "b"},
        phase_parity={"install": "failure"}, legs={"redteam": {"verdict": "vulnerable"}},
        findings=_findings())
    md = report.render_markdown(d)
    assert "# Router-bench learnings — run 42" in md
    assert "dependency" in md and "rate_limit" in md
    assert "F-1-001" in md
    # severity-sorted: high (dependency) before medium (rate_limit)
    assert md.index("dependency") < md.index("rate_limit")


def test_render_markdown_green_run_states_clean():
    d = report.build_digest(run_id=7, conclusion="success", branch="m", title="t",
                            window={"start": "a", "end": "b"}, phase_parity={},
                            legs={}, findings=[])
    md = report.render_markdown(d)
    assert "No high-severity findings" in md or "clean" in md.lower()
