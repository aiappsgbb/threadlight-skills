from __future__ import annotations
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent.parent / "references" / "fixtures"

import findings  # noqa: E402


def test_classify_real_signatures():
    # dependency drift (the agent-framework 1.4 conflict that forced the hotfix)
    assert findings.classify_line(
        "ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/...")[0] == "dependency"
    assert findings.classify_line(
        "Cannot install agent-framework-core==1.4.0 because these package versions "
        "have conflicting dependencies.")[0] == "dependency"
    # model + rate-limit cascade
    assert findings.classify_line(
        "Last error: CAPIError: Your requests to gpt-5.4-mini ... exceeded rate limit.")[0] == "rate_limit"
    assert findings.classify_line(
        "Request failed due to a transient API error. Retrying...")[0] == "model_unavailable"
    assert findings.classify_line(
        "HTTP 400 operation unsupported")[0] == "wire_protocol"
    assert findings.classify_line("a perfectly normal log line") is None


def test_protocol_contract_invoke_mismatch():
    # The Phase-4 terminal blocker: deploy declared `responses`, the pack asked for
    # `invocations`, so `azd ai agent invoke --protocol invocations` gets an HTTP 400.
    # This is NOT `wire_protocol` (no "unsupported"), so it needs its own rule —
    # otherwise the invoke-fail run scores as "0 findings / looks clean".
    cat, sev = findings.classify_line(
        "HTTP 400 bad_request: Invoke API requires the request to use the "
        "'invocations' protocol version '2.0.0'")
    assert cat == "protocol_contract"
    assert sev == "high"
    # the MAF/Responses mirror of the same contract bug
    assert findings.classify_line(
        "Bot gets 400: responses protocol not declared")[0] == "protocol_contract"


def test_skill_loader_is_high_severity():
    # A missing skill on a workflow-classified workload is universal + blocking
    # (all arms hit `Skill not found: threadlight-workflow` on fsi-kyc-aml), so it
    # is high, not medium.
    cat, sev = findings.classify_line(
        "\u2717 skill(threadlight-workflow) Skill not found: threadlight-workflow")
    assert cat == "skill_loader"
    assert sev == "high"


def test_noise_lines_are_not_findings():
    # command-echo (GA "Run" block echoes the script source in cyan-bold [36;1m) must be ignored,
    # otherwise the scanner flags the workflow's own defensive comments + grep-based detectors.
    assert findings.is_noise('\x1b[36;1m  if ! grep -qE "CAPIError|429"; then')
    assert findings.is_noise('^[[36;1m# passing it to --resource triggers AADSTS500011')
    assert findings.is_noise("##[group]Run set -euo pipefail")
    assert not findings.is_noise("Request failed due to a transient API error. Retrying...")


def test_scan_dependency_fixture_one_category():
    lines = (FIXTURES / "logs" / "failed-dependency.log").read_text().splitlines()
    got = findings.scan_lines(lines, run_id=28389162228, phase="install")
    cats = {f["category"] for f in got}
    assert cats == {"dependency"}
    assert got[0]["count"] == 3            # 3 evidence lines collapsed into one finding
    assert got[0]["severity"] == "high"
    assert got[0]["id"] == "F-28389162228-001"


def test_scan_ratelimit_fixture_dedups_by_category():
    lines = (FIXTURES / "logs" / "failed-ratelimit.log").read_text().splitlines()
    got = findings.scan_lines(lines, run_id=28435017341, phase="design")
    by_cat = {f["category"]: f for f in got}
    assert set(by_cat) == {"model_unavailable", "rate_limit"}
    assert by_cat["model_unavailable"]["count"] == 5     # 5 transient-retry lines collapse
    assert by_cat["rate_limit"]["count"] == 1


def test_scan_filters_echoed_grep_detector():
    # a SUCCESS-run line that lists error tokens inside the workflow's own detector
    lines = ['\x1b[36;1m  if ! tail -200 "$LOG" | grep -qE "CAPIError|429|exceeded rate limit"; then']
    assert findings.scan_lines(lines, run_id=1, phase="smoke") == []


def test_step_name_column_does_not_leak_into_classification():
    # Real green-run FP: the step NAME column ("threadlight-deploy + azd up") combined with
    # an innocuous message ("...fails...") matched the deploy rule. Only the MESSAGE must classify.
    line = ("e2e\t[Phase 3/4] Drive 6.2 - threadlight-deploy + azd up\t"
            "2026-06-30T10:30:00Z If anything fails that you cannot auto-recover from, stop.")
    assert findings.scan_lines([line], run_id=1, phase="deploy") == []


def test_allow_list_restricts_categories_for_green_runs():
    # Green-run warnings-only pass: a high-sev dependency line must be suppressed,
    # but a low-sev retry line still surfaces.
    lines = [
        "2026Z ERROR: ResolutionImpossible: conflicting dependencies",
        "2026Z Retrying... attempt 2",
    ]
    got = findings.scan_lines(lines, run_id=9, phase="all-steps",
                              allow={"retry", "slow_turn", "router_fallback"})
    cats = {f["category"] for f in got}
    assert cats == {"retry"}
    assert got[0]["id"] == "F-9-001"

