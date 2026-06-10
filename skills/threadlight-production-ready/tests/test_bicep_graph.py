#!/usr/bin/env python3
"""Tests for BicepGraph (v0.3.0).

Pins the contract for the compile-once Bicep -> ARM resource view that
replaced the v0.2.0 text-regex parser. Smoking gun was: a Bicep file
containing the substring "Microsoft.Network/virtualNetworks" inside a
comment passed NET-001 (vnet exists). BicepGraph must only count *real*
resource declarations.

stdlib-only; no pytest. Run with:

    python skills/threadlight-production-ready/tests/test_bicep_graph.py

Exit codes: 0 = all green, N = number of failed assertions.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
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


def _bicep_available() -> bool:
    """Return True iff `az bicep build` works on the host."""
    try:
        cp = subprocess.run(["az", "bicep", "version"], capture_output=True,
                            text=True, timeout=20, check=False)
        return cp.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


BICEP_OK = _bicep_available()


# ---------------------------------------------------------------------------
# 1) PrerequisiteError surfaces a friendly hint when bicep is missing.
#    We *simulate* the failure by pointing FROM-REPO at a temp dir with a
#    broken main.bicep so `az bicep build` errors out. We assert on the
#    exception text shape rather than monkey-patching az.
# ---------------------------------------------------------------------------


def t_prerequisite_error_on_broken_bicep() -> None:
    print("\nt_prerequisite_error_on_broken_bicep")
    if not BICEP_OK:
        print("  [SKIP] bicep CLI not installed on host")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "infra").mkdir()
        # Syntactically broken Bicep — guaranteed compile error.
        (root / "infra" / "main.bicep").write_text(
            "this is not bicep at all\n", encoding="utf-8",
        )
        raised = False
        try:
            pr.BicepGraph.from_repo(root)
        except pr.PrerequisiteError as e:
            raised = True
            msg = str(e).lower()
            expect(
                "main.bicep" in msg or "bicep build" in msg or "fix the bicep" in msg,
                "prereq-error: message points at main.bicep / bicep build failure",
            )
        expect(raised, "prereq-error: PrerequisiteError raised when every main fails")


# ---------------------------------------------------------------------------
# 2) Empty graph: no main.bicep -> empty resource list, no exception.
# ---------------------------------------------------------------------------


def t_empty_graph_when_no_main() -> None:
    print("\nt_empty_graph_when_no_main")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "specs").mkdir()
        g = pr.BicepGraph.from_repo(root)
        expect(g.resources == [], "empty: resources is empty list")
        expect(g.source_files == [], "empty: no source files compiled")
        expect(g.by_type("Microsoft.Network/virtualNetworks") == [],
               "empty: by_type returns empty list")
        expect(g.has_type("Microsoft.Network/virtualNetworks") is False,
               "empty: has_type returns False")
        expect(g.count("Microsoft.Network/virtualNetworks") == 0,
               "empty: count returns 0")


# ---------------------------------------------------------------------------
# 3) Comment-only bicep does NOT count as a real resource declaration.
#    This is the v0.2.0 smoking-gun regression: a file that mentions
#    "Microsoft.Network/virtualNetworks" inside a comment used to pass
#    NET-001. With BicepGraph it must return 0.
# ---------------------------------------------------------------------------


def t_comment_only_does_not_register() -> None:
    print("\nt_comment_only_does_not_register")
    if not BICEP_OK:
        print("  [SKIP] bicep CLI not installed on host")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "infra").mkdir()
        (root / "infra" / "main.bicep").write_text(
            "// This pilot will eventually deploy a Microsoft.Network/virtualNetworks\n"
            "// and Microsoft.ApiManagement/service for the AI gateway.\n"
            "// TODO: write the actual modules.\n"
            "param env string = 'dev'\n",
            encoding="utf-8",
        )
        g = pr.BicepGraph.from_repo(root)
        expect(g.count("Microsoft.Network/virtualNetworks") == 0,
               "comment-only: vnet count == 0")
        expect(g.count("Microsoft.ApiManagement/service") == 0,
               "comment-only: APIM count == 0")
        expect(g.has_type("Microsoft.Network/virtualNetworks") is False,
               "comment-only: has_type(vnet) is False")


# ---------------------------------------------------------------------------
# 4) Real declaration registers under the right ARM type (case-insensitive).
# ---------------------------------------------------------------------------


def t_real_declaration_registers() -> None:
    print("\nt_real_declaration_registers")
    if not BICEP_OK:
        print("  [SKIP] bicep CLI not installed on host")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "infra").mkdir()
        (root / "infra" / "main.bicep").write_text(
            "param location string = 'westeurope'\n"
            "resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {\n"
            "  name: 'vnet-test'\n"
            "  location: location\n"
            "  properties: { addressSpace: { addressPrefixes: ['10.0.0.0/16'] } }\n"
            "}\n",
            encoding="utf-8",
        )
        g = pr.BicepGraph.from_repo(root)
        expect(g.count("Microsoft.Network/virtualNetworks") == 1,
               "real-decl: vnet count == 1")
        # Case-insensitivity (ARM lowercases types internally)
        expect(g.count("microsoft.network/virtualnetworks") == 1,
               "real-decl: by_type is case-insensitive")
        expect(g.has_type("Microsoft.Network/virtualNetworks") is True,
               "real-decl: has_type True")
        v = g.by_type("Microsoft.Network/virtualNetworks")[0]
        expect(isinstance(v, dict) and v.get("type", "").lower()
               == "microsoft.network/virtualnetworks",
               "real-decl: resource shape is dict with type field")


# ---------------------------------------------------------------------------
# 5) Module expansion: a top-level main.bicep that references a module
#    should yield the resources declared in the module — proving
#    `_walk` correctly flattens nested templates.
# ---------------------------------------------------------------------------


def t_module_expansion_flattens_resources() -> None:
    print("\nt_module_expansion_flattens_resources")
    if not BICEP_OK:
        print("  [SKIP] bicep CLI not installed on host")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "infra").mkdir()
        (root / "infra" / "main.bicep").write_text(
            "param location string = 'westeurope'\n"
            "module net './modules/network.bicep' = {\n"
            "  name: 'net'\n"
            "  params: { location: location }\n"
            "}\n",
            encoding="utf-8",
        )
        (root / "infra" / "modules").mkdir()
        (root / "infra" / "modules" / "network.bicep").write_text(
            "param location string\n"
            "resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {\n"
            "  name: 'vnet-mod'\n"
            "  location: location\n"
            "  properties: { addressSpace: { addressPrefixes: ['10.1.0.0/16'] } }\n"
            "}\n"
            "resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {\n"
            "  name: 'law-mod'\n"
            "  location: location\n"
            "  properties: {}\n"
            "}\n",
            encoding="utf-8",
        )
        g = pr.BicepGraph.from_repo(root)
        expect(g.count("Microsoft.Network/virtualNetworks") == 1,
               "module-expand: vnet from module visible at top level")
        expect(g.count("Microsoft.OperationalInsights/workspaces") == 1,
               "module-expand: LAW from module visible at top level")
        # Nested template wrappers should NOT leak as resources themselves.
        expect(g.count("Microsoft.Resources/deployments") == 0,
               "module-expand: module wrapper itself NOT counted")


# ---------------------------------------------------------------------------
# 6) property_values pulls dotted-path values across all resources of a type.
# ---------------------------------------------------------------------------


def t_property_values_dotted_path() -> None:
    print("\nt_property_values_dotted_path")
    if not BICEP_OK:
        print("  [SKIP] bicep CLI not installed on host")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "infra").mkdir()
        (root / "infra" / "main.bicep").write_text(
            "param location string = 'westeurope'\n"
            "resource pe 'Microsoft.Network/privateEndpoints@2023-09-01' = {\n"
            "  name: 'pe-1'\n"
            "  location: location\n"
            "  properties: {\n"
            "    subnet: { id: '/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/pe' }\n"
            "    privateLinkServiceConnections: []\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        g = pr.BicepGraph.from_repo(root)
        ids = g.property_values("Microsoft.Network/privateEndpoints", "properties.subnet.id")
        expect(len(ids) == 1 and isinstance(ids[0], str) and "subnets/pe" in ids[0],
               "prop-values: dotted path resolves to subnet id string")


# ---------------------------------------------------------------------------
# Helper used by sample-pilot-broken: assert the FIXTURE itself contains the
# comment-only smoking gun, so the regression test below has something real
# to compile.
# ---------------------------------------------------------------------------


def t_sample_pilot_broken_compiles_to_zero_real_resources() -> None:
    """Compile the smoking-gun fixture and assert: 0 vnets, 0 APIMs."""
    print("\nt_sample_pilot_broken_compiles_to_zero_real_resources")
    if not BICEP_OK:
        print("  [SKIP] bicep CLI not installed on host")
        return
    fixture = SKILL_DIR / "references" / "fixtures" / "sample-pilot-broken"
    if not fixture.exists():
        expect(False, "broken-fixture: sample-pilot-broken missing")
        return
    g = pr.BicepGraph.from_repo(fixture)
    # The broken fixture's main.bicep is comment-only; no real resources.
    expect(g.count("Microsoft.Network/virtualNetworks") == 0,
           "broken-fixture: 0 vnets declared (was 1 under regex parser)")
    expect(g.count("Microsoft.ApiManagement/service") == 0,
           "broken-fixture: 0 APIMs declared")


def main() -> int:
    tests = [
        t_prerequisite_error_on_broken_bicep,
        t_empty_graph_when_no_main,
        t_comment_only_does_not_register,
        t_real_declaration_registers,
        t_module_expansion_flattens_resources,
        t_property_values_dotted_path,
        t_sample_pilot_broken_compiles_to_zero_real_resources,
    ]
    print(f"Running {len(tests)} BicepGraph tests (bicep_ok={BICEP_OK})")
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
