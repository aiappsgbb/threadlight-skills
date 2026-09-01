"""Hermetic application-path enforcement probes (Task 5).

A Conformance Test Kit (CTK) claim or upstream conformance report is
useful dependency evidence, but it is never sufficient by itself: it
proves the *framework* implements a contract, not that *this* target
application's dispatch path actually enforces it. This module drives the
target's real dispatch seam — named by a probe contract, never
guessed — with synthetic fixtures and proves whether a denied or
transformed consequential action ever reaches its tool service.

Every probe runs the target's dispatch callable in an isolated
subprocess: a sanitized (allow-listed, not inherited) environment,
``PYTHONHASHSEED=0`` for reproducibility, a canonical-JSON payload on
stdin, and a hard timeout at the subprocess boundary. The contract's
``side_effect_mode`` must be ``synthetic`` or ``dry-run`` — never
``live`` — so nothing here can mutate customer state.

The dispatch callable and the synthetic tool service it may call both
append payload-free ``start``/``invocation``/``decision`` records to an
exclusive, per-run temporary observation ledger. That ledger is the
*only* thing this module trusts when the child cannot be trusted to
self-report: a crashed, timed-out, or lying child process still leaves
behind whatever it managed to write before the fault happened, so a
parent that has to kill the child can still prove whether the tool
service was ever reached. The child's own stdout report is used only
when it is present, well-formed, *and* consistent with the ledger; it
otherwise reports nothing more than an invocation count, an argument
hash, a decision, an exception class, and audit event IDs — never a raw
argument or tool output.

A denied or transformed action that nonetheless reaches the tool service
(whether self-reported honestly or only provable via the ledger after a
crash/timeout/malformed result) is never a passing probe:

- a clean but *inconsistent* self-report (the seam claims ``deny`` while
  the ledger proves invocation, or claims ``transform`` while the
  argument hash the tool received does not match what was reported) maps
  to ``ENF-001``;
- a crash, timeout, or malformed output/verdict that the ledger proves
  nonetheless reached the tool (fail-open) maps to ``ENF-002``, as a
  completed ``must-fix`` probe result — never a tooling exception; and
- an outcome that is genuinely unobservable (the child failed *and* the
  ledger recorded nothing at all, not even a ``start`` record) raises
  :class:`ProbeToolingError`, since neither a pass nor a specific finding
  can be proven from no evidence at all.

CTK/upstream conformance evidence is tracked separately elsewhere in the
assessor and never substitutes for these application-path probes.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

import canonical
from contracts import Finding, ProbeResult


THIS_FILE = Path(__file__).resolve()

# ``side_effect_mode`` values a probe contract may declare. A probe
# contract is never permitted to request a live side effect.
_ALLOWED_SIDE_EFFECT_MODES: Tuple[str, ...] = ("synthetic", "dry-run")

# The exact JSON keys (and only those keys) a well-formed child stdout
# report may carry — deliberately narrow so no payload field can slip in.
_REQUIRED_REPORT_KEYS: Tuple[str, ...] = (
    "decision",
    "invocation_count",
    "argument_hash",
    "exception_class",
)

# What a passing probe must observe, keyed by ``ProbeCase.fault``.
_EXPECTED_BY_FAULT: Mapping[str, str] = {
    "deny": "tool_not_invoked",
    "transform": "tool_received_transformed_arguments",
    "raise": "tool_not_invoked",
    "sleep": "tool_not_invoked",
    "invalid": "tool_not_invoked",
    "fail_open": "tool_not_invoked",
    "mismatch": "tool_not_invoked",
}

# Human-readable, stable reason codes recorded on a *passing* probe,
# keyed by ``ProbeCase.fault``. Never a catalog finding ID: those are
# reserved for probes that did not pass (see ``_FINDING_TEMPLATES``).
_PASS_REASON_BY_FAULT: Mapping[str, str] = {
    "deny": "deny-enforced",
    "transform": "transform-enforced",
    "raise": "crash-blocked",
    "sleep": "timeout-blocked",
    "invalid": "malformed-verdict-blocked",
}

_DEFAULT_PASS_REASON = "application-probe-enforced"

# The standard deterministic enforcement-probe suite: one (probe_id,
# fault) pair per required probe from section 7.3 of the design spec,
# excluding the deliberately negative fail-open/mismatch control cases
# (those are exercised individually, never as part of the default run).
_ENFORCEMENT_PROBE_SUITE: Tuple[Tuple[str, str], ...] = (
    ("deny", "deny"),
    ("transform", "transform"),
    ("crash", "raise"),
    ("timeout", "sleep"),
    ("malformed-verdict", "invalid"),
)

_ENFORCEMENT_PROBE_ARGUMENTS: Mapping[str, object] = MappingProxyType({"amount": 7})

_FINDING_TEMPLATES: Mapping[str, Mapping[str, object]] = MappingProxyType(
    {
        "ENF-001": MappingProxyType(
            {
                "phase": "pre-deploy",
                "plane": "runtime",
                "summary": (
                    "an application-path probe's self-reported deny/"
                    "transform decision does not match what the ledger "
                    "proves actually happened"
                ),
                "details": (
                    "The application dispatch seam reported a deny or "
                    "transform decision, but the observation ledger (or "
                    "the argument hash the synthetic tool actually "
                    "received) proves a different outcome — either the "
                    "tool was reached despite a claimed deny, or it "
                    "received arguments that do not match the "
                    "self-reported transform. A probe result is never "
                    "treated as passing on a self-report alone."
                ),
            }
        ),
        "ENF-002": MappingProxyType(
            {
                "phase": "pre-deploy",
                "plane": "runtime",
                "summary": (
                    "a probe fault proves the tool service is reachable "
                    "despite a condition that should have blocked it "
                    "(fail-open)"
                ),
                "details": (
                    "Agent Hooks is cooperative and can be bypassed by a "
                    "caller that skips the hook. This application-path "
                    "probe drove a crash, timeout, or malformed-output "
                    "condition that should have blocked the action, and "
                    "the observation ledger proves the synthetic tool "
                    "service was reached anyway, with no compensating "
                    "control found for that bypass surface."
                ),
            }
        ),
    }
)


class ProbeContractError(ValueError):
    """Raised when a probe contract or probe case is invalid or unsafe.

    Covers a missing/malformed ``governance/probe-contract.json``, a
    non-synthetic/dry-run ``side_effect_mode``, a probe case naming an
    action the contract does not declare, or an unknown ``fault``.
    """


class ProbeToolingError(RuntimeError):
    """Raised when a probe's outcome cannot be observed at all.

    This is a harness/environment failure — the child process failed
    (crashed, timed out, or produced unparseable output) *and* the
    observation ledger recorded no evidence whatsoever, so neither a
    pass nor any specific finding can be proven. It is never raised
    merely because a probe failed in an observable way (see ``ENF-002``
    for that case).
    """


@dataclass(frozen=True)
class ProbeCase:
    probe_id: str
    action_id: str
    fault: str
    arguments: Mapping[str, object]


def load_probe_contract(root: Path) -> Mapping[str, object]:
    """Load and validate ``<root>/governance/probe-contract.json``.

    Returns a read-only mapping with exactly ``dispatch``, ``audit_sink``,
    ``timeout_ms``, ``side_effect_mode``, ``observation_ledger``, and
    ``actions`` (normalized to a tuple). Raises :class:`ProbeContractError`
    for anything missing, malformed, or unsafe — including a
    ``side_effect_mode`` other than ``synthetic``/``dry-run`` and an
    ``observation_ledger`` that would escape *root*.
    """
    root_path = Path(root)
    contract_path = root_path / "governance" / "probe-contract.json"
    if not contract_path.is_file():
        raise ProbeContractError(f"missing probe contract: {contract_path}")

    try:
        raw = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProbeContractError(
            f"cannot parse probe contract {contract_path}: {error}"
        ) from error

    if not isinstance(raw, Mapping):
        raise ProbeContractError(
            f"probe contract must be a JSON object: {contract_path}"
        )

    for key in ("dispatch", "audit_sink"):
        value = raw.get(key)
        if not isinstance(value, str) or ":" not in value:
            raise ProbeContractError(
                f"probe contract {key!r} must be an importable 'module:attr' "
                f"reference string; got {value!r}"
            )

    timeout_ms = raw.get("timeout_ms")
    if (
        not isinstance(timeout_ms, int)
        or isinstance(timeout_ms, bool)
        or timeout_ms <= 0
    ):
        raise ProbeContractError(
            f"probe contract 'timeout_ms' must be a positive integer; "
            f"got {timeout_ms!r}"
        )

    side_effect_mode = raw.get("side_effect_mode")
    if side_effect_mode not in _ALLOWED_SIDE_EFFECT_MODES:
        raise ProbeContractError(
            "probe contract 'side_effect_mode' must be one of "
            f"{_ALLOWED_SIDE_EFFECT_MODES!r} (never live); got "
            f"{side_effect_mode!r}"
        )

    observation_ledger = raw.get("observation_ledger")
    if not isinstance(observation_ledger, str) or not observation_ledger:
        raise ProbeContractError(
            "probe contract 'observation_ledger' must be a non-empty "
            f"relative path; got {observation_ledger!r}"
        )
    ledger_relative = Path(observation_ledger)
    if ledger_relative.is_absolute() or ".." in ledger_relative.parts:
        raise ProbeContractError(
            "probe contract 'observation_ledger' must be a relative path "
            f"inside the target root; got {observation_ledger!r}"
        )

    actions = raw.get("actions")
    if (
        not isinstance(actions, list)
        or not actions
        or not all(isinstance(action, str) and action for action in actions)
    ):
        raise ProbeContractError(
            "probe contract 'actions' must be a non-empty list of "
            f"non-empty strings; got {actions!r}"
        )

    return MappingProxyType(
        {
            "dispatch": raw["dispatch"],
            "audit_sink": raw["audit_sink"],
            "timeout_ms": timeout_ms,
            "side_effect_mode": side_effect_mode,
            "observation_ledger": observation_ledger,
            "actions": tuple(actions),
        }
    )


def run_application_probe(root: Path, case: ProbeCase) -> ProbeResult:
    """Drive one probe *case* through the target's real dispatch seam.

    Loads and validates the probe contract, rejects a case naming an
    action or fault the contract/harness does not know about, then runs
    the isolated-subprocess protocol described in the module docstring.
    Never mutates the target repository or any customer state — the
    contract's ``side_effect_mode`` guarantees that.
    """
    root_path = Path(root).resolve()
    contract = load_probe_contract(root_path)

    if case.action_id not in contract["actions"]:
        raise ProbeContractError(
            f"probe case action {case.action_id!r} is not declared in the "
            f"probe contract's actions {contract['actions']!r}"
        )
    if case.fault not in _EXPECTED_BY_FAULT:
        raise ProbeContractError(f"unknown probe fault: {case.fault!r}")
    if not isinstance(case.arguments, Mapping):
        raise ProbeContractError("ProbeCase.arguments must be a mapping")

    ledger_dir = root_path / Path(contract["observation_ledger"]).parent
    try:
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_fd, ledger_name = tempfile.mkstemp(
            dir=str(ledger_dir),
            prefix=f".probe-{case.probe_id}-",
            suffix=".jsonl",
        )
        os.close(ledger_fd)
    except OSError as error:
        raise ProbeToolingError(
            f"cannot create the exclusive observation ledger for probe "
            f"{case.probe_id!r}: {error}"
        ) from error

    ledger_path = Path(ledger_name)
    try:
        outcome = _dispatch_child(root_path, contract, case, ledger_path)
    finally:
        ledger_path.unlink(missing_ok=True)

    return _build_probe_result(case, outcome)


def run_enforcement_probe_set(root: Path) -> Tuple[ProbeResult, ...]:
    """Run the standard deny/transform/crash/timeout/malformed-verdict suite.

    Exercises every probe in ``_ENFORCEMENT_PROBE_SUITE`` for every action
    the probe contract declares, using the same fixed synthetic argument
    fixture each time (deterministic and side-effect-free by construction:
    see ``run_application_probe``).
    """
    contract = load_probe_contract(Path(root))
    results = []
    for action_id in contract["actions"]:
        for probe_id, fault in _ENFORCEMENT_PROBE_SUITE:
            case = ProbeCase(probe_id, action_id, fault, _ENFORCEMENT_PROBE_ARGUMENTS)
            results.append(run_application_probe(root, case))
    return tuple(results)


def findings_from_probes(probes: Tuple[ProbeResult, ...]) -> Tuple[Finding, ...]:
    """Turn non-passing probe results into catalog findings.

    A passing (or ``not-applicable``) probe never produces a finding. A
    probe's ``reason_code`` is expected to already be the exact catalog
    finding ID (``ENF-001``/``ENF-002``) once it did not pass; anything
    else is defensively mapped to ``ENF-001`` rather than silently
    dropped, since an unrecognized non-passing probe is never simply
    ignored.
    """
    findings = []
    for probe in probes:
        if probe.status in ("pass", "not-applicable"):
            continue
        template = _FINDING_TEMPLATES.get(probe.reason_code)
        finding_id = probe.reason_code if template is not None else "ENF-001"
        template = template or _FINDING_TEMPLATES["ENF-001"]
        findings.append(
            Finding(
                finding_id=finding_id,
                status=probe.status,
                phase=template["phase"],
                plane=template["plane"],
                reason_code=probe.reason_code,
                summary=template["summary"],
                details=template["details"],
                affected_actions=(probe.action_id,) if probe.action_id else (),
                affected_paths=(probe.path_id,) if probe.path_id else (),
                evidence_refs=probe.evidence_refs,
            )
        )
    return tuple(findings)


def _build_probe_result(case: ProbeCase, outcome: Mapping[str, object]) -> ProbeResult:
    expected = _EXPECTED_BY_FAULT[case.fault]
    invoked = bool(outcome["invoked"])
    report = outcome["child_report"]

    if report is not None:
        decision = report.get("decision")
        if decision == "deny":
            observed = "tool_invoked_despite_deny" if invoked else "tool_not_invoked"
        elif decision == "transform":
            if not invoked:
                observed = "tool_not_invoked"
            elif outcome.get("invocation_argument_hash") != report.get(
                "argument_hash"
            ):
                observed = "argument_hash_mismatch"
            else:
                observed = "tool_received_transformed_arguments"
        else:
            observed = f"unknown_decision:{decision}"

        status = "pass" if observed == expected else "must-fix"
        reason_code = (
            _PASS_REASON_BY_FAULT.get(case.fault, _DEFAULT_PASS_REASON)
            if status == "pass"
            else "ENF-001"
        )
        evidence_refs = tuple(
            sorted(
                {
                    ref
                    for ref in (report.get("argument_hash"), *report.get("audit_ids", ()))
                    if ref
                }
            )
        )
        return ProbeResult(
            probe_id=case.probe_id,
            action_id=case.action_id,
            path_id=None,
            status=status,
            reason_code=reason_code,
            expected=expected,
            observed=observed,
            evidence_refs=evidence_refs,
        )

    # The child's own report is unusable (it crashed, timed out, or wrote
    # unparseable/malformed output) — only the ledger can prove reality,
    # since a killed or crashed process is never trusted to self-report.
    if not outcome["ledger_observable"]:
        raise ProbeToolingError(
            f"probe {case.probe_id!r} outcome is unobservable: the child "
            f"process failed ({outcome['child_error']}) and the "
            "observation ledger recorded no evidence the interceptor was "
            "ever reached"
        )

    if invoked:
        observed = "tool_invoked_despite_fault"
        status = "must-fix"
        reason_code = "ENF-002"
    else:
        observed = "tool_not_invoked"
        status = "pass" if observed == expected else "must-fix"
        reason_code = (
            _PASS_REASON_BY_FAULT.get(case.fault, _DEFAULT_PASS_REASON)
            if status == "pass"
            else "ENF-001"
        )

    evidence_refs = tuple(
        sorted({ref for ref in (outcome.get("invocation_argument_hash"),) if ref})
    )
    return ProbeResult(
        probe_id=case.probe_id,
        action_id=case.action_id,
        path_id=None,
        status=status,
        reason_code=reason_code,
        expected=expected,
        observed=observed,
        evidence_refs=evidence_refs,
    )


def _dispatch_child(
    root_path: Path,
    contract: Mapping[str, object],
    case: ProbeCase,
    ledger_path: Path,
) -> Mapping[str, object]:
    stdin_payload = {
        "case": {
            "probe_id": case.probe_id,
            "action_id": case.action_id,
            "fault": case.fault,
            "arguments": dict(case.arguments),
        },
        "ledger_path": str(ledger_path),
        "dispatch": contract["dispatch"],
        "audit_sink": contract["audit_sink"],
    }
    stdin_bytes = canonical.canonical_bytes(stdin_payload)

    # A sanitized, allow-listed environment — never the parent's own
    # ``os.environ`` — so nothing the assessor's own process happens to
    # have set (credentials, tokens, unrelated configuration) can leak
    # into, or influence, the isolated child.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(root_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    try:
        process = subprocess.Popen(  # noqa: S603 - fixed, trusted argv; no shell
            [sys.executable, str(THIS_FILE), "--child"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(root_path),
        )
    except OSError as error:
        raise ProbeToolingError(
            f"cannot start the isolated probe subprocess: {error}"
        ) from error

    child_error: Optional[str] = None
    exit_code: Optional[int] = None
    stdout_bytes = b""
    try:
        stdout_bytes, _stderr_bytes = process.communicate(
            input=stdin_bytes, timeout=contract["timeout_ms"] / 1000.0
        )
        exit_code = process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        child_error = "timeout"

    events = _read_ledger_events(ledger_path)
    invoked = any(event.get("event") == "invocation" for event in events)
    invocation_argument_hash = next(
        (
            event.get("argument_hash")
            for event in events
            if event.get("event") == "invocation"
        ),
        None,
    )

    report: Optional[Mapping[str, object]] = None
    if child_error is None:
        if exit_code == 0:
            try:
                parsed = json.loads(stdout_bytes.decode("utf-8"))
                _validate_child_report(parsed)
                report = parsed
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                child_error = "malformed_output"
        else:
            child_error = "nonzero_exit"

    return {
        "child_report": report,
        "child_error": child_error,
        "exit_code": exit_code,
        "invoked": invoked,
        "ledger_observable": bool(events),
        "invocation_argument_hash": invocation_argument_hash,
    }


def _read_ledger_events(ledger_path: Path) -> list:
    try:
        text = ledger_path.read_text(encoding="utf-8")
    except OSError:
        return []
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # A killed child can leave a partial trailing line; ignore
            # it rather than let it look like a real recorded event.
            continue
    return events


def _validate_child_report(report: object) -> None:
    if not isinstance(report, dict):
        raise ValueError("child report must be a JSON object")
    missing = [key for key in _REQUIRED_REPORT_KEYS if key not in report]
    if missing:
        raise ValueError(f"child report missing required keys: {missing}")
    if not isinstance(report["decision"], str) or not report["decision"]:
        raise ValueError("child report 'decision' must be a non-empty string")
    invocation_count = report["invocation_count"]
    if not isinstance(invocation_count, int) or isinstance(invocation_count, bool):
        raise ValueError("child report 'invocation_count' must be an int")
    argument_hash = report["argument_hash"]
    if argument_hash is not None and not isinstance(argument_hash, str):
        raise ValueError("child report 'argument_hash' must be a string or null")
    exception_class = report["exception_class"]
    if exception_class is not None and not isinstance(exception_class, str):
        raise ValueError("child report 'exception_class' must be a string or null")
    audit_ids = report.get("audit_ids", [])
    if not isinstance(audit_ids, list) or not all(
        isinstance(audit_id, str) for audit_id in audit_ids
    ):
        raise ValueError("child report 'audit_ids' must be a list of strings")


def _run_as_child() -> None:
    """Isolated-subprocess entry point; never runs during a normal import.

    Reads the canonical JSON payload from stdin, imports the fixture's
    dispatch callable and audit sink named by the probe contract, invokes
    the dispatch callable with the probe case and ledger path, drains and
    validates the audit sink for payload-freeness, and writes a canonical
    JSON report to stdout containing only invocation count, argument
    hash, decision, exception class, and audit event IDs.
    """
    raw = sys.stdin.buffer.read()
    payload = json.loads(raw.decode("utf-8"))
    case_payload = payload["case"]
    ledger_path = payload["ledger_path"]
    dispatch_ref = str(payload["dispatch"])
    audit_sink_ref = str(payload["audit_sink"])

    dispatch_module_name, dispatch_attr = dispatch_ref.split(":", 1)
    dispatch_module = importlib.import_module(dispatch_module_name)
    dispatch = getattr(dispatch_module, dispatch_attr)

    result = dispatch(case_payload, ledger_path)

    audit_module_name, audit_attr = audit_sink_ref.split(":", 1)
    audit_module = importlib.import_module(audit_module_name)
    audit_events = getattr(audit_module, audit_attr)
    audit_ids = []
    for record in audit_events:
        canonical.validate_payload_free_audit(record)
        audit_ids.append(str(record["audit_id"]))

    report = {
        "decision": str(result["decision"]),
        "invocation_count": int(result["invocation_count"]),
        "argument_hash": result.get("argument_hash"),
        "exception_class": result.get("exception_class"),
        "audit_ids": audit_ids,
    }
    sys.stdout.buffer.write(canonical.canonical_bytes(report))
    sys.stdout.flush()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        _run_as_child()
    else:
        raise SystemExit(
            "probes.py is a library module; its child entry point is only "
            "ever invoked internally by run_application_probe"
        )
