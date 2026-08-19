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
import tempfile
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
# Mock marker — a conservative, delimited token, never a substring
# ---------------------------------------------------------------------------

def test_mock_marker_matches_delimited_mock_conventions() -> None:
    # Standalone / delimited mock, mocked, mockserver, and "local mock" all count.
    for endpoint in (
        "https://mock.example/mcp",   # dot-delimited
        "erp-mock.internal",          # hyphen-delimited
        "erp-mock",                   # trailing token
        "svc_mock",                   # underscore-delimited
        "mocked-api.local",           # 'mocked'
        "mockserver.internal",        # 'mockserver' compound convention
        "returns-MockServer",         # case-insensitive
        "local mock",                 # space-delimited
    ):
        gaps = sc.integration_binding_gaps(
            integrations=[{"id": "erp", "availability": "real"}],
            mcp_config={"servers": {"erp": {"url": endpoint}}},
        )
        assert gaps == [
            "integration erp is declared real but runtime endpoint is still mock"
        ], f"expected {endpoint!r} to read as a mock endpoint"


def test_mock_marker_does_not_match_substrings_like_mockingbird() -> None:
    # A real hostname that merely CONTAINS 'mock' as a substring (mockingbird,
    # smock, mockapifactory) is not a mock endpoint and must not trip the gate.
    for endpoint in (
        "https://mockingbird.example.com/mcp",
        "https://smock.contoso.com/mcp",
        "https://mockapifactory.io/mcp",
        "https://erp.contoso.com/mcp",
    ):
        assert sc.integration_binding_gaps(
            integrations=[{"id": "erp", "availability": "real"}],
            mcp_config={"servers": {"erp": {"url": endpoint}}},
        ) == [], f"{endpoint!r} must not be treated as a mock endpoint"


# ---------------------------------------------------------------------------
# Repo-root resolution — prefer the explicit CLI root, else discover the nearest
# marked ancestor; never blindly assume parent.parent for a nested manifest.
# ---------------------------------------------------------------------------

def _make_repo_with_nested_manifest() -> tuple[Path, Path]:
    """Build <root>/{azure.yaml, infra/mcp-config.json} with a manifest nested at
    <root>/a/b/specs/manifest.json (a non-default path). Returns (root, manifest).

    Uses ``tempfile.mkdtemp`` (not the pytest ``tmp_path`` fixture) so the file
    is exercised identically under pytest and the standalone runner below."""
    root = Path(tempfile.mkdtemp()) / "root"
    (root / "infra").mkdir(parents=True)
    (root / "infra" / "mcp-config.json").write_text(
        '{"servers": {"erp": {"url": "https://mock.example/mcp"}}}',
        encoding="utf-8",
    )
    (root / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    nested = root / "a" / "b" / "specs"
    nested.mkdir(parents=True)
    manifest = nested / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    return root, manifest


def test_repo_root_prefers_explicit_cli_root() -> None:
    root, manifest = _make_repo_with_nested_manifest()
    # An explicit root (what the CLI passes as Path.cwd()) always wins, even for a
    # deeply nested manifest whose parent.parent is NOT the root.
    assert sc._repo_root_for_manifest(manifest, explicit_root=root) == root
    assert manifest.parent.parent != root  # precondition: parent.parent is wrong


def test_repo_root_discovers_nearest_marked_ancestor_for_nested_manifest() -> None:
    root, manifest = _make_repo_with_nested_manifest()
    # No explicit root: walk up to the nearest ancestor carrying a repo marker
    # (azure.yaml / infra), NOT the manifest's grandparent (a/b).
    resolved = sc._repo_root_for_manifest(manifest)
    assert resolved == root
    assert resolved != manifest.parent.parent


def test_repo_root_default_layout_unaffected() -> None:
    root = Path(tempfile.mkdtemp()) / "root"
    (root / "infra").mkdir(parents=True)
    (root / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
    (root / "specs").mkdir()
    manifest = root / "specs" / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    assert sc._repo_root_for_manifest(manifest) == root


def test_repo_root_falls_back_to_grandparent_without_markers() -> None:
    # A bare pilot with no azure.yaml / infra / .git / mcp-config: discovery finds
    # no marker and falls back to the legacy grandparent, which is correct for the
    # default specs layout.
    root = Path(tempfile.mkdtemp()) / "bare"
    (root / "specs").mkdir(parents=True)
    manifest = root / "specs" / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    assert sc._repo_root_for_manifest(manifest) == manifest.parent.parent == root


def test_nested_manifest_binding_gap_uses_correct_root() -> None:
    # End-to-end: with the explicit CLI root, the nested manifest still resolves
    # the effective mcp-config under <root>/infra and flags the mock binding.
    root, manifest = _make_repo_with_nested_manifest()
    resolved = sc._repo_root_for_manifest(manifest, explicit_root=root)
    mcp = sc._load_effective_mcp_config(resolved)
    gaps = sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config=mcp,
    )
    assert gaps == [
        "integration erp is declared real but runtime endpoint is still mock"
    ]


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
