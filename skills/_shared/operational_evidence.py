"""Bounded, read-only restore and on-call evidence checks for the scorecard."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re


def _text(value):
    return (isinstance(value, str) and 0 < len(value.strip()) <= 2048
            and not re.search(r"[<>]|\b(?:todo|tbd|placeholder|not executed)\b", value, re.I))


def _time(value, now, days):
    if not isinstance(value, str):
        raise ValueError("execution timestamp missing")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("execution timestamp invalid") from None
    if dt.tzinfo is None or not timedelta(0) <= now - dt <= timedelta(days=days):
        raise ValueError("execution timestamp stale, future or without timezone")
    return dt


def _json_pairs(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate evidence field")
        data[key] = value
    return data


def read_record(root, path):
    path = Path(path)
    if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 1024 * 1024:
        raise ValueError("evidence path outside root or oversized")
    body = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(body, object_pairs_hook=_json_pairs)
    else:
        pairs = []
        for line in body.splitlines():
            match = re.fullmatch(r"\s*(?:\*\*)?([A-Za-z_0-9 ]+)(?::\*\*|\*\*:|:)\s*(.*?)\s*", line)
            if match:
                key = match[1].strip().lower().replace(" ", "_")
                value = match[2]
                if key in {"rto_seconds", "rpo_seconds"} and re.fullmatch(r"\d+(?:\.\d+)?", value):
                    value = float(value)
                pairs.append((key, value))
        data = _json_pairs(pairs)
    if not isinstance(data, dict):
        raise ValueError("evidence must be an object or labelled Markdown record")
    return data


def _attachment(root, data):
    ref, digest = data.get("evidence_ref"), data.get("evidence_sha256")
    if (not _text(ref) or Path(ref).is_absolute()
            or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
        raise ValueError("raw evidence reference/hash missing or invalid")
    path = root / ref
    if not path.resolve().is_relative_to(root.resolve()) or not 0 < path.stat().st_size <= 4 * 1024 * 1024:
        raise ValueError("raw evidence outside root or oversized")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("raw evidence digest mismatch")


def _in_scope(resource, sub, rg):
    prefix = f"/subscriptions/{sub}/resourceGroups/{rg}/providers/".lower()
    return _text(resource) and resource.lower().startswith(prefix) and len(resource) > len(prefix)


def restore(root, paths, sub, rg, *, now=None):
    now = now or datetime.now(timezone.utc)
    if not paths:
        return "must-fix", "No restore-drill evidence; execute the approved service-specific restore runbook."
    valid, errors = [], []
    for path in paths:
        try:
            data = read_record(root, path)
            dt = _time(data.get("completed_at"), now, 90)
            if not sub or not rg:
                raise ValueError("assessment target not resolved")
            if (data.get("subscription_id", "").lower() != sub.lower()
                    or data.get("resource_group", "").lower() != rg.lower()):
                raise ValueError("restore evidence target mismatch")
            for key in ("drill_owner", "restore_point_selected"):
                if not _text(data.get(key)):
                    raise ValueError(f"{key} missing or placeholder")
            if not _in_scope(data.get("protected_item"), sub, rg):
                raise ValueError("protected item not in assessment scope")
            # The isolated restore target may intentionally be in another RG.
            target = data.get("restore_target")
            if (not _text(target) or not re.fullmatch(
                    r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/.+", target)
                    or target.lower() == data["protected_item"].lower()):
                raise ValueError("isolated restore target missing or invalid")
            if data.get("result") != "success" or data.get("validation") != "success":
                raise ValueError("restore or data/application validation did not succeed")
            for key in ("rto_seconds", "rpo_seconds"):
                value = data.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError(f"measured {key} missing or invalid")
            _attachment(root, data)
            valid.append((dt, path))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            # Do not relay file contents, paths outside root or raw decoder text.
            errors.append(f"{path.name}: {exc}" if type(exc) is ValueError
                          else f"{path.name}: unreadable or malformed evidence")
    if not valid:
        return "not-verified", "Restore documented but not restore-verified: " + "; ".join(errors[:5])
    dt, path = max(valid, key=lambda item: item[0])
    return "pass", (f"Restore-verified from `{path.name}` executed {(now - dt).days} days ago; "
                    "scoped success, data/application validation and measured RTO/RPO with raw evidence. "
                    "This is retained drill evidence, not a new live test or an RTO/RPO target guarantee.")


RECEIVER_FIELDS = {
    "emailReceivers": "emailAddress", "smsReceivers": "phoneNumber",
    "voiceReceivers": "phoneNumber", "webhookReceivers": "serviceUri",
    "azureAppPushReceivers": "emailAddress", "azureFunctionReceivers": "httpTriggerUrl",
    "logicAppReceivers": "callbackUrl", "eventHubReceivers": "eventHubName",
}
ROUTING_KEYS = {"action_group_id", "rule_id", "target_resource_id",
                "receiver_type", "receiver_name", "destination_sha256"}


def alert_route(root, groups, rules, sub, rg, *, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(groups, list) or not isinstance(rules, list):
        return "not-verified", "Action-group or alert-rule inventory unavailable/invalid."
    try:
        routing = read_record(root, root / "docs/alert-routing.json")
        if set(routing) != ROUTING_KEYS or not all(_text(v) for v in routing.values()):
            raise ValueError("declared alert routing incomplete")
        for key in ("action_group_id", "rule_id", "target_resource_id"):
            if not _in_scope(routing[key], sub, rg):
                raise ValueError("declared alert routing outside assessment scope")
        group = next((g for g in groups if isinstance(g, dict)
                      and g.get("id", "").lower() == routing["action_group_id"].lower()), None)
        rule = next((r for r in rules if isinstance(r, dict)
                     and r.get("id", "").lower() == routing["rule_id"].lower()), None)
        if group is None or rule is None:
            return "must-fix", "Declared action group or alert rule absent from scoped inventory."
        group, rule = group.get("properties", group), rule.get("properties", rule)
        if group.get("enabled") is not True or rule.get("enabled") is not True:
            return "must-fix", "Declared action group or alert rule is disabled."
        if routing["target_resource_id"].lower() not in [s.lower() for s in rule.get("scopes", [])
                                                       if isinstance(s, str)]:
            return "must-fix", "Alert rule does not target the declared resource."
        actions = rule.get("actions", [])
        if isinstance(actions, dict):
            actions = actions.get("actionGroups", [])
        ids = [a.get("actionGroupId") if isinstance(a, dict) else a for a in actions]
        if routing["action_group_id"].lower() not in [i.lower() for i in ids if isinstance(i, str)]:
            return "must-fix", "Alert rule does not reference the declared action group."
        receiver_type = routing["receiver_type"]
        destination_field = RECEIVER_FIELDS.get(receiver_type)
        if not destination_field:
            raise ValueError("receiver type unsupported; independent verification required")
        receivers = group.get(receiver_type, [])
        receiver = next((r for r in receivers if isinstance(r, dict)
                         and r.get("name") == routing["receiver_name"]), None)
        if not receiver or receiver.get("enabled", True) is not True:
            return "must-fix", "Declared receiver absent or disabled."
        destination = receiver.get(destination_field)
        if (not _text(destination) or re.search("no.?reply", destination, re.I)
                or receiver.get("status", "Enabled") in {"Disabled", "NotSpecified"}
                or hashlib.sha256(destination.encode()).hexdigest() != routing["destination_sha256"]):
            return "must-fix", "Receiver destination is unusable or does not match the declared on-call route."
    except (OSError, ValueError, TypeError, AttributeError):
        return "not-verified", "Alert inventory present; expected on-call mapping absent, invalid or unsupported."
    try:
        receipt = read_record(root, root / "evidence/alert-delivery.json")
        if any(receipt.get(k) != v for k, v in routing.items()):
            raise ValueError("receipt route mismatch")
        received = _time(receipt.get("received_at"), now, 1)
        started = _time(receipt.get("test_started_at"), now, 1)
        if (received < started or receipt.get("status") != "delivered"
                or not _text(receipt.get("correlation_id"))):
            raise ValueError("receipt missing correlated delivery")
        _attachment(root, receipt)
    except (OSError, ValueError, TypeError, AttributeError):
        return "not-verified", ("On-call routing configured (enabled rule, target, group and intended receiver); "
                                "delivery not-verified without a fresh correlated receiver receipt.")
    return "pass", "On-call routing configured and correlated delivery observed within 24 hours."
