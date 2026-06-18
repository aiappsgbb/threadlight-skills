"""
Tests for Phase 1 — discover (discover.py).

Mocks subprocess.run so no real `az bicep build` is needed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import discover  # noqa: E402 — module under test


# ---------------------------------------------------------------------------
# Minimal ARM JSON fixtures
# ---------------------------------------------------------------------------

_ARM_AOAI = {
    "type": "Microsoft.CognitiveServices/accounts/deployments",
    "name": "gpt4o-deployment",
    "location": "eastus2",
    "sku": {"name": "Standard", "capacity": 100},
    "properties": {
        "model": {
            "name": "gpt-4o",
            "version": "2024-08-06",
            "format": "OpenAI",
        }
    },
}

_ARM_STORAGE_LRS = {
    "type": "Microsoft.Storage/storageAccounts",
    "name": "mystorage",
    "location": "eastus2",
    "sku": {"name": "Standard_LRS", "tier": "Standard"},
    "properties": {"accessTier": "Hot"},
}

_ARM_STORAGE_ZRS = {
    "type": "Microsoft.Storage/storageAccounts",
    "name": "mystoragezrs",
    "location": "eastus2",
    "sku": {"name": "Standard_ZRS", "tier": "Standard"},
    "properties": {"accessTier": "Cool"},
}

_ARM_COSMOS = {
    "type": "Microsoft.DocumentDB/databaseAccounts",
    "name": "mycosmosdb",
    "location": "[resourceGroup().location]",
    "properties": {
        "databaseAccountOfferType": "Standard",
        "capabilities": [{"name": "EnableServerless"}],
        "enableMultipleWriteLocations": False,
    },
}

_SAMPLE_ARM_JSON = {
    "resources": [
        _ARM_AOAI,
        _ARM_STORAGE_LRS,
        _ARM_COSMOS,
    ]
}

# Modern Bicep (languageVersion 2.0) emits nested module templates whose
# `resources` block is a *symbolic-name object*, not a list. The top-level
# template wraps each module as a `Microsoft.Resources/deployments` resource.
_SAMPLE_ARM_JSON_SYMBOLIC = {
    "resources": {
        "aoaiModule": {
            "type": "Microsoft.Resources/deployments",
            "name": "aoai-module",
            "properties": {
                "template": {
                    "resources": {
                        "gpt": _ARM_AOAI,
                    }
                }
            },
        },
        "dataModule": {
            "type": "Microsoft.Resources/deployments",
            "name": "data-module",
            "properties": {
                "template": {
                    "resources": {
                        "stg": _ARM_STORAGE_LRS,
                        "cosmos": _ARM_COSMOS,
                    }
                }
            },
        },
    }
}

_MANIFEST_ALL_TYPES = {
    "deployment_manifest": {
        "expected_resource_types": [
            "Microsoft.CognitiveServices/accounts/deployments",
            "Microsoft.Storage/storageAccounts",
            "Microsoft.DocumentDB/databaseAccounts",
        ]
    }
}

_MANIFEST_WITH_MISSING = {
    "deployment_manifest": {
        "expected_resource_types": [
            "Microsoft.CognitiveServices/accounts/deployments",
            "Microsoft.Search/searchServices",  # not in compiled ARM → drift warning
        ]
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bicep_proc(arm_json: dict):
    """Return a mock CompletedProcess for a successful az bicep build."""
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(arm_json)
    proc.stderr = ""
    return proc


def _make_failed_proc(stderr: str, returncode: int = 1):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = ""
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# Test: missing bicep CLI raises FileNotFoundError
# ---------------------------------------------------------------------------

def test_discover_requires_bicep_cli(tmp_path):
    """az bicep build reporting bicep-not-found → FileNotFoundError with friendly message.

    A standalone `bicep` on PATH is NOT required (az bundles its own). The
    friendly install hint comes from the `az bicep build` error branch.
    """
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_MANIFEST_ALL_TYPES), encoding="utf-8")
    bicep = tmp_path / "main.bicep"
    bicep.write_text("// stub", encoding="utf-8")

    with (
        patch("discover.shutil.which", return_value="/usr/bin/az"),
        patch(
            "discover.subprocess.run",
            return_value=_make_failed_proc(
                "Bicep CLI not found. Install it now by running 'az bicep install'."
            ),
        ),
    ):
        with pytest.raises(FileNotFoundError) as exc_info:
            discover.discover_resources(bicep, manifest, use_azd_env=False)

    assert "bicep" in str(exc_info.value).lower()
    assert "az bicep install" in str(exc_info.value)


def test_discover_does_not_require_standalone_bicep(tmp_path):
    """A missing standalone `bicep` must NOT abort when `az bicep build` works.

    Regression for the spurious `shutil.which("bicep")` pre-gate.
    """
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_MANIFEST_ALL_TYPES), encoding="utf-8")
    bicep = tmp_path / "main.bicep"
    bicep.write_text("// stub", encoding="utf-8")

    def fake_which(name):
        # az present, standalone bicep absent
        return None if name == "bicep" else f"/usr/bin/{name}"

    with (
        patch("discover.shutil.which", side_effect=fake_which),
        patch(
            "discover.subprocess.run",
            return_value=_make_bicep_proc(_SAMPLE_ARM_JSON),
        ),
    ):
        results = discover.discover_resources(bicep, manifest, use_azd_env=False)

    assert len(results) == 3


def test_discover_requires_az_cli(tmp_path):
    """shutil.which returns None for 'az' → FileNotFoundError."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_MANIFEST_ALL_TYPES), encoding="utf-8")
    bicep = tmp_path / "main.bicep"
    bicep.write_text("// stub", encoding="utf-8")

    with patch("discover.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError) as exc_info:
            discover.discover_resources(bicep, manifest, use_azd_env=False)

    assert "az" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test: az bicep build subprocess error raises RuntimeError
# ---------------------------------------------------------------------------

def test_discover_raises_on_nonzero_build(tmp_path):
    """Non-zero returncode (not bicep-not-found) → RuntimeError."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_MANIFEST_ALL_TYPES), encoding="utf-8")
    bicep = tmp_path / "main.bicep"
    bicep.write_text("// stub", encoding="utf-8")

    with (
        patch("discover.shutil.which", return_value="/usr/bin/az"),
        patch("discover.subprocess.run", return_value=_make_failed_proc("Syntax error in bicep")),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            discover.discover_resources(bicep, manifest, use_azd_env=False)

    assert "az bicep build failed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test: AOAI extractor
# ---------------------------------------------------------------------------

def test_extractor_aoai_deployment():
    """AOAI deployment with Standard SKU maps to PAYG tier."""
    sku = discover._extract_aoai_deployment(_ARM_AOAI)
    assert sku["name"] == "gpt-4o"
    assert sku["tier"] == "PAYG"
    assert sku["capacity"] == 100
    assert sku["extra"]["model_version"] == "2024-08-06"
    assert sku["extra"]["model_format"] == "OpenAI"


def test_extractor_aoai_ptu():
    """AOAI deployment with ProvisionedManaged SKU maps to PTU tier."""
    resource = {
        **_ARM_AOAI,
        "sku": {"name": "ProvisionedManaged", "capacity": 25},
    }
    sku = discover._extract_aoai_deployment(resource)
    assert sku["tier"] == "PTU"
    assert sku["capacity"] == 25


# ---------------------------------------------------------------------------
# Test: Storage redundancy extraction
# ---------------------------------------------------------------------------

def test_extractor_storage_redundancy_lrs():
    """Standard_LRS → redundancy: LRS."""
    sku = discover._extract_storage(_ARM_STORAGE_LRS)
    assert sku["name"] == "Standard_LRS"
    assert sku["extra"]["redundancy"] == "LRS"
    assert sku["extra"]["access_tier"] == "Hot"


def test_extractor_storage_redundancy_zrs():
    """Standard_ZRS → redundancy: ZRS."""
    sku = discover._extract_storage(_ARM_STORAGE_ZRS)
    assert sku["extra"]["redundancy"] == "ZRS"


def test_extractor_storage_redundancy_grs():
    """Standard_GRS → redundancy: GRS."""
    resource = {
        **_ARM_STORAGE_LRS,
        "sku": {"name": "Standard_GRS", "tier": "Standard"},
    }
    sku = discover._extract_storage(resource)
    assert sku["extra"]["redundancy"] == "GRS"


# ---------------------------------------------------------------------------
# Test: Cosmos extractor
# ---------------------------------------------------------------------------

def test_extractor_cosmos_serverless():
    """Cosmos with EnableServerless capability → tier: serverless."""
    sku = discover._extract_cosmos(_ARM_COSMOS)
    assert sku["name"] == "cosmos-noSQL"
    assert sku["tier"] == "serverless"


def test_extractor_cosmos_provisioned():
    """Cosmos without EnableServerless → tier: provisioned."""
    resource = {
        **_ARM_COSMOS,
        "properties": {
            "databaseAccountOfferType": "Standard",
            "capabilities": [],
            "enableMultipleWriteLocations": False,
            "capacity": {"totalThroughputLimit": 4000},
        },
    }
    sku = discover._extract_cosmos(resource)
    assert sku["tier"] == "provisioned"
    assert sku["capacity"] == 4000


# ---------------------------------------------------------------------------
# Test: full discover_resources with mocked subprocess
# ---------------------------------------------------------------------------

def test_discover_returns_expected_resources(tmp_path):
    """Mock az bicep build → discover_resources returns 3 normalized entries."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_MANIFEST_ALL_TYPES), encoding="utf-8")
    bicep = tmp_path / "main.bicep"
    bicep.write_text("// stub", encoding="utf-8")

    with (
        patch("discover.shutil.which", return_value="/usr/bin/az"),
        patch(
            "discover.subprocess.run",
            return_value=_make_bicep_proc(_SAMPLE_ARM_JSON),
        ),
    ):
        results = discover.discover_resources(bicep, manifest, use_azd_env=False)

    assert len(results) == 3
    kinds = {r["resource_kind"] for r in results}
    assert "Microsoft.CognitiveServices/accounts/deployments" in kinds
    assert "Microsoft.Storage/storageAccounts" in kinds
    assert "Microsoft.DocumentDB/databaseAccounts" in kinds

    # All resource_ids should be None when use_azd_env=False.
    assert all(r["resource_id"] is None for r in results)

    # All sources should be "bicep".
    assert all(r["source"] == "bicep" for r in results)


def test_discover_handles_symbolic_name_object_resources(tmp_path):
    """Nested module templates with object-shaped `resources` (Bicep
    languageVersion 2.0) must be flattened, not crash. Regression test."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_MANIFEST_ALL_TYPES), encoding="utf-8")
    bicep = tmp_path / "main.bicep"
    bicep.write_text("// stub", encoding="utf-8")

    with (
        patch("discover.shutil.which", return_value="/usr/bin/az"),
        patch(
            "discover.subprocess.run",
            return_value=_make_bicep_proc(_SAMPLE_ARM_JSON_SYMBOLIC),
        ),
    ):
        results = discover.discover_resources(bicep, manifest, use_azd_env=False)

    kinds = {r["resource_kind"] for r in results}
    assert "Microsoft.CognitiveServices/accounts/deployments" in kinds
    assert "Microsoft.Storage/storageAccounts" in kinds
    assert "Microsoft.DocumentDB/databaseAccounts" in kinds
    assert len(results) == 3


def test_discover_template_location_becomes_placeholder(tmp_path):
    """Template expression in location → 'resourceGroup-location' placeholder."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_MANIFEST_ALL_TYPES), encoding="utf-8")
    bicep = tmp_path / "main.bicep"
    bicep.write_text("// stub", encoding="utf-8")

    with (
        patch("discover.shutil.which", return_value="/usr/bin/az"),
        patch(
            "discover.subprocess.run",
            return_value=_make_bicep_proc(_SAMPLE_ARM_JSON),
        ),
    ):
        results = discover.discover_resources(bicep, manifest, use_azd_env=False)

    cosmos_entry = next(
        r for r in results if r["resource_kind"] == "Microsoft.DocumentDB/databaseAccounts"
    )
    assert cosmos_entry["region"] == "resourceGroup-location"


# ---------------------------------------------------------------------------
# Test: drift warning emitted
# ---------------------------------------------------------------------------

def test_drift_warning_emitted(tmp_path, capsys):
    """Manifest declares Microsoft.Search/searchServices but Bicep has none → stderr warning."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_MANIFEST_WITH_MISSING), encoding="utf-8")
    bicep = tmp_path / "main.bicep"
    bicep.write_text("// stub", encoding="utf-8")

    # ARM JSON only has AOAI (no Search service).
    arm_no_search = {
        "resources": [_ARM_AOAI]
    }

    with (
        patch("discover.shutil.which", return_value="/usr/bin/az"),
        patch(
            "discover.subprocess.run",
            return_value=_make_bicep_proc(arm_no_search),
        ),
    ):
        results = discover.discover_resources(bicep, manifest, use_azd_env=False)

    captured = capsys.readouterr()
    assert "drift warning" in captured.err
    assert "Microsoft.Search/searchServices" in captured.err
    # The AOAI resource should still be returned.
    assert len(results) == 1
    assert results[0]["resource_kind"] == "Microsoft.CognitiveServices/accounts/deployments"


# ---------------------------------------------------------------------------
# Test: helper functions
# ---------------------------------------------------------------------------

def test_strip_template_expr_literal():
    assert discover._strip_template_expr("myresource") == "myresource"


def test_strip_template_expr_quoted():
    # Last quoted string in the expression is '-gpt4o' (the resource suffix).
    # NOTE: The leading dash is kept — this is best-effort, not a full ARM evaluator.
    assert discover._strip_template_expr("[concat(parameters('prefix'), '-gpt4o')]") == "-gpt4o"


def test_strip_template_expr_single_param():
    # A bare variables() call returns the inner quoted string.
    assert discover._strip_template_expr("[variables('something')]") == "something"


def test_resolve_location_literal():
    assert discover._resolve_location("eastus2") == "eastus2"


def test_resolve_location_template_expr():
    assert discover._resolve_location("[resourceGroup().location]") == "resourceGroup-location"


def test_parse_gib():
    assert discover._parse_gib("0.5Gi") == 0.5
    assert discover._parse_gib("2Gi") == 2.0
    assert discover._parse_gib("") is None
    assert discover._parse_gib("512Mi") is None  # Mebibytes not handled, return None
