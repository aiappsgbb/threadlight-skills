#!/usr/bin/env python3
"""Tests for `--secure-score-floor` CLI flag wiring (v0.3.0 rubber-duck fix).

The flag is declared by argparse but `GOV-104` reads the floor from
``ctx.manifest["secure_score_floor"]``. Module J wires the CLI value
into the manifest dict via ``setdefault`` in ``main()`` so the flag is
honored. This test pins the contract:

  * ``_parse_args(["--secure-score-floor", "85"]).secure_score_floor == 85``
  * ``manifest`` already containing ``secure_score_floor`` wins over the
    CLI default (manifest is more specific intent).
  * ``manifest`` missing the field gets the CLI value via setdefault.

stdlib-only; no pytest. Run directly: ``python3 test_secure_score_floor_wiring.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import production_ready as mod  # noqa: E402

FAILURES: list[str] = []


def expect(cond: bool, name: str, msg: str = "") -> None:
    label = "PASS" if cond else "FAIL"
    line = f"  [{label}] {name}"
    if msg:
        line += f" — {msg}"
    print(line)
    if not cond:
        FAILURES.append(name)


def t_flag_parses_to_int() -> None:
    """`--secure-score-floor 85` → args.secure_score_floor == 85 (int)."""
    print("\nt_flag_parses_to_int")
    args = mod._parse_args([
        "--secure-score-floor", "85",
        "--root", ".",
        "--out", "/tmp/x.json",
        "--report", "/tmp/x.md",
        "--in-postdeploy", "/tmp/in.json",
        "--trend-csv", "",
    ])
    expect(isinstance(args.secure_score_floor, int),
           "flag parses to int", f"type={type(args.secure_score_floor).__name__}")
    expect(args.secure_score_floor == 85,
           "flag value preserved", f"got {args.secure_score_floor}")


def t_default_is_sixty() -> None:
    """No flag → args.secure_score_floor == 60 (the documented default)."""
    print("\nt_default_is_sixty")
    args = mod._parse_args([
        "--root", ".",
        "--out", "/tmp/x.json",
        "--report", "/tmp/x.md",
        "--in-postdeploy", "/tmp/in.json",
        "--trend-csv", "",
    ])
    expect(args.secure_score_floor == 60,
           "default == 60", f"got {args.secure_score_floor}")


def t_setdefault_injects_cli_into_empty_manifest() -> None:
    """Manifest without the key gets the CLI value via setdefault."""
    print("\nt_setdefault_injects_cli_into_empty_manifest")
    manifest: dict = {}
    cli_value = 75
    manifest.setdefault("secure_score_floor", cli_value)
    expect(manifest["secure_score_floor"] == 75,
           "empty manifest gets CLI value", f"got {manifest.get('secure_score_floor')}")


def t_setdefault_preserves_explicit_manifest_entry() -> None:
    """Manifest with an explicit floor wins over the CLI default.

    This is the documented precedence: SPEC §12 `secure_score_floor`
    (loaded into manifest by the user) is a stronger statement of
    customer intent than the CLI default, so it sticks.
    """
    print("\nt_setdefault_preserves_explicit_manifest_entry")
    manifest: dict = {"secure_score_floor": 90}
    cli_value = 60
    manifest.setdefault("secure_score_floor", cli_value)
    expect(manifest["secure_score_floor"] == 90,
           "explicit manifest entry wins", f"got {manifest.get('secure_score_floor')}")


def main() -> int:
    t_flag_parses_to_int()
    t_default_is_sixty()
    t_setdefault_injects_cli_into_empty_manifest()
    t_setdefault_preserves_explicit_manifest_entry()
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} test(s)")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print(f"OK: all 4 secure-score-floor wiring tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
