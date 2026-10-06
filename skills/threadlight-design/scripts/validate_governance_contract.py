#!/usr/bin/env python3
"""Validate the SPEC §11a Runtime Governance Contract of a pilot workspace.

Usage: python3 skills/threadlight-design/scripts/validate_governance_contract.py <workspace>

Exit 0: the explicit contract is complete and valid (or the SPEC declares none).
Exit 1: the contract is incomplete or invalid; missing keys are listed.
This is an offline input-contract check, not runtime governance evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from skills._shared.governance import GovernanceContractError, validate_governance_contract  # noqa: E402
from skills._shared.governance_selection import spec_contract, spec_contract_gaps  # noqa: E402

TEMPLATE = """framework: github-copilot-sdk   # or microsoft-agent-framework
governance:
  mode: off
  environment_modes: {development: evaluate_only, staging: evaluate_only, preproduction: enforce, production: enforce}
  lifecycle_bindings: []
tools: []"""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    workspace = Path(argv[1]).resolve()
    if not (workspace / "specs" / "SPEC.md").exists():
        print(f"FAIL: {workspace}/specs/SPEC.md not found")
        return 1
    gaps = spec_contract_gaps(workspace)
    if gaps:
        print("FAIL: SPEC §11a governance contract is missing: " + ", ".join(gaps))
        print("Minimal complete block for an ungoverned demo (`mode: off`):\n" + TEMPLATE)
        return 1
    try:
        document = spec_contract(workspace)[0]
        if document is None:
            print("OK: SPEC declares no explicit governance contract (legacy pipeline).")
            return 0
        validate_governance_contract(document, deployment_target="demo-sandbox")
    except (GovernanceContractError, ValueError) as exc:
        print(f"FAIL: SPEC §11a governance contract invalid: {exc}")
        return 1
    print(f"OK: SPEC §11a governance contract complete (mode={document['governance']['mode']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
