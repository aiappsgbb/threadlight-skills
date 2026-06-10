#!/usr/bin/env python3
"""Tests for `_append_trend_csv` (v0.3.0).

The CLI appends a single row per run to `tests/production-readiness-trend.csv`
so SEs can graph score / verification_debt / posture over time without an
external store. Header is written on first call; subsequent calls append
only the data row.

stdlib-only; no pytest.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"

sys.path.insert(0, str(SCRIPT.parent))
import production_ready as pr  # noqa: E402

FAILURES: list[str] = []

EXPECTED_HEADER = ("date,tool_version,posture,raw_percent,with_waivers_percent,"
                   "verified,total_scoreable,verification_debt,recommendation")


def expect(cond: bool, name: str, msg: str = "") -> None:
    label = "PASS" if cond else "FAIL"
    line = f"  [{label}] {name}"
    if msg:
        line += f" — {msg}"
    print(line)
    if not cond:
        FAILURES.append(name)


def mk_manifest(*, raw: int = 42, with_waivers: int = 50, verified: int = 30,
                total: int = 100, debt: int = 12, rec: str = "not_ready",
                checked_at: str = "2026-06-10T12:00:00Z") -> dict:
    return {
        "tool_version": pr.VERSION,
        "checked_at": checked_at,
        "score": {"raw_percent": raw, "with_waivers_percent": with_waivers},
        "verification_coverage": {"verified": verified, "total_scoreable": total,
                                  "percent": 0},
        "verification_debt": {"total": debt, "by_pillar": {}},
        "go_live_recommendation": rec,
    }


# ---------------------------------------------------------------------------
# 1) First write creates header + 1 row.
# ---------------------------------------------------------------------------


def t_first_write_creates_header() -> None:
    print("\nt_first_write_creates_header")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "trend.csv"
        pr._append_trend_csv(p, mk_manifest(), "standard-ai-gateway")
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        expect(len(lines) == 2, f"first-write: 2 lines (header+row), got {len(lines)}")
        expect(lines[0] == EXPECTED_HEADER, "first-write: header matches contract")
        expect(lines[1].startswith("2026-06-10T12:00:00Z,"),
               "first-write: row starts with checked_at")


# ---------------------------------------------------------------------------
# 2) Second write appends only a row (no extra header).
# ---------------------------------------------------------------------------


def t_second_write_appends_only_row() -> None:
    print("\nt_second_write_appends_only_row")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "trend.csv"
        pr._append_trend_csv(p, mk_manifest(raw=40, checked_at="2026-06-10T12:00:00Z"),
                             "standard-ai-gateway")
        pr._append_trend_csv(p, mk_manifest(raw=42, checked_at="2026-06-11T12:00:00Z"),
                             "standard-ai-gateway")
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        expect(len(lines) == 3, f"second-write: 3 lines (header+2 rows), got {len(lines)}")
        expect(lines[0] == EXPECTED_HEADER, "second-write: header still on line 0")
        expect(lines.count(EXPECTED_HEADER) == 1,
               "second-write: header NOT duplicated")
        expect(lines[1].startswith("2026-06-10T12:00:00Z,") and
               lines[2].startswith("2026-06-11T12:00:00Z,"),
               "second-write: rows in append order")


# ---------------------------------------------------------------------------
# 3) Row contract: each row has 9 comma-separated columns matching header.
# ---------------------------------------------------------------------------


def t_row_has_correct_columns() -> None:
    print("\nt_row_has_correct_columns")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "trend.csv"
        m = mk_manifest(raw=42, with_waivers=50, verified=30, total=100,
                        debt=12, rec="not_ready",
                        checked_at="2026-06-10T12:00:00Z")
        pr._append_trend_csv(p, m, "citadel-spoke")
        row = p.read_text(encoding="utf-8").strip().split("\n")[1]
        cells = row.split(",")
        expect(len(cells) == 9, f"row: 9 columns, got {len(cells)}")
        expect(cells == ["2026-06-10T12:00:00Z", pr.VERSION, "citadel-spoke",
                         "42", "50", "30", "100", "12", "not_ready"],
               f"row: matches expected schema (got {cells})")


# ---------------------------------------------------------------------------
# 4) Missing parents are created (path.parent.mkdir(parents=True, exist_ok=True)).
# ---------------------------------------------------------------------------


def t_creates_parent_directory() -> None:
    print("\nt_creates_parent_directory")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a" / "b" / "c" / "trend.csv"
        pr._append_trend_csv(p, mk_manifest(), "standard-ai-gateway")
        expect(p.exists(), "mkdir: nested parent dirs created and file written")


# ---------------------------------------------------------------------------
# 5) Missing verification_debt / verification_coverage default to empty/0 —
#    we don't want a missing key to crash a long-running trend.
# ---------------------------------------------------------------------------


def t_partial_manifest_does_not_crash() -> None:
    print("\nt_partial_manifest_does_not_crash")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "trend.csv"
        m = {
            "tool_version": pr.VERSION,
            "checked_at": "2026-06-10T12:00:00Z",
            "score": {"raw_percent": 0, "with_waivers_percent": 0},
            "go_live_recommendation": "not_ready",
        }
        pr._append_trend_csv(p, m, "standard-ai-gateway")
        row = p.read_text(encoding="utf-8").strip().split("\n")[1]
        cells = row.split(",")
        expect(len(cells) == 9, "partial: still 9 columns (missing fields = empty)")
        # Columns 5, 6, 7 are verified, total_scoreable, verification_debt.
        # Missing -> empty string for the first two, "0" for debt (.get(...,0)).
        expect(cells[5] == "" and cells[6] == "",
               "partial: verified/total empty when coverage missing")
        expect(cells[7] == "0", "partial: debt defaults to 0")


def main() -> int:
    tests = [
        t_first_write_creates_header,
        t_second_write_appends_only_row,
        t_row_has_correct_columns,
        t_creates_parent_directory,
        t_partial_manifest_does_not_crash,
    ]
    print(f"Running {len(tests)} trend-csv tests")
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
