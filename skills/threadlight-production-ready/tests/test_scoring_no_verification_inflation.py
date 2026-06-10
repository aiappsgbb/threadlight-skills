#!/usr/bin/env python3
"""Tests for the v0.3.0 scoring contract.

The bug being pinned: v0.2.0 awarded `not-verified` findings +2 points
on a 0-4 scale. That meant a pilot the skill couldn't actually check
landed around 50% raw_percent — close enough to a "ready with risk"
narrative to mislead an SE. v0.3.0 awards `not-verified` +0 and surfaces
the gap as `verification_debt`.

stdlib-only; no pytest.
"""
from __future__ import annotations

import sys
from dataclasses import asdict
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


def mk(fid: str, status: str, *, pillar: str = "network-posture", tier: int = 1,
       severity: str = "must-fix") -> "pr.Finding":
    return pr.Finding(
        id=fid, title=fid, pillar=pillar, severity=severity,
        status=status, tier=tier, detail="",
    )


# ---------------------------------------------------------------------------
# 1) Empty / not-applicable returns ("not-applicable", 0, 0, 0)
# ---------------------------------------------------------------------------


def t_empty_returns_not_applicable() -> None:
    print("\nt_empty_returns_not_applicable")
    st, pct, mx, debt = pr._score_pillar([])
    expect(st == "not-applicable", "empty: status not-applicable")
    expect(pct == 0 and mx == 0 and debt == 0, "empty: zeros for pct/max/debt")


def t_only_not_applicable_findings() -> None:
    print("\nt_only_not_applicable_findings")
    fs = [mk("X-1", "not-applicable"), mk("X-2", "not-applicable")]
    st, pct, mx, debt = pr._score_pillar(fs)
    expect(st == "not-applicable", "only-na: status not-applicable")
    expect(debt == 0, "only-na: 0 verification debt")


# ---------------------------------------------------------------------------
# 2) Point assignment per status — the headline v0.3.0 contract.
#    All single-status pillars to make the math obvious.
# ---------------------------------------------------------------------------


def t_pass_is_4() -> None:
    print("\nt_pass_is_4")
    fs = [mk("A", "pass"), mk("B", "pass")]
    st, pct, mx, debt = pr._score_pillar(fs)
    expect(pct == 100, f"pass: 8/8 -> 100% (got {pct})")
    expect(st == "green", "pass: status green")
    expect(debt == 0, "pass: 0 verification debt")


def t_waived_is_3() -> None:
    print("\nt_waived_is_3")
    fs = [mk("A", "waived"), mk("B", "waived")]
    st, pct, mx, debt = pr._score_pillar(fs)
    # 2*3 / 2*4 = 6/8 = 75%
    expect(pct == 75, f"waived: 6/8 -> 75% (got {pct})")
    expect(debt == 0, "waived: 0 verification debt")


def t_should_fix_is_1() -> None:
    print("\nt_should_fix_is_1")
    fs = [mk("A", "should-fix"), mk("B", "should-fix")]
    st, pct, mx, debt = pr._score_pillar(fs)
    # 2*1 / 2*4 = 2/8 = 25%
    expect(pct == 25, f"should-fix: 2/8 -> 25% (got {pct})")
    expect(st == "amber", "should-fix: status amber")


def t_must_fix_is_0_red() -> None:
    print("\nt_must_fix_is_0_red")
    fs = [mk("A", "must-fix"), mk("B", "pass")]
    st, pct, mx, debt = pr._score_pillar(fs)
    expect(st == "red", "must-fix: any must-fix forces red")
    # 1*0 + 1*4 = 4/8 = 50%
    expect(pct == 50, f"must-fix: 4/8 -> 50% (got {pct})")


def t_not_verified_is_zero_not_two() -> None:
    """THE headline test. v0.2.0 awarded +2 (50%). v0.3.0 awards +0."""
    print("\nt_not_verified_is_zero_not_two")
    fs = [mk("A", "not-verified"), mk("B", "not-verified")]
    st, pct, mx, debt = pr._score_pillar(fs)
    expect(pct == 0,
           f"not-verified-is-zero: 0/8 -> 0% (was 50% in v0.2.0; got {pct}%)")
    expect(debt == 2, f"not-verified: 2 entries -> debt 2 (got {debt})")


# ---------------------------------------------------------------------------
# 3) Verification debt is per-pillar count of not-verified findings (NOT
#    inflated by waived/should-fix/etc.)
# ---------------------------------------------------------------------------


def t_verification_debt_counts_only_not_verified() -> None:
    print("\nt_verification_debt_counts_only_not_verified")
    fs = [
        mk("A", "pass"),
        mk("B", "must-fix"),
        mk("C", "should-fix"),
        mk("D", "waived"),
        mk("E", "not-verified"),
        mk("F", "not-verified"),
        mk("G", "not-applicable"),
    ]
    st, pct, mx, debt = pr._score_pillar(fs)
    expect(debt == 2,
           f"debt-count: only E+F count as debt -> 2 (got {debt})")
    # 4 (pass) + 0 (mf) + 1 (sf) + 3 (waived) + 0 + 0 = 8 / (6*4=24) = 33%
    expect(pct == 33, f"mixed: 8/24 -> 33% (got {pct})")
    expect(st == "red", "mixed: any must-fix forces red")


# ---------------------------------------------------------------------------
# 4) Experimental filter — IDs flagged in FINDING_CATALOG are excluded
#    from scoring unless include_experimental=True.
# ---------------------------------------------------------------------------


def _pick_experimental_id() -> str:
    """Pick any experimental ID from the catalog at runtime."""
    for fid, meta in pr.FINDING_CATALOG.items():
        if meta.get("experimental"):
            return fid
    raise RuntimeError("no experimental IDs in catalog — test fixture wrong")


def t_experimental_excluded_by_default() -> None:
    print("\nt_experimental_excluded_by_default")
    exp_id = _pick_experimental_id()
    fs = [mk("A", "pass"), mk(exp_id, "must-fix")]
    st, pct, mx, debt = pr._score_pillar(fs, include_experimental=False)
    # Experimental dropped -> only A remains -> 4/4 = 100%
    expect(pct == 100, f"exp-excluded: 4/4 -> 100% (got {pct})")
    expect(st == "green", "exp-excluded: status green")


def t_experimental_included_when_opted_in() -> None:
    print("\nt_experimental_included_when_opted_in")
    exp_id = _pick_experimental_id()
    fs = [mk("A", "pass"), mk(exp_id, "must-fix")]
    st, pct, mx, debt = pr._score_pillar(fs, include_experimental=True)
    expect(st == "red", "exp-included: must-fix forces red")
    expect(pct == 50, f"exp-included: 4/8 -> 50% (got {pct})")


def main() -> int:
    tests = [
        t_empty_returns_not_applicable,
        t_only_not_applicable_findings,
        t_pass_is_4,
        t_waived_is_3,
        t_should_fix_is_1,
        t_must_fix_is_0_red,
        t_not_verified_is_zero_not_two,
        t_verification_debt_counts_only_not_verified,
        t_experimental_excluded_by_default,
        t_experimental_included_when_opted_in,
    ]
    print(f"Running {len(tests)} scoring-contract tests")
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
