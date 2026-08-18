#!/usr/bin/env python3
"""Standalone tests for threadlight-safe-check.

Two concerns:

1. The pure ``integration_binding_gaps`` helper — the ONLY new failure the
   post-deploy gate learns to raise: a system integration whose deployment
   snapshot declares ``availability: real`` while the effective MCP server
   endpoint is provably still the scaffolded mock.

2. A byte-for-byte parity assertion between the canonical
   ``scripts/safe_check.py`` and the example copy shipped as
   ``examples/returns-triage-governed/tests/safe_check.py`` — the two must
   never drift, so the helper + behaviour stay in lock-step.

pytest-style (bare ``test_`` functions + ``assert``); also runnable standalone
(``python3 skills/threadlight-safe-check/tests/test_safe_check.py``) because the
skill ships no pytest harness of its own.
"""
from __future__ import annotations

import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "safe_check.py"
REPO_ROOT = SKILL_DIR.parent.parent
EXAMPLE_COPY = (
    REPO_ROOT / "examples" / "returns-triage-governed" / "tests" / "safe_check.py"
)

sys.path.insert(0, str(SCRIPT.parent))
import safe_check as sc  # noqa: E402


# ---------------------------------------------------------------------------
# integration_binding_gaps — the certain contradiction
# ---------------------------------------------------------------------------

def test_real_spec_with_mock_runtime_is_gap() -> None:
    gaps = sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"url": "https://mock.example/mcp"}}},
    )
    assert gaps == [
        "integration erp is declared real but runtime endpoint is still mock"
    ]


def test_mock_spec_with_mock_runtime_is_allowed() -> None:
    assert sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "mock"}],
        mcp_config={"servers": {"erp": {"url": "https://mock.example/mcp"}}},
    ) == []


def test_real_spec_with_real_runtime_is_allowed() -> None:
    assert sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"url": "https://erp.contoso.com/mcp"}}},
    ) == []


def test_real_spec_with_missing_server_is_not_invented_as_mock() -> None:
    # A real integration whose server has no entry in mcp-config has *missing*
    # config metadata — that is not evidence of a mock, so no gap is invented.
    assert sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {}},
    ) == []


def test_real_spec_with_endpointless_server_is_not_invented_as_mock() -> None:
    # Server exists but declares no url/host/name — absence of endpoint
    # metadata must never be treated as a mock marker.
    assert sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"type": "http"}}},
    ) == []


def test_mock_marker_detected_in_host_or_name() -> None:
    by_host = sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"host": "erp-mock.internal"}}},
    )
    by_name = sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"name": "erp-mock"}}},
    )
    assert by_host == [
        "integration erp is declared real but runtime endpoint is still mock"
    ]
    assert by_name == [
        "integration erp is declared real but runtime endpoint is still mock"
    ]


def test_multiple_integrations_only_flags_the_contradiction() -> None:
    gaps = sc.integration_binding_gaps(
        integrations=[
            {"id": "erp", "availability": "real"},
            {"id": "crm", "availability": "real"},
            {"id": "oms", "availability": "mock"},
        ],
        mcp_config={
            "servers": {
                "erp": {"url": "https://mock.example/mcp"},   # real + mock -> gap
                "crm": {"url": "https://crm.contoso.com/mcp"},  # real + real -> ok
                "oms": {"url": "https://mock.example/mcp"},    # mock + mock -> ok
            }
        },
    )
    assert gaps == [
        "integration erp is declared real but runtime endpoint is still mock"
    ]


def test_malformed_inputs_never_raise() -> None:
    assert sc.integration_binding_gaps(integrations=None, mcp_config=None) == []
    assert sc.integration_binding_gaps(integrations=[], mcp_config={}) == []
    assert sc.integration_binding_gaps(
        integrations=["not-a-dict", {"availability": "real"}],  # no id
        mcp_config={"servers": {"erp": {"url": "mock"}}},
    ) == []


# ---------------------------------------------------------------------------
# Canonical <-> example parity: the two safe_check.py copies never drift.
# ---------------------------------------------------------------------------

def test_example_safe_check_is_byte_identical_to_canonical() -> None:
    assert EXAMPLE_COPY.exists(), f"example copy missing at {EXAMPLE_COPY}"
    canonical = SCRIPT.read_bytes()
    example = EXAMPLE_COPY.read_bytes()
    assert canonical == example, (
        "examples/returns-triage-governed/tests/safe_check.py has drifted from "
        "skills/threadlight-safe-check/scripts/safe_check.py — the example copy "
        "must be re-synchronized byte-for-byte."
    )


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except AssertionError as exc:
                failures += 1
                print(f"[FAIL] {name}: {exc}")
    print(f"\n=== {failures} failure(s) ===")
    sys.exit(1 if failures else 0)
