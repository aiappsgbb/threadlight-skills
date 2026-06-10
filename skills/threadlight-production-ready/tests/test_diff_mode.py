#!/usr/bin/env python3
"""Tests for `--diff` mode (v0.3.0).

`_diff_manifests(prior, current)` returns a human-readable markdown
diff between two production-readiness manifests:

  - delta arrow for raw_percent
  - "## new must-fix" for findings that flipped to must-fix or are net-new
  - "## status changes" for per-finding status flips
  - "## new pass" for net-new pass entries
  - "## removed" for findings that disappeared
  - "(no per-finding changes)" when the two manifests have the same state

stdlib-only; no pytest.
"""
from __future__ import annotations

import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"

sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402

FAILURES: list[str] = []


def expect(cond: bool, name: str, msg: str = "") -> None:
    label = "PASS" if cond else "FAIL"
    line = f"  [{label}] {name}"
    if msg:
        line += f" — {msg}"
    print(line)
    if not cond:
        FAILURES.append(name)


def mk_manifest(*, checked_at: str, raw: int, findings: dict[str, list[dict]]) -> dict:
    """findings = { pillar_id: [ {id, status}, ... ] }"""
    return {
        "tool_version": pr.VERSION,
        "checked_at": checked_at,
        "score": {"raw_percent": raw, "with_waivers_percent": raw},
        "pillars": [
            {"pillar": pid, "findings": fs}
            for pid, fs in findings.items()
        ],
    }


# ---------------------------------------------------------------------------
# 1) Identical manifests -> "(no per-finding changes)" and 0% delta arrow.
# ---------------------------------------------------------------------------


def t_identical_no_changes() -> None:
    print("\nt_identical_no_changes")
    a = mk_manifest(checked_at="2026-06-01T10:00:00Z", raw=42, findings={
        "network-posture": [{"id": "NET-001", "status": "pass"}],
    })
    out = pr._diff_manifests(a, a)
    expect("(no per-finding changes)" in out, "identical: 'no per-finding changes' line")
    expect("delta   : — +0%" in out or "—" in out, "identical: zero-delta arrow")


# ---------------------------------------------------------------------------
# 2) Score regression -> down arrow + correct sign.
# ---------------------------------------------------------------------------


def t_score_regression_arrow() -> None:
    print("\nt_score_regression_arrow")
    a = mk_manifest(checked_at="2026-06-01T10:00:00Z", raw=60, findings={
        "network-posture": [{"id": "NET-001", "status": "pass"}],
    })
    b = mk_manifest(checked_at="2026-06-02T10:00:00Z", raw=40, findings={
        "network-posture": [{"id": "NET-001", "status": "pass"}],
    })
    out = pr._diff_manifests(a, b)
    expect("▼" in out, "regression: down arrow ▼")
    expect("-20%" in out, "regression: -20% delta")


def t_score_improvement_arrow() -> None:
    print("\nt_score_improvement_arrow")
    a = mk_manifest(checked_at="2026-06-01T10:00:00Z", raw=40, findings={
        "network-posture": [{"id": "NET-001", "status": "must-fix"}],
    })
    b = mk_manifest(checked_at="2026-06-02T10:00:00Z", raw=80, findings={
        "network-posture": [{"id": "NET-001", "status": "pass"}],
    })
    out = pr._diff_manifests(a, b)
    expect("▲" in out, "improvement: up arrow ▲")
    expect("+40%" in out, "improvement: +40% delta")


# ---------------------------------------------------------------------------
# 3) Status flip surfaces under "## status changes" with the right transition.
# ---------------------------------------------------------------------------


def t_status_flip_surfaces() -> None:
    print("\nt_status_flip_surfaces")
    a = mk_manifest(checked_at="t1", raw=50, findings={
        "network-posture": [
            {"id": "NET-001", "status": "pass"},
            {"id": "NET-002", "status": "should-fix"},
        ],
    })
    b = mk_manifest(checked_at="t2", raw=30, findings={
        "network-posture": [
            {"id": "NET-001", "status": "must-fix"},  # regressed
            {"id": "NET-002", "status": "should-fix"},  # same
        ],
    })
    out = pr._diff_manifests(a, b)
    expect("## status changes" in out, "flip: status-changes section present")
    expect("NET-001 : pass → must-fix" in out, "flip: NET-001 line shows transition")
    expect("NET-002" not in out.split("## status changes", 1)[1].split("##", 1)[0],
           "flip: unchanged NET-002 not listed under status changes")


# ---------------------------------------------------------------------------
# 4) Net-new finding with must-fix surfaces under "## new must-fix"; pass
#    surfaces under "## new pass".
# ---------------------------------------------------------------------------


def t_net_new_must_fix_and_pass() -> None:
    print("\nt_net_new_must_fix_and_pass")
    a = mk_manifest(checked_at="t1", raw=50, findings={
        "network-posture": [{"id": "NET-001", "status": "pass"}],
    })
    b = mk_manifest(checked_at="t2", raw=50, findings={
        "network-posture": [
            {"id": "NET-001", "status": "pass"},
            {"id": "NET-NEW-A", "status": "must-fix"},
            {"id": "NET-NEW-B", "status": "pass"},
        ],
    })
    out = pr._diff_manifests(a, b)
    expect("## new must-fix" in out, "net-new: 'new must-fix' section")
    expect("NET-NEW-A → must-fix" in out, "net-new: must-fix line present")
    expect("## new pass" in out, "net-new: 'new pass' section")
    expect("NET-NEW-B → pass" in out, "net-new: pass line present")


# ---------------------------------------------------------------------------
# 5) Removed finding surfaces under "## removed" with prior status.
# ---------------------------------------------------------------------------


def t_removed_finding() -> None:
    print("\nt_removed_finding")
    a = mk_manifest(checked_at="t1", raw=50, findings={
        "network-posture": [
            {"id": "NET-001", "status": "pass"},
            {"id": "NET-GONE", "status": "should-fix"},
        ],
    })
    b = mk_manifest(checked_at="t2", raw=50, findings={
        "network-posture": [{"id": "NET-001", "status": "pass"}],
    })
    out = pr._diff_manifests(a, b)
    expect("## removed" in out, "removed: section present")
    expect("NET-GONE (was should-fix)" in out, "removed: prior status echoed")


def main() -> int:
    tests = [
        t_identical_no_changes,
        t_score_regression_arrow,
        t_score_improvement_arrow,
        t_status_flip_surfaces,
        t_net_new_must_fix_and_pass,
        t_removed_finding,
    ]
    print(f"Running {len(tests)} --diff renderer tests")
    for t in tests:
        try:
            t()
        except Exception as exc:  # pragma: no cover
            FAILURES.append(t.__name__)
            print(f"  [FAIL] {t.__name__} raised: {type(exc).__name__}: {exc}")
    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        return len(FAILURES)
    print(f"✅ All {len(tests)} test(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
