"""Operational evidence must not inherit freshness or success from inventory."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/production_ready.py"
spec = importlib.util.spec_from_file_location("operational_readiness", SCRIPT)
pr = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pr
spec.loader.exec_module(pr)

SUB = "11111111-1111-4111-8111-111111111111"
SCOPE = f"/subscriptions/{SUB}/resourceGroups/rg-test"
SOURCE = SCOPE + "/providers/Microsoft.DocumentDB/databaseAccounts/source"
TARGET = SCOPE + "/providers/Microsoft.DocumentDB/databaseAccounts/restored"
GROUP = SCOPE + "/providers/Microsoft.Insights/actionGroups/oncall"
RULE = SCOPE + "/providers/Microsoft.Insights/metricAlerts/test"


def stamp(days=0):
    return (datetime.now(timezone.utc) - timedelta(days=days, seconds=1)).isoformat()


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "evidence").mkdir()
    return SimpleNamespace(
        root=tmp_path, spec_text="", spec_12={}, bicep_text="", docs_text="",
        bicep_graph=None, manifest={"deployment_manifest": {
            "subscription_id": SUB, "resource_group": "rg-test"}},
        azd_env={},
    )


def finding(ctx, name):
    return next(f for f in pr._check_reliability_static(ctx) if f.id == name)


def raw_evidence(ctx, data):
    raw = json.dumps(data).encode()
    (ctx.root / "evidence/raw.json").write_bytes(raw)
    return {"evidence_ref": "evidence/raw.json", "evidence_sha256": hashlib.sha256(raw).hexdigest()}


def drill(ctx):
    data = {
        "completed_at": stamp(), "subscription_id": SUB, "resource_group": "rg-test",
        "drill_owner": "Test Operator", "protected_item": SOURCE,
        "restore_point_selected": "point-001", "restore_target": TARGET,
        "result": "success", "validation": "success", "rto_seconds": 720,
        "rpo_seconds": 1800,
    }
    data.update(raw_evidence(ctx, {"restoreJob": "job-001", "status": "Succeeded",
                                 "target": TARGET, "integrity": "matched"}))
    return data


@pytest.mark.parametrize("body", [
    "# Restore drill 2020-01-01\nResult: failure\n",
    "# Restore drill 2099-01-01\nResult: not executed\n",
    "# Restore drill\nResult: _success | failure_\n",
])
def test_review_reproductions_never_pass(ctx, body):
    (ctx.root / "docs/restore-drill.md").write_text(body)
    assert finding(ctx, "REL-007").status != "pass"


@pytest.mark.parametrize("change", [
    {"completed_at": stamp(91)}, {"completed_at": stamp(-1)},
    {"completed_at": "2026-01-01"}, {"completed_at": "invalid"},
    {"result": "failure"}, {"result": "_success | failure_"},
    {"validation": "not executed"}, {"restore_target": "TODO"},
    {"restore_point_selected": ""}, {"protected_item": ""},
    {"subscription_id": "22222222-2222-4222-8222-222222222222"},
    {"resource_group": "rg-unrelated"}, {"rto_seconds": -1},
    {"rpo_seconds": True}, {"evidence_sha256": "0" * 64},
    {"evidence_ref": "../outside.json"},
])
def test_invalid_drill_never_passes(ctx, change):
    data = drill(ctx)
    data.update(change)
    (ctx.root / "docs/restore-drill.json").write_text(json.dumps(data))
    assert finding(ctx, "REL-007").status != "pass"


@pytest.mark.parametrize("format", ["json", "md"])
def test_complete_scoped_drill_passes_without_mtime(ctx, format):
    data = drill(ctx)
    path = ctx.root / f"docs/restore-drill.{format}"
    path.write_text(json.dumps(data) if format == "json" else "\n".join(
        f"**{key.replace('_', ' ').title()}:** {value}" for key, value in data.items()
    ))
    import os
    os.utime(path, (1, 1))
    assert finding(ctx, "REL-007").status == "pass"


def test_drills_selected_by_execution_not_checkout(ctx):
    data = drill(ctx)
    (ctx.root / "docs/restore-drill-valid.json").write_text(json.dumps(data))
    (ctx.root / "docs/restore-drill-draft.md").write_text("# Restore drill\nTODO")
    assert finding(ctx, "REL-007").status == "pass"


def alert_fixtures(ctx):
    receiver = {"name": "oncall", "emailAddress": "oncall@example.com"}
    groups = [{"id": GROUP, "enabled": True, "emailReceivers": [receiver]}]
    rules = [{"id": RULE, "enabled": True, "scopes": [SOURCE],
              "actions": [{"actionGroupId": GROUP}]}]
    routing = {"action_group_id": GROUP, "rule_id": RULE, "target_resource_id": SOURCE,
               "receiver_type": "emailReceivers", "receiver_name": "oncall",
               "destination_sha256": hashlib.sha256(b"oncall@example.com").hexdigest()}
    (ctx.root / "docs/alert-routing.json").write_text(json.dumps(routing))
    return groups, rules, routing


def sre(ctx, monkeypatch, groups, rules):
    def inventory(*args):
        if args[:3] == ("monitor", "action-group", "list"):
            return groups
        if args[:4] == ("monitor", "metrics", "alert", "list"):
            return rules
        return []
    monkeypatch.setattr(pr, "_az_json", inventory)
    return next(f for f in pr._check_sre_live(ctx, {1: True}, SUB, "rg-test")[0]
                if f.id == "SRE-101")


@pytest.mark.parametrize("change", ["disabled", "empty", "wrong-receiver",
                                   "unmapped", "disabled-rule", "wrong-target",
                                   "failed-inventory", "malformed-inventory"])
def test_alert_inventory_is_not_routing_or_delivery(ctx, monkeypatch, change):
    groups, rules, _ = alert_fixtures(ctx)
    if change == "disabled":
        groups[0]["enabled"] = False
    elif change == "empty":
        groups[0]["emailReceivers"] = []
    elif change == "wrong-receiver":
        groups[0]["emailReceivers"][0]["emailAddress"] = "noreply@example.com"
    elif change == "unmapped":
        rules[0]["actions"] = []
    elif change == "disabled-rule":
        rules[0]["enabled"] = False
    elif change == "wrong-target":
        rules[0]["scopes"] = ["/subscriptions/another/resourceGroups/another"]
    elif change == "failed-inventory":
        groups = None
    else:
        groups = {"unexpected": "shape"}
    assert sre(ctx, monkeypatch, groups, rules).status != "pass"


def test_configured_route_is_not_verified_delivery(ctx, monkeypatch):
    groups, rules, _ = alert_fixtures(ctx)
    result = sre(ctx, monkeypatch, groups, rules)
    assert result.status == "not-verified"
    assert "configured" in result.detail


@pytest.mark.parametrize("change", [{}, {"received_at": stamp(2)},
                                   {"received_at": stamp(-1)}, {"status": "failed"},
                                   {"correlation_id": ""}, {"rule_id": RULE + "-wrong"},
                                   {"evidence_sha256": "0" * 64}])
def test_delivery_requires_current_correlated_receipt(ctx, monkeypatch, change):
    groups, rules, routing = alert_fixtures(ctx)
    receipt = {**routing, "status": "delivered", "received_at": stamp(),
               "correlation_id": "alert-001", "test_started_at": stamp(0.001)}
    receipt.update(raw_evidence(ctx, {"id": "alert-001", "status": "delivered",
                                    "actionGroupId": GROUP, "receiver": "oncall"}))
    receipt.update(change)
    (ctx.root / "evidence/alert-delivery.json").write_text(json.dumps(receipt))
    assert (sre(ctx, monkeypatch, groups, rules).status == "pass") == (not change)


def test_no_fake_restore_cli_in_current_recipe():
    source = SCRIPT.read_text()
    assert "azqr restore-drill" not in source
    recipe = SCRIPT.parents[1] / "references/remediation-recipes/REL-007.md"
    assert "mtime" in recipe.read_text()


def test_recipe_distinguishes_configuration_from_delivery():
    recipe = SCRIPT.parents[1] / "references/remediation-recipes/SRE-101.md"
    text = recipe.read_text()
    assert "not-verified" in text and "correlation" in text
    assert "teamWebhookReceivers" not in text
