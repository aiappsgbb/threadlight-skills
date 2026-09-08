"""Payload-free, source-bound LOCAL-14 evidence; never deployed attestation.

The recorder executes a cooperative inspected host. Consumers revalidate the
observed sequence and its bindings, not kind labels or a claimed pass count.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

SCHEMA = "threadlight-native-local-evidence/v1"
SOURCE = "assessor:native-local-private-channel"
ACTION = "returns_apply_decision"
MODES = ("interactive", "batch", "background", "subagent", "direct-tool")
CONTROLS = {
    "approval-anti-replay": "native-approval",
    "payload-free-audit": "native-durable-audit",
    "native-tool-result-schema": "native-tool-result-schema",
    "native-enforcement": "native-enforcement",
}
CASES = {
    "approved": (2, 2), "rejected": (0, 1), "absent": (0, 1),
    "invalid-human": (0, 1), "expired": (0, 1), "changed": (0, 1),
    "risk-incomplete": (1, 1), "audit-down": (0, 0), "incomplete": (1, 0),
    "schema": (0, 0), "missing-profile-low": (1, 0), "missing-profile-risk": (1, 1),
    "missing-profile-absent": (0, 1), "missing-profile-wrong-info": (0, 0),
}
_FIELDS = {
    "routing_target": "expected_resolved_path",
    "resolved_path": "resolved_path",
    "unbound_read": "tool_id queries acs_evaluations effects",
    "native_evaluation": "point snapshot_hash result_hash native_decision read_names",
    "pre_action_decision": "decision receipt_id action_hash policy_hash receipt_hash receipt",
    "invocation": "argument_hash original_argument_hash action_hash receipt_hash effect_count",
    "model_instructions": "served_sha256 request_sha256 scope",
    "schema_output": "output_hash",
    "terminal": "effects reads decisions approval_requests binding_status",
    "batch_completed": "items", "background_completed": "", "nested_host": "child_factory_hash",
    "direct_guard": "exception_class",
    "approval_request": "intent_hash nonce_hash status",
    "human_decision": "intent_hash grant_hash status approved",
    "approval_consume": "intent_hash grant_hash nonce_hash action_hash policy_hash status",
    "approval_replay": "intent_hash grant_hash status",
    "approval_expired": "intent_hash grant_hash status",
    "approval_changed": "intent_hash grant_hash status",
    "approval_fresh": "requests effects distinct_nonces",
    "audit_failure": "exception_class",
}
_COUNTS = {"queries", "acs_evaluations", "effects", "reads", "approval_requests",
           "effect_count", "items", "status", "requests", "distinct_nonces"}
_RECEIPT = {"receipt_id", "correlation_id", "action_id", "action_hash", "policy_digest",
            "decision", "reason_code", "agent_version", "image_digest", "recorded_at"}


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError("native-local-evidence:" + reason)


def timestamp(value):
    require(isinstance(value, str), "timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "timestamp-zone")
    return result


def source_fingerprints(root, project):
    project_files = {project / name for name in ("agent.yaml", "AGENTS.md", "governance/probe-contract.json")}
    project_files.update(p for p in (project / "src/agent").rglob("*")
                         if p.is_file() and p.suffix in {".py", ".rego", ".yaml", ".json", ".md"})
    project_files.add(project / "scripts/local_probe.py")
    runner_files = set((root / "skills/threadlight-govern/references/runtime").glob("*.py"))
    runner_files.update((root / "skills/threadlight-deploy/references/governance").glob("*.py"))
    runner_files.update(root / "skills/_shared" / name for name in (
        "governance.py", "native_validation.py", "native_local_evidence.py",
        "local_control_fixture.py", "local_model_fixture.py", "governance-upstream-pin.json"))
    runner_files.update(root / "skills/threadlight-governed-actions/scripts" / name
                        for name in ("native_local.py", "native_local_child.py"))
    return {prefix + p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for prefix, base, files in (("project:", project, project_files), ("runner:", root, runner_files))
            for p in sorted(files)}


def validate_events(events, *, captured_at=None):
    require(isinstance(events, list) and 24 < len(events) < 1000, "event-count")
    require(all(isinstance(e, dict) for e in events), "event-object")
    require(all(type(e.get("counter")) is int and e["counter"] == n
                for n, e in enumerate(events, 1)), "sequence")
    require(set(events[0]) == {"event", "counter", "observation", "source_hashes"}
            and events[0]["event"] == "installed", "installation")
    require(events[-1] == {"event": "complete", "counter": len(events)}, "completion")
    installation = events[0]["observation"]
    pins = json.loads(Path(__file__).with_name("governance-upstream-pin.json").read_text())
    expected = {pins[k]["distribution"]: pins[k]["version"] for k in ("agt", "acs", "agent_hooks")}
    expected.update(pins["maf"])
    require(isinstance(installation, dict) and set(installation) == {
        "schema", "execution_mode", "phase", "platform", "python_version", "collected_at",
        "packages", "imports", "opa_sha256", "deployed_image"}, "installation-shape")
    require(installation["schema"] == "native-installed-observation/v1"
            and installation["execution_mode"] == "local" and installation["phase"] == "pre-deploy"
            and installation["platform"] == "linux-amd64"
            and installation["deployed_image"] == "not-verified"
            and installation["opa_sha256"] == pins["opa"]["linux_amd64_static_sha256"],
            "installation-scope")
    records = installation["packages"]
    require(isinstance(records, list) and len(records) == len(expected), "package-count")
    require(all(isinstance(r, dict) and set(r) == {"distribution", "version", "filename", "sha256"}
                for r in records), "package-shape")
    require({r["distribution"]: r["version"] for r in records} == expected
            and all({k: r[k] for k in ("filename", "sha256")} == pins["wheels"][r["distribution"]]
                    for r in records), "package-pin")
    version = installation["python_version"]
    require(isinstance(version, str) and re.fullmatch(r"[1-9][0-9]*\.[0-9]+\.[0-9]+", version)
            and tuple(map(int, version.split(".")[:2])) >= (3, 12), "python-version")
    require(isinstance(installation["imports"], dict) and set(installation["imports"]) == {
        "agent_framework", "agent_framework.foundry", "agent_framework_foundry_hosting",
        "agent_primitives", "agent_hooks", "agent_control_specification"}
            and all(isinstance(v, str) and v.endswith("/__init__.py")
                    for v in installation["imports"].values()), "native-imports")
    observed_at = timestamp(installation["collected_at"])
    if captured_at is not None:
        require(abs((timestamp(captured_at) - observed_at).total_seconds()) <= 300, "capture-binding")
    hashes = events[0]["source_hashes"]
    require(isinstance(hashes, dict) and hashes
            and all(isinstance(k, str) and k.startswith(("project:", "runner:"))
                    and isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v)
                    for k, v in hashes.items()), "source-hashes")
    expected_groups = {(mode, case): (int(case == "allow" and mode != "direct-tool"), 0)
                       for mode in MODES for case in ("allow", "deny")}
    expected_groups.update({("interactive", case): counts for case, counts in CASES.items()})
    groups = {key: [] for key in expected_groups}
    receipt_ids = set()
    for event in events[1:-1]:
        name = event.get("event")
        require(isinstance(name, str) and name in _FIELDS, "event-type")
        require(set(event) == {"event", "counter", "action_id", "case", "mode"} | set(_FIELDS[name].split()),
                "event-fields")
        require(event["action_id"] == ACTION
                and (event["mode"], event["case"]) in groups, "action-scope")
        groups[(event["mode"], event["case"])].append(event)
        for key, value in event.items():
            if key in _COUNTS:
                require(type(value) is int and 0 <= value <= 1000, "integer:" + key)
            elif key == "approved":
                require(type(value) is bool, "human-decision-type")
            elif key.endswith("_hash") or key in {"served_sha256", "request_sha256"}:
                pattern = "[0-9a-f]{64}" if key in {"served_sha256", "child_factory_hash"} else "sha256:[0-9a-f]{64}"
                require(isinstance(value, str) and re.fullmatch(pattern, value), "digest:" + key)
        if name == "pre_action_decision":
            receipt = event["receipt"]
            require(isinstance(receipt, dict) and set(receipt) == _RECEIPT
                    and all(isinstance(v, str) and 0 < len(v) <= 256 for v in receipt.values()),
                    "payload-free-receipt")
            require(digest(receipt) == event["receipt_hash"]
                    and receipt["action_id"] == ACTION and receipt["action_hash"] == event["action_hash"]
                    and receipt["policy_digest"] == event["policy_hash"]
                    and receipt["decision"] == event["decision"]
                    and receipt["receipt_id"] == event["receipt_id"]
                    and receipt["agent_version"] == "local-fixture"
                    and receipt["image_digest"] == "sha256:" + "0" * 64, "receipt-binding")
            require(event["receipt_id"] not in receipt_ids, "replayed-receipt")
            receipt_ids.add(event["receipt_id"])
    for (mode, case), group in groups.items():
        effects, requests = expected_groups[(mode, case)]
        terminal = [e for e in group if e["event"] == "terminal"]
        require(len(terminal) == 1 and group[-1] == terminal[0], "terminal:" + case)
        end = terminal[0]
        require(end["effects"] == effects and end["approval_requests"] == requests
                and end["binding_status"] == "unverified", "terminal-scope:" + case)
        invocations = [e for e in group if e["event"] == "invocation"]
        pending = [e for e in group if e["event"] == "approval_request"]
        require(len(invocations) == effects and len(pending) == requests, "observed-counts:" + case)
        require([e["effect_count"] for e in invocations] == list(range(1, effects + 1)), "effect-order")
        routes = [e for e in group if e["event"] == "routing_target"]
        resolved = [e for e in group if e["event"] == "resolved_path"]
        require(len(routes) == len(resolved) == 1
                and routes[0]["expected_resolved_path"] == resolved[0]["resolved_path"], "served-route")
        if mode != "direct-tool":
            require(any(e["event"] == "model_instructions"
                        and e["served_sha256"] == hashes.get("project:src/agent/copilot-instructions.md")
                        for e in group), "served-instructions")
        else:
            require(any(e["event"] == "direct_guard" and e["exception_class"] == "GovernedToolUnavailable"
                        for e in group), "direct-guard")
        if mode != "direct-tool" and case in {"allow", "deny"}:
            require(any(e["event"] == "native_evaluation" for e in group), "native-evaluation")
            if case == "deny":
                require("deny" in end["decisions"], "native-deny")
            else:
                require(any(e["event"] == "native_evaluation" and e["read_names"] ==
                            ["returns_get_case", "oms_get_order", "customer_get_profile"] for e in group),
                        "ordered-trusted-reads")
        for invocation in invocations:
            require(invocation["argument_hash"] == invocation["original_argument_hash"], "actual-arguments")
            acks = [e for e in group if e["event"] == "pre_action_decision"
                    and e["counter"] < invocation["counter"] and e["decision"] == "allow"
                    and e["action_hash"] == invocation["action_hash"]
                    and e["receipt_hash"] == invocation["receipt_hash"]]
            require(len(acks) == 1, "ack-before-effect")
            if requests:
                consumes = [e for e in group if e["event"] == "approval_consume" and e["status"] == 200
                            and e["action_hash"] == invocation["action_hash"]
                            and e["policy_hash"] == acks[0]["policy_hash"]
                            and e["counter"] < acks[0]["counter"]]
                require(len(consumes) == 1, "consumption-before-ack")
                consume = consumes[0]
                humans = [e for e in group if e["event"] == "human_decision" and e["status"] == 200
                          and e["approved"] is True and e["intent_hash"] == consume["intent_hash"]
                          and e["grant_hash"] == consume["grant_hash"] and e["counter"] < consume["counter"]]
                require(len(humans) == 1 and any(e["status"] == 202
                        and e["intent_hash"] == consume["intent_hash"] and e["nonce_hash"] == consume["nonce_hash"]
                        and e["counter"] < humans[0]["counter"] for e in pending), "authenticated-human-sequence")
    approved = groups[("interactive", "approved")]
    consumed = [e for e in approved if e["event"] == "approval_consume" and e["status"] == 200]
    require(len(consumed) == 2 and len({e["nonce_hash"] for e in consumed}) == 2
            and len({e["intent_hash"] for e in consumed}) == 2
            and len({e["grant_hash"] for e in consumed}) == 2, "fresh-distinct-approvals")
    require(any(e["event"] == "approval_replay" and e["status"] in {400, 403, 409, 410}
                and e["intent_hash"] == consumed[0]["intent_hash"]
                and e["grant_hash"] == consumed[0]["grant_hash"]
                and consumed[0]["counter"] < e["counter"] < consumed[1]["counter"] for e in approved),
            "approval-replay")
    for name in ("changed", "expired"):
        require(any(e["event"] == "approval_" + name and e["status"] in {400, 403, 409, 410}
                    for e in groups[("interactive", name)]), "approval-" + name)
    require(any(e["event"] == "audit_failure" for e in groups[("interactive", "audit-down")]),
            "audit-failure")
    require(any(e["event"] == "schema_output" for e in events), "output-schema")
    require(any(e["event"] == "unbound_read" and e["queries"] == 1
                and e["acs_evaluations"] == e["effects"] == 0 for e in events), "unbound-read")


def build_envelope(events, *, source, captured_at, policy_hashes):
    validate_events(events, captured_at=captured_at)
    return {"schema": SCHEMA, "phase": "pre-deploy", "execution_mode": "local",
            "source": source, "captured_at": captured_at,
            "policy_set_sha256": digest(sorted(policy_hashes, key=lambda p: p["path"])),
            "events": events}


def validated_controls(envelope, probes, evidence, *, source, phase, captured_at, policy_hashes,
                       assessor, root=None):
    """Return only the exact native controls whose full envelope validates."""
    if envelope is None:
        return frozenset()
    require(assessor == {"name": "threadlight-governed-actions", "version": "2.0.0", "adapter": "maf/v1"},
            "assessor")
    require(isinstance(envelope, dict) and set(envelope) == {
        "schema", "phase", "execution_mode", "source", "captured_at", "policy_set_sha256", "events"},
        "envelope-shape")
    require(envelope["schema"] == SCHEMA and phase == envelope["phase"] == "pre-deploy"
            and envelope["execution_mode"] == "local" and envelope["source"] == source
            and envelope["captured_at"] == captured_at
            and envelope["policy_set_sha256"] == digest(sorted(policy_hashes, key=lambda p: p["path"])),
            "envelope-binding")
    events = envelope["events"]
    validate_events(events, captured_at=captured_at)
    if root is not None:
        require(events[0]["source_hashes"] == source_fingerprints(Path(__file__).resolve().parents[2], Path(root)),
                "executed-source-drift")
    by_id = {e["evidence_id"]: e for e in evidence}
    controls = set()
    for probe_id, kind in CONTROLS.items():
        family = [p for p in probes if p["probe_id"] == probe_id and p["action_id"] == ACTION]
        require(len(family) == 1 and family[0]["status"] == "pass"
                and family[0]["path_id"] is None and family[0]["mode"] is None
                and family[0]["reason_code"] == "native-local-executed"
                and family[0]["observed_sha256"] == "sha256:" + hashlib.sha256((kind + ":local-only").encode()).hexdigest(),
                "control-scope:" + probe_id)
        refs = family[0]["evidence_refs"]
        require(isinstance(refs, list) and len(refs) == 2 and len(set(refs)) == 2
                and all(r in by_id for r in refs), "control-references")
        observed = [by_id[r] for r in refs]
        require({e["kind"] for e in observed} == {kind, "native-loader-integrity"}, "control-kinds")
        for entry in observed:
            expected_digest = digest(events if entry["kind"] == kind else events[0]["observation"])
            expected_id = kind + "-" + expected_digest[7:] if entry["kind"] == kind else probe_id + "-installation"
            require(entry["evidence_id"] == expected_id
                    and entry["sha256"] == expected_digest and entry["source"] == SOURCE
                    and entry["live_verified"] is False and entry["phase"] == "pre-deploy"
                    and entry["repository"] == source["repository"]
                    and entry["source_commit"] == source["commit"]
                    and entry["collected_at"] == captured_at
                    and entry["policy_set_sha256"] == envelope["policy_set_sha256"]
                    and entry.get("deployed_target") is None and entry["target_environment"] is None,
                    "control-evidence-binding")
        controls.add((ACTION, probe_id))
    return frozenset(controls)
