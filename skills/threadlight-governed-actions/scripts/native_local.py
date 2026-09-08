"""Assessor-owned native local protocol; the existing Task3–5 protocol is unchanged."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shlex
import subprocess
import sys
import uuid

import canonical
from contracts import PathRecord, ProbeResult, ProbeEvidence
import maf_adapter
import mediation
import probes
from skills._shared import native_local_evidence

ROOT = Path(__file__).resolve().parents[3]
MODES = mediation.REQUIRED_NON_PROVIDER_MODES


def source_fingerprints(root, project):
    return native_local_evidence.source_fingerprints(root, project)


def contract(root):
    path = Path(root) / "governance/probe-contract.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if value.get("protocol") != "native-local/v1":
        return None
    if (value.get("entrypoint") != "scripts/local_probe.py"
            or value.get("side_effect_mode") != "local-sdk-storage"
            or value.get("execution_modes") != list(MODES)
            or value.get("factory") != "src/agent/container.py:build_host"
            or value.get("output_contract") != "tool-result-schema"):
        raise probes.ProbeContractError("invalid-native-local-contract")
    return value


def execute(project, declaration):
    """A private pipe, nonce and monotonic counter, never stdout/model reports.

    Docker fd3 is attached to the parent's private stdout pipe, with ordinary
    stdout redirected to stderr. The bridge is trusted, scoped to this worktree,
    networkless and read-only except ignored scratch. This attests a cooperative
    inspected host, not a malicious process with access to its own descriptors.
    """
    project = Path(project).resolve()
    workspace = ROOT.parent if ROOT.name == ".governance-tools" else ROOT
    if not project.is_relative_to(workspace) or (workspace != ROOT and project != workspace):
        raise probes.ProbeContractError("native-project-outside-runner-root")
    scratch = ROOT / ".governance-validation" / ("local-" + uuid.uuid4().hex)
    scratch.mkdir(parents=True)
    (ROOT / ".governance-validation/tmp").mkdir(exist_ok=True)
    nonce = secrets.token_hex(32)
    relative_root = ROOT.relative_to(workspace).as_posix()
    mounted_root = "/work" if relative_root == "." else "/work/" + relative_root
    script = "skills/threadlight-governed-actions/scripts/native_local_child.py"
    payload = {"root": mounted_root, "project": "/work/" + project.relative_to(workspace).as_posix(),
               "scratch": "/work/" + scratch.relative_to(workspace).as_posix(),
               "proof_nonce": nonce, "proof_fd": 3, "execution_modes": declaration["execution_modes"]}
    # The same prepared official-wheel environment as the shared exact-pin runner.
    command = ["docker", "run", "--rm", "--network", "none", "--platform", "linux/amd64",
               "-i", "-v", f"{workspace}:/work:ro",
               "-v", f"{ROOT / '.governance-validation'}:{mounted_root}/.governance-validation",
               "-w", mounted_root, "-e", f"TMPDIR={mounted_root}/.governance-validation/tmp",
               "-e", f"PYTHONPYCACHEPREFIX={mounted_root}/.governance-validation/pycache",
               "-e", f"ACS_OPA_PATH={mounted_root}/.governance-validation/opa-linux-amd64",
               "python:3.12-slim", "sh", "-c",
               "exec .governance-validation/linux-venv/bin/python " + script + " 3>&1 1>&2"]
    if platform.system() == "Linux" and os.environ.get("THREADLIGHT_GOVERNANCE_RUNTIME") == "1":
        # Already inside the qualified environment; no nested Docker or git needed.
        payload.update(root=str(ROOT), project=str(project), scratch=str(scratch))
        command = ["sh", "-c", "exec " + shlex.quote(str(ROOT / ".governance-validation/linux-venv/bin/python"))
                   + " " + shlex.quote(str(ROOT / script)) + " 3>&1 1>&2"]
    try:
        result = subprocess.run(command, input=canonical.canonical_bytes(payload),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, cwd=ROOT)
    except (OSError, subprocess.SubprocessError) as error:
        raise probes.ProbeToolingError("native-local-runner-unavailable") from error
    (scratch / "runner.log").write_bytes(result.stderr[-131072:])
    partial = probes._decode_path_proof_events(result.stdout[:probes._MAX_PATH_PROOF_BYTES], nonce)
    (scratch / "observations.json").write_text(json.dumps(
        [{k: v for k, v in e.items() if k != "proof_nonce"} for e in partial], indent=2) + "\n")
    if result.returncode or len(result.stdout) > probes._MAX_PATH_PROOF_BYTES:
        raise probes.ProbeToolingError(f"native-local-run-failed: {scratch.relative_to(ROOT)}/runner.log")
    events = probes._decode_path_proof_events(result.stdout, nonce)
    if (not events or [e.get("counter") for e in events] != list(range(1, len(events) + 1))
            or events[0].get("event") != "installed" or events[-1].get("event") != "complete"):
        raise probes.ProbeToolingError("native-private-proof-incomplete")
    cleaned = [{k: v for k, v in e.items() if k != "proof_nonce"} for e in events]
    if cleaned[0].get("source_hashes") != source_fingerprints(ROOT, project):
        raise probes.ProbeToolingError("native-executed-source-hash-mismatch")
    (scratch / "observations.json").write_text(json.dumps(cleaned, indent=2) + "\n")
    return cleaned


def evaluate(events, declaration, actions):
    native_local_evidence.validate_events(events)
    selected = {a.action_id for a in actions if a.policy_binding not in {None, "", "none"}}
    if selected != set(declaration["actions"]) or selected != {"returns_apply_decision"}:
        raise probes.ProbeContractError("native-selected-action-coverage-mismatch")
    installed = events[0]["observation"]
    served_hash = events[0]["source_hashes"]["project:src/agent/copilot-instructions.md"]
    for terminal in (e for e in events if e["event"] == "terminal" and e["mode"] != "direct-tool"):
        _require(any(e["event"] == "model_instructions" and e["case"] == terminal["case"]
                     and e["mode"] == terminal["mode"] and e["served_sha256"] == served_hash
                     for e in events), "served-model-instructions-not-observed")
    pins = json.loads((ROOT / "skills/_shared/governance-upstream-pin.json").read_text())
    tuple_observed = maf_adapter.compare_native_observation(installed, pins)
    result, paths = [], []
    for mode in MODES:
        nodes = ("entry", "agent/subagent", "pre-action-seam", "tool-service")
        path_id = mediation._path_id("returns_apply_decision", mode, nodes)
        paths.append(PathRecord(path_id=path_id, action_id="returns_apply_decision", mode=mode,
                                nodes=nodes, pre_action_seam="native Hooks/ACS or direct context guard",
                                equivalent_control_ref=None, covered=False, status="not-verified",
                                evidence_refs=("src/agent/container.py", "governance/probe-contract.json"),
                                discovered=True))
        for case in ("allow", "deny"):
            records = [e for e in events if e.get("case") == case and e.get("mode") == mode]
            terminal = _terminal(records)
            invocations = [e for e in records if e["event"] == "invocation"]
            expected_count = int(case == "allow" and mode != "direct-tool")
            _require(terminal["effects"] == len(invocations) == expected_count, "effect-count-mismatch")
            if mode == "direct-tool":
                _require(any(e["event"] == "direct_guard" and
                             e["exception_class"] == "GovernedToolUnavailable" for e in records),
                         "raw-tool-guard-not-executed")
                records = [*records, {"event": "pre_action_decision", "decision": "deny",
                                     "source": "runtime.trusted_effect_snapshot"}]
            else:
                _require(any(e["event"] == "native_evaluation" for e in records), "native-acs-not-run")
                if case == "allow":
                    _require(any(e["event"] == "native_evaluation" and e["read_names"] == [
                        "returns_get_case", "oms_get_order", "customer_get_profile"] for e in records),
                        "native-ordered-reads-missing")
                for i, e in enumerate(records):
                    if e["event"] == "invocation":
                        _require(e["argument_hash"] == e["original_argument_hash"] and any(
                            p["event"] == "pre_action_decision" and p["decision"] == "allow"
                            and p["action_hash"] == e["action_hash"] and p["receipt_hash"] == e["receipt_hash"]
                            for p in records[:i]), "durable-ack-not-before-matching-effect")
                if case == "deny":
                    _require("deny" in terminal["decisions"], "deny-decision-missing")
            correlated = [{**e, "action_id": "returns_apply_decision", "path_id": path_id,
                           "mode": mode, "evidence_id": f"native-{mode}-{case}-{i}"}
                          for i, e in enumerate(records)]
            proof = probes._build_path_probe_result(
                {"action_id": "returns_apply_decision", "mode": mode, "path_id": path_id},
                {"events": correlated}, "assessor:native-local-private-channel", "path-dispatch-" + case)
            integrity = _evidence("native-integrity-" + mode + "-" + case, "native-loader-integrity",
                                  [installed, records])
            result.append(replace(proof, evidence_refs=proof.evidence_refs + (integrity.evidence_id,),
                                  evidence_items=proof.evidence_items + (integrity,)))
    for case, effects, requests in (("approved", 2, 2), ("rejected", 0, 1), ("absent", 0, 1),
                                   ("invalid-human", 0, 1), ("expired", 0, 1),
                                   ("changed", 0, 1), ("risk-incomplete", 1, 1), ("audit-down", 0, 0),
                                   ("incomplete", 1, 0), ("schema", 0, 0),
                                   ("missing-profile-low", 1, 0), ("missing-profile-risk", 1, 1),
                                   ("missing-profile-absent", 0, 1), ("missing-profile-wrong-info", 0, 0)):
        records = [e for e in events if e.get("case") == case]
        terminal = _terminal(records)
        _require(terminal["effects"] == effects and terminal["approval_requests"] == requests,
                 "native-case-failed:" + case)
        _require(terminal["binding_status"] == "unverified", "local-proof-promoted-to-live")
    for name in ("replay", "changed", "expired"):
        _require(any(e["event"] == "approval_" + name and e["status"] in {400, 403, 409, 410}
                     for e in events), "approval-binding-not-rejected:" + name)
    _require(any(e["event"] == "approval_fresh" and e["effects"] == e["requests"] ==
                 e["distinct_nonces"] == 2 for e in events), "second-fresh-grant-missing")
    _require(any(e["event"] == "audit_failure" for e in events), "audit-failure-control-missing")
    _require(any(e["event"] == "schema_output" for e in events), "native-output-schema-missing")
    _require(any(e["event"] == "unbound_read" and e["queries"] == 1
                 and e["acs_evaluations"] == e["effects"] == 0 for e in events),
             "unbound-read-positive-control-missing")
    for probe_id, kind in ((probes.APPROVAL_PROBE_ID, "native-approval"),
                           (probes.AUDIT_PROBE_ID, "native-durable-audit"),
                           ("native-tool-result-schema", "native-tool-result-schema"),
                           ("native-enforcement", "native-enforcement")):
        record_hash = hashlib.sha256(canonical.canonical_bytes(events)).hexdigest()
        item = _evidence(kind + "-" + record_hash, kind, events)
        installation = _evidence(probe_id + "-installation", "native-loader-integrity", installed)
        result.append(ProbeResult(probe_id=probe_id, action_id="returns_apply_decision", path_id=None,
                                  status="pass", reason_code="native-local-executed",
                                  expected="selected native local control", observed=kind + ":local-only",
                                  evidence_refs=(item.evidence_id, installation.evidence_id),
                                  evidence_items=(item, installation)))
    output_required = any(a.binding_requires_output is True for a in actions if a.action_id in selected)
    output_evidence = _evidence("native-output-selection", "native-output-selection", declaration)
    result.append(ProbeResult(
        probe_id=probes.OUTPUT_PROBE_ID, action_id="returns_apply_decision", path_id=None,
        status="not-verified" if output_required else "not-applicable",
        reason_code="native-output-hook-unverified" if output_required else "native-output-hook-not-selected",
        expected="output-hook proof only when selected",
        observed="tool-result schema is separate; no lifecycle/stream output-hook claim",
        evidence_refs=(output_evidence.evidence_id,), evidence_items=(output_evidence,)))
    return tuple(paths), tuple(result), tuple_observed


def _require(condition, reason):
    if not condition:
        raise probes.ProbeToolingError(reason)


def _terminal(records):
    terminal = [e for e in records if e["event"] == "terminal"]
    _require(len(terminal) == 1, "missing-or-duplicate-native-terminal")
    return terminal[0]


def _evidence(name, kind, value):
    return ProbeEvidence(name, kind, "assessor:native-local-private-channel",
                         "sha256:" + hashlib.sha256(canonical.canonical_bytes(value)).hexdigest())
