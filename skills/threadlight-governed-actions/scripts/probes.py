"""Hermetic application-path enforcement probes (Task 5).

A Conformance Test Kit (CTK) claim or upstream conformance report is
useful dependency evidence, but it is never sufficient by itself: it
proves the *framework* implements a contract, not that *this* target
application's dispatch path actually enforces it. This module drives both the
target's generic enforcement seam and one declared application execution
dispatcher in isolated children. The dispatcher must route each assessor-bound
action/mode through the application's real routing table; convention-named
functions are discovery evidence only and are never selected by the assessor.

Every probe runs the target's dispatch callable in an isolated
subprocess: a sanitized (allow-listed, not inherited) environment,
``PYTHONHASHSEED=0`` for reproducibility, a canonical-JSON payload on
stdin, and a hard timeout at the subprocess boundary. The contract's
``side_effect_mode`` must be ``synthetic`` or ``dry-run`` — never
``live`` — so nothing here can mutate customer state.

That hard timeout applies only to the fault-under-test, never to
interpreter/import startup: the child writes a fixed-length ready marker
to stdout the moment it has finished importing the dispatch callable,
*before* ever invoking it, and the parent's ``timeout_ms`` clock starts
only once that marker is observed. A separate, generous harness-level
startup bound still keeps a hung or crashed-before-ready child from
blocking a probe run forever — it is just never charged against the
probe contract's own timeout budget, so process-startup jitter can never
make an otherwise-fast probe misclassify as a timeout.

The dispatch callable and the synthetic tool service it may call both
append payload-free ``start``/``invocation``/``decision`` records to an
exclusive, per-run temporary observation ledger. That ledger is the
*only* thing this module trusts when the child cannot be trusted to
self-report: a crashed, timed-out, or lying child process still leaves
behind whatever it managed to write before the fault happened, so a
parent that has to kill the child can still prove whether the tool
service was ever reached. The child's own stdout report is used only
when the child completed normally (exited zero and produced well-formed
output); it otherwise reports nothing more than an invocation count, an
argument hash, a decision, an exception class, and audit event IDs —
never a raw argument or tool output.

A crash, a timeout, unparseable/malformed stdout, or a well-formed but
semantically invalid ("malformed") verdict is judged from the ledger,
exactly like a deny/transform outcome:

- if the ledger proves the tool was never reached (fail-closed), the
  probe still passes — a process dying, hanging, or reporting nonsense
  is not itself a violation as long as it also never let the action
  through — but only once the ledger also proves a *visible operational
  audit signal* was recorded for that action; a "start" record alone,
  with no corroborating audit event, is never enough to grant a pass on
  silence alone, and instead raises :class:`ProbeToolingError`;
- if the ledger proves the tool *was* reached anyway (fail-open) for a
  fault whose own contract forbids invocation on a normal completion
  (deny/crash/timeout/malformed-verdict), that is unconditionally a
  completed ``ENF-002`` must-fix finding — never a tooling exception —
  regardless of which abnormal shape produced it;
- if the ledger proves the tool was reached for a fault whose own
  contract instead *expects* invocation on a normal completion (a
  transform-family fault), an incomplete run is never enough on its own
  to prove either a pass or a fail-open violation: only a completed,
  well-formed self-report can be compared against the ledger's argument
  hash to confirm the transform was correct (see ``ENF-001`` below), so
  this is truthfully reported as ``not-verified`` rather than a
  fabricated ``ENF-002`` — and the same truthful ``not-verified``
  outcome applies when the ledger proves the tool was *never* reached
  for a transform-family fault's incomplete run, since reaching the
  tool was never itself forbidden and an incomplete run proves nothing
  either way;
- a clean but *inconsistent* self-report from a completed, well-formed
  run maps to ``ENF-001``: the seam claims ``deny`` while the ledger
  proves invocation, claims ``transform`` while the argument hash the
  tool received does not match what was reported, while the ledger
  proves *more than one* invocation for the action (an extra raw
  invocation this fixture never admits to), or while the self-reported
  invocation count disagrees with how many invocation records the
  ledger actually holds. That is the one case where a self-report is
  compared against the ledger rather than a ledger-only judgment,
  precisely because the run completed normally with a recognized
  decision. Even then, a completed self-report is never trusted on its
  own: it must additionally be corroborated by a matching
  ``start``/``decision`` ledger record and at least one audit id, or the
  outcome is unobservable and raises :class:`ProbeToolingError` — a
  self-report the ledger cannot corroborate at all is indistinguishable
  from a stub that never really drove the dispatch seam;
- an outcome that is genuinely unobservable (the child failed *and* the
  ledger recorded nothing at all, not even a ``start`` record) raises
  :class:`ProbeToolingError`, since neither a pass nor a specific finding
  can be proven from no evidence at all.

A non-passing probe's finding is only ever produced when it proves a
concrete, truthful violation (``ENF-001``/``ENF-002``); a ``not-verified``
probe — one whose outcome genuinely cannot be confirmed either way —
never produces any finding at all, exactly like a pass, rather than
being mislabeled as a violation it never actually proved.

Every ledger "invocation" record carries both the hash the synthetic
tool actually received and a hash of the case's original (pre-transform)
arguments — still payload-free, just two hashes rather than one — so a
genuine transform (the two hashes differing) can be told apart from a
raw, untransformed invocation (the two hashes matching) without ever
needing the payload itself. A completed ``transform`` decision is only
ever trusted when those two hashes actually *differ*: self-report and
ledger agreeing with each other on an unchanged hash is never, by
itself, proof that a transform happened, since a buggy (or malicious)
seam could pass the raw arguments straight through while still
claiming ``transform``. Whether identical hashes are then a definite
``ENF-001`` violation or a truthfully ``not-verified`` outcome depends
on whether the *case's own arguments* are known — by this harness's
fixed reference-argument contract (never by embedding the target's own
transform *policy*, which is business logic that belongs to the
application under test, not to this generic harness) — to require an
actual change; an arbitrary case using different arguments cannot be
judged that way at all, since a legitimate, policy-compliant no-op is
indistinguishable from a bug without knowing the target's own rules.

A missing ``original_argument_hash`` on that same ledger invocation
record — the field simply absent, as if an older or buggy ledger writer
never recorded it — is never treated as equivalent to either an
observed change or a proven no-op, and is never a pass: it is
incomplete mandatory evidence, and always raises
:class:`ProbeToolingError`, even when every other field of the
self-report and ledger otherwise agrees. A completed ``transform``
decision is trusted only when the ledger can actually prove, one way or
the other, whether the arguments changed — never when it is silent
about it.

This module also never mutates the target repository: an
``observation_ledger`` whose parent directory does not yet exist is
created only for the duration of one probe run and removed again
afterward (if left empty), and an ``observation_ledger`` path that would
resolve — following any symlink along the way — outside the target
root is rejected outright as an unsafe contract.

CTK/upstream conformance evidence is tracked separately elsewhere in the
assessor and never substitutes for these application-path probes.

Task 6 adds three further, independent probe families below (approval
anti-replay, output mediation, and payload-free audit). Their
``redeem``/``emit_output`` dispatch seams run in the exact same kind of
isolated, sanitized subprocess as the application-path probes above —
a target whose dispatch seam hangs, exits the interpreter, or crashes
outright can never hang or kill this assessor process itself.

Anti-replay needs real, durable, service-side redemption state — a
replay is only a replay if the store remembers the first use — but that
state is never the target's own declared, checked-in nonce ledger and
never outlives the assessment that created it.
``run_approval_probe_sequence`` allocates one exclusive, private ledger
per sequence, drives the whole first-use/replay/mutated-binding
sequence against it, and removes it again in a ``finally`` — so the
target repository is left byte-identical whether the sequence
succeeded, failed, timed out, or raised, and consecutive assessments at
the same commit always start from the same empty nonce space. See
``run_approval_probe``, ``run_approval_probe_sequence``,
``run_output_probe``, and ``run_privacy_probe_set`` for each family's
own docstring.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import secrets
import select
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import canonical
from contracts import Finding, Phase, ProbeEvidence, ProbeResult, UnsafeTargetError

if TYPE_CHECKING:
    from contracts import PathRecord


THIS_FILE = Path(__file__).resolve()

# ``side_effect_mode`` values a probe contract may declare. A probe
# contract is never permitted to request a live side effect.
_ALLOWED_SIDE_EFFECT_MODES: Tuple[str, ...] = ("synthetic", "dry-run")

# Target-owned contracts cannot extend an assessor run beyond this fixed
# application-level budget.
_MAX_APPLICATION_TIMEOUT_MS: int = 5_000

# The exact JSON keys (and only those keys) a well-formed child stdout
# report may carry — deliberately narrow so no payload field can slip in.
_REQUIRED_REPORT_KEYS: Tuple[str, ...] = (
    "decision",
    "invocation_count",
    "argument_hash",
    "exception_class",
)

_REQUIRED_PATH_REPORT_KEYS: Tuple[str, ...] = (
    "action_id",
    "mode",
    "resolved_path",
    "expected_resolved_path",
)

# The only decision values a *completed* child report is ever allowed to
# claim. Anything else — even from a well-formed, exit-zero JSON
# envelope — is exactly what "malformed verdict" means at the
# governance level, and is never treated as a clean pass.
_RECOGNIZED_DECISIONS: Tuple[str, ...] = ("deny", "transform")

# A fixed-length sentinel the isolated child writes to stdout the moment
# it has finished interpreter/import startup and resolved the dispatch
# callable — *before* ever invoking it. The parent's application-level
# ``timeout_ms`` boundary starts only once this marker is observed, so
# process-startup jitter is never charged against the probe's
# fault-under-test time budget (see ``_dispatch_child``).
_CHILD_READY_MARKER: bytes = b"PROBE-CHILD-READY\n"

# A generous, harness-level bound on interpreter/import startup itself —
# independent of, and never carved out of, the contract's own
# ``timeout_ms``. It exists only so a child that hangs or crashes before
# ever becoming ready cannot block a probe run forever; ordinary startup
# (well under a few hundred milliseconds) never comes close to it.
_CHILD_READY_TIMEOUT_S: float = 5.0

# Assessor-owned technical bounds for draining the execution-path proof pipe.
# Target contracts cannot raise either limit.
_PATH_PROOF_DRAIN_TIMEOUT_S: float = 0.25
_MAX_PATH_PROOF_BYTES: int = 1_048_576

# What a passing probe must observe, keyed by ``ProbeCase.fault``. Also
# doubles as the harness's registry of recognized fault names (see
# ``run_application_probe``'s validation).
_EXPECTED_BY_FAULT: Mapping[str, str] = {
    "deny": "tool_not_invoked",
    "transform": "tool_received_transformed_arguments",
    "raise": "tool_not_invoked",
    "sleep": "tool_not_invoked",
    "invalid": "tool_not_invoked",
    "fail_open": "tool_not_invoked",
    "mismatch": "tool_not_invoked",
    # Deliberately negative fail-open variants of the crash/timeout/
    # malformed-verdict faults: the fixture invokes the tool *first*,
    # then crashes/hangs/reports a malformed verdict. These prove that
    # ENF-002 is reachable for every abnormal-termination shape, not
    # only the generic ``fail_open`` (crash-after-invoke) case — never
    # expected to pass, so "tool_not_invoked" here is nominal only.
    "timeout_fail_open": "tool_not_invoked",
    "malformed_fail_open": "tool_not_invoked",
    # A transform-family regression: correctly transforms, reaches the
    # tool, then hangs before ever reporting a decision. Its "expected"
    # value matters here only as informational metadata (what a clean
    # completed run of this fault would look like) — an incomplete run
    # is classified as "not-verified", never a fabricated pass or
    # fail-open, since reaching the tool is exactly what a correct run
    # of a transform-family fault is supposed to do (see
    # ``_TRANSFORM_EXPECTED_OUTCOME`` in ``_build_probe_result``).
    "slow_correct_transform": "tool_received_transformed_arguments",
    # A ledger-less self-report regression: never writes anything to
    # the ledger, yet returns a clean, well-formed "deny" report. Never
    # expected to pass — it always raises ``ProbeToolingError`` — so
    # "tool_not_invoked" here is nominal only, like the fail-open
    # variants above.
    "stub": "tool_not_invoked",
    # A no-evidence fail-closed regression: crashes immediately after
    # only a "start" record, with no audit event recorded at all.
    # Proves a pass is never granted on silence alone — always raises
    # ``ProbeToolingError`` — so "tool_not_invoked" here is nominal
    # only, like "stub" above.
    "silent_crash": "tool_not_invoked",
    # A transform-family regression that invokes the tool *twice* (once
    # correctly transformed, once with the raw arguments) while still
    # self-reporting a single clean invocation. Never expected to pass
    # — always a completed ENF-001 finding.
    "double_invoke_transform": "tool_received_transformed_arguments",
    # A transform-family regression that crashes before ever invoking
    # the tool at all. Never expected to pass or fail — always
    # truthfully "not-verified", since an incomplete run of a fault
    # whose normal completion is supposed to reach the tool proves
    # nothing either way.
    "crash_before_transform_invoke": "tool_received_transformed_arguments",
    # A transform-family regression that self-reports a clean
    # "transform" decision, corroborated by a matching ledger record,
    # yet never actually applies the transform policy: it invokes the
    # tool with the raw, completely unchanged arguments. Never expected
    # to pass — proves the harness requires the ledger to show the
    # tool actually received something different from the case's
    # original arguments, not merely that the self-report and ledger
    # agree with each other on an unchanged hash.
    "raw_passthrough_transform": "tool_received_transformed_arguments",
    # A transform-family regression whose ledger "invocation" record is
    # missing the mandatory "original_argument_hash" field entirely —
    # as if an older or buggy ledger writer never recorded it — even
    # though the self-report and every other ledger record are
    # otherwise fully consistent and would, on the surface, look like a
    # correct transform. Never expected to pass — always raises
    # ``ProbeToolingError``, since incomplete mandatory ledger evidence
    # can never be trusted as proof a transform actually happened,
    # regardless of how clean the rest of the self-report looks.
    "transform_missing_original_hash": "tool_received_transformed_arguments",
}

# Human-readable, stable reason codes recorded on a *passing* probe,
# keyed by ``ProbeCase.fault``. Never a catalog finding ID: those are
# reserved for probes that did not pass (see ``_FINDING_TEMPLATES``).
# A crash/timeout/malformed-verdict probe still passes when the ledger
# proves fail-closed (the tool was never reached) — the process dying
# or reporting nonsense is not itself a violation as long as it also
# never let the action through.
_PASS_REASON_BY_FAULT: Mapping[str, str] = {
    "deny": "deny-enforced",
    "transform": "transform-enforced",
    "raise": "crash-blocked",
    "sleep": "timeout-blocked",
    "invalid": "malformed-verdict-blocked",
}

_DEFAULT_PASS_REASON = "application-probe-enforced"

# The reason code recorded when an abnormal outcome reached the tool but
# cannot be judged a fail-open violation, because the fault's own
# contract expects invocation on a normal completion (see
# ``_TRANSFORM_EXPECTED_OUTCOME``). Deliberately not a catalog finding ID
# (``ENF-001``/``ENF-002``): ``findings_from_probes`` still surfaces it
# (its status is ``not-verified``, never dropped like a pass), but it is
# never mistaken for a confirmed ``must-fix`` violation.
_NOT_VERIFIED_REASON = "enforcement-probe-outcome-not-verified"

# The single "expected" value shared by every fault whose own normal,
# completed run is supposed to reach the tool (only "transform" and its
# regression variants such as "slow_correct_transform"). Used by the
# abnormal-outcome branch of ``_build_probe_result`` to decide whether
# reaching the tool at all is inherently forbidden for a given fault
# (deny/crash/timeout/malformed-verdict families) or merely what a
# correct run of it is expected to do (the transform family) — in the
# latter case an incomplete run cannot be judged fail-open from the
# ledger alone.
_TRANSFORM_EXPECTED_OUTCOME = "tool_received_transformed_arguments"

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

# The exact reference arguments the standard, deterministic transform
# probe (``run_enforcement_probe_set``) always exercises. Documented, by
# construction of the synthetic fixture's own $5 pre-authorized refund
# ceiling, to require an actual change on any correct transform
# completion — a completed "transform" decision whose ledger proves the
# tool received these particular arguments completely unchanged is
# therefore a definite, provable enforcement failure, never merely an
# unconfirmed possibility. This harness deliberately never embeds the
# fixture's own transform *policy* (the $5 cap itself is business logic
# that belongs to the target application, not the generic probe
# harness) — it only knows, by the design contract of its own fixed
# reference case, that these particular arguments are transform-
# triggering. An arbitrary probe case using different arguments is not
# known by this generic harness to require a change at all (whether a
# transform is a legitimate no-op depends on business policy this
# module never embeds), so the identical no-op evidence there is
# truthfully "not-verified" instead of an invented violation — see
# ``_build_probe_result``.
_TRANSFORM_REQUIRED_REFERENCE_ARGUMENTS: Mapping[str, object] = _ENFORCEMENT_PROBE_ARGUMENTS

# Observed values for a completed "transform" decision whose ledger
# proves the tool received arguments byte-identical to the case's
# original, pre-transform arguments (no observable change at all) —
# split into a definite violation (the case is known, by this module's
# own reference-argument contract, to require a change) versus a
# truthfully unconfirmed one (an arbitrary case this generic harness
# cannot independently know required any change).
_TRANSFORM_NO_OP_REQUIRED_OBSERVED = "tool_received_unchanged_required_arguments"
_TRANSFORM_NO_OP_UNVERIFIED_OBSERVED = "tool_received_unverified_unchanged_arguments"

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
                    "probe drove a crash, timeout, malformed-output, or "
                    "malformed-verdict condition that should have blocked "
                    "the action, and the observation ledger proves the "
                    "synthetic tool service was reached anyway, with no "
                    "compensating control found for that bypass surface. "
                    "A crash/timeout/malformed outcome that the ledger "
                    "instead proves was fail-closed (the tool was never "
                    "reached) is not itself a violation; only fail-open — "
                    "the tool being invoked despite the fault — earns "
                    "this finding."
                ),
            }
        ),
        "APR-001": MappingProxyType(
            {
                "phase": "pre-deploy",
                "plane": "runtime",
                "summary": (
                    "an approval anti-replay probe proves a replayed or "
                    "mutated binding was accepted, or that nonce redemption "
                    "is not atomic"
                ),
                "details": (
                    "Approval must be bound to the action id, actor, "
                    "tenant, target, policy, and (already-transformed) "
                    "arguments it authorizes, valid only within its own "
                    "expiry window, and redeemable at most once via an "
                    "atomic, service-side nonce store. This probe proves, "
                    "from the persistent nonce ledger's own contents "
                    "rather than any self-report, that a byte-identical "
                    "replay or any single mutated field (subject, role, "
                    "target, tenant, policy, action, or arguments) was "
                    "still accepted, or that the ledger itself grew a "
                    "second record for an already-consumed nonce — proving "
                    "redemption is not actually atomic."
                ),
            }
        ),
        "OUT-001": MappingProxyType(
            {
                "phase": "pre-deploy",
                "plane": "runtime",
                "summary": (
                    "a protected output probe proves output was released "
                    "before its verdict, or an incremental stream was not "
                    "provably chunk-mediated within a declared bound"
                ),
                "details": (
                    "Protected output must be buffered until its "
                    "governing verdict is known — a denied verdict must "
                    "release zero bytes — and any incremental (streamed) "
                    "release only ever passes when the probe contract "
                    "declares an explicit, nonzero exposure bound and "
                    "chunk mediation, and the observation ledger "
                    "independently proves every released chunk was "
                    "mediated and within that declared bound. A stream "
                    "verdict without a declared bound is rejected before "
                    "the dispatch seam is ever invoked at all."
                ),
            }
        ),
        "AUD-001": MappingProxyType(
            {
                "phase": "pre-deploy",
                "plane": "runtime",
                "summary": (
                    "a decision audit record carries a payload-bearing "
                    "field"
                ),
                "details": (
                    "The decision audit trail must be complete, "
                    "correlated, delivered, and payload-free. This probe "
                    "delegates payload-freeness judgment entirely to "
                    "``canonical.validate_payload_free_audit`` and proves "
                    "a sample audit record still carries a banned "
                    "payload-carrying key (for example raw ``arguments``) "
                    "rather than only derived, non-reversible evidence "
                    "such as a hash."
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


class PartialProbeToolingError(ProbeToolingError):
    """A tooling failure that happened after some probe results were already proven."""

    def __init__(self, message: str, *, partial_results: Tuple[ProbeResult, ...]):
        super().__init__(message)
        self.partial_results = partial_results


class ProofChannelReadError(ProbeToolingError):
    """A bounded proof-channel drain ended with only partial bytes."""

    def __init__(self, message: str, *, reason: str, partial_bytes: bytes):
        super().__init__(message)
        self.reason = reason
        self.partial_bytes = partial_bytes


@dataclass(frozen=True)
class ProbeCase:
    probe_id: str
    action_id: str
    fault: str
    arguments: Mapping[str, object]
    path_id: Optional[str] = None


def _validated_execution_paths(raw: Mapping[str, object]) -> Tuple[Mapping[str, str], ...]:
    declared = raw.get("execution_paths", ())
    if not isinstance(declared, (list, tuple)):
        raise ProbeContractError(
            "probe contract 'execution_paths' must be a list of "
            "{action_id, mode, path_id} records"
        )
    normalized: List[Mapping[str, str]] = []
    required = {"action_id", "mode", "path_id"}
    for index, item in enumerate(declared):
        if not isinstance(item, Mapping) or set(item) != required:
            raise ProbeContractError(
                f"probe contract execution_paths[{index}] must contain exactly "
                f"{tuple(sorted(required))!r}; got {item!r}"
            )
        values = {}
        for key in sorted(required):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ProbeContractError(
                    f"probe contract execution_paths[{index}].{key} must be "
                    f"a non-empty string; got {value!r}"
                )
            values[key] = value.strip()
        normalized.append(MappingProxyType(values))
    return tuple(normalized)


def load_probe_contract(root: Path) -> Mapping[str, object]:
    """Load and validate ``<root>/governance/probe-contract.json``.

    Returns a read-only mapping with ``dispatch``, ``execution_dispatch``,
    ``audit_sink``, ``timeout_ms``, ``side_effect_mode``,
    ``observation_ledger``, ``actions`` (normalized to a tuple), and normalized
    ``execution_paths``.
    A legacy top-level ``path_id`` may be read but is never attached to a
    generic dispatch result and therefore cannot prove mediation. Raises
    :class:`ProbeContractError`
    for anything missing, malformed, or unsafe — including a
    ``timeout_ms`` outside the assessor-owned safe range, a
    ``side_effect_mode`` other than ``synthetic``/``dry-run``, and an
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

    for key in ("dispatch", "execution_dispatch", "audit_sink"):
        value = raw.get(key)
        if (
            not isinstance(value, str)
            or value.count(":") != 1
            or not all(part.strip() for part in value.split(":", 1))
        ):
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
    if timeout_ms > _MAX_APPLICATION_TIMEOUT_MS:
        raise ProbeContractError(
            "probe contract 'timeout_ms' must be at most "
            f"{_MAX_APPLICATION_TIMEOUT_MS} milliseconds; got {timeout_ms!r}"
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
    resolved_root = root_path.resolve()
    resolved_ledger_dir = (root_path / ledger_relative).parent.resolve()
    try:
        resolved_ledger_dir.relative_to(resolved_root)
    except ValueError as error:
        raise ProbeContractError(
            "probe contract 'observation_ledger' directory resolves "
            "outside the target root (symlink escape?); got "
            f"{observation_ledger!r}"
        ) from error

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

    path_id = raw.get("path_id")
    if path_id is not None and (not isinstance(path_id, str) or not path_id.strip()):
        raise ProbeContractError(
            "probe contract 'path_id' must be a non-empty string when "
            f"declared; got {path_id!r}"
        )

    return MappingProxyType(
        {
            "dispatch": raw["dispatch"],
            "execution_dispatch": raw["execution_dispatch"],
            "audit_sink": raw["audit_sink"],
            "timeout_ms": timeout_ms,
            "side_effect_mode": side_effect_mode,
            "observation_ledger": observation_ledger,
            "path_id": path_id,
            "actions": tuple(actions),
            "execution_paths": _validated_execution_paths(raw),
        }
    )


def validate_execution_paths(
    contract: Mapping[str, object],
    discovered_paths: Sequence["PathRecord"],
) -> Tuple[Mapping[str, str], ...]:
    """Bind action/mode declarations to the assessor's recomputed inventory.

    Per-path callable references are deliberately forbidden. The isolated child
    invokes only ``execution_dispatch`` and verifies the resolved target against
    the dispatcher's same-module ``EXECUTION_ROUTES`` table.
    """
    bindings = tuple(contract.get("execution_paths", ()))
    expected = {
        (path.action_id, path.mode): path
        for path in discovered_paths
        if path.discovered and path.mode != "provider-hosted-tool"
    }
    seen_keys = set()
    seen_path_ids = set()
    validated: List[Mapping[str, str]] = []
    for binding in bindings:
        action_id = binding["action_id"]
        mode = binding["mode"]
        path_id = binding["path_id"]
        key = (action_id, mode)
        if key in seen_keys or path_id in seen_path_ids:
            raise ProbeContractError(
                f"duplicate execution path binding for {action_id!r}/{mode!r} "
                f"({path_id!r})"
            )
        seen_keys.add(key)
        seen_path_ids.add(path_id)

        path = expected.get(key)
        if path is None or path.path_id != path_id:
            raise ProbeContractError(
                f"execution path binding {action_id!r}/{mode!r}/{path_id!r} "
                "does not match assessor-discovered PathRecord"
            )
        validated.append(binding)

    missing = sorted(set(expected) - seen_keys)
    if missing:
        action_id, mode = missing[0]
        raise ProbeContractError(
            f"unlisted assessor-discovered path for {action_id!r}/{mode!r}; "
            "every discovered path requires one execution_paths binding"
        )
    if len(validated) != len(expected):
        raise ProbeContractError(
            "probe contract binds a path outside the assessor-discovered inventory"
        )
    return tuple(validated)


def _missing_ancestor_dirs(ledger_dir: Path) -> List[Path]:
    """Return *ledger_dir* and any ancestors that do not yet exist.

    Ordered deepest-first — exactly the directories a subsequent
    ``mkdir(parents=True)`` would create. Used so a probe run can clean
    up, once it is done, only the specific directories it actually
    created — never a directory that already existed before the run
    (the loop stops the moment it reaches one), and never anything
    outside the target root (``load_probe_contract`` already rejects an
    ``observation_ledger`` whose directory would resolve outside root
    before this is ever called).
    """
    missing: List[Path] = []
    current = ledger_dir
    while not current.exists():
        missing.append(current)
        current = current.parent
    return missing


def _remove_created_dirs(created_dirs: List[Path]) -> None:
    """Best-effort remove *created_dirs* (deepest-first), only if empty.

    Never forces a removal: if a directory is not empty (this run's own
    ledger file was not the only thing in it, for whatever reason) its
    ``rmdir()`` simply fails and is silently skipped, so this can never
    destroy anything a probe run did not itself create.
    """
    for directory in created_dirs:
        try:
            directory.rmdir()
        except OSError:
            pass


def run_application_probe(root: Path, case: ProbeCase) -> ProbeResult:
    """Drive one probe *case* through the target's real dispatch seam.

    Loads and validates the probe contract, rejects a case naming an
    action or fault the contract/harness does not know about, then runs
    the isolated-subprocess protocol described in the module docstring.
    Never mutates the target repository or any customer state — the
    contract's ``side_effect_mode`` guarantees that, ``load_probe_contract``
    rejects an ``observation_ledger`` that would resolve outside *root*,
    and any directory this call itself has to create to hold the
    observation ledger is removed again once the run completes.
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
    created_dirs = _missing_ancestor_dirs(ledger_dir)
    try:
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_fd, ledger_name = tempfile.mkstemp(
            dir=str(ledger_dir),
            prefix=f".probe-{case.probe_id}-",
            suffix=".jsonl",
        )
        os.close(ledger_fd)
    except OSError as error:
        _remove_created_dirs(created_dirs)
        raise ProbeToolingError(
            f"cannot create the exclusive observation ledger for probe "
            f"{case.probe_id!r}: {error}"
        ) from error

    ledger_path = Path(ledger_name)
    try:
        outcome = _dispatch_child(root_path, contract, case, ledger_path)
    finally:
        ledger_path.unlink(missing_ok=True)
        _remove_created_dirs(created_dirs)

    return _build_probe_result(
        case,
        outcome,
        str(contract["observation_ledger"]),
        str(contract["dispatch"]),
    )


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
            case = ProbeCase(
                probe_id,
                action_id,
                fault,
                _ENFORCEMENT_PROBE_ARGUMENTS,
            )
            try:
                results.append(run_application_probe(root, case))
            except ProbeToolingError as error:
                raise PartialProbeToolingError(
                    str(error), partial_results=tuple(results)
                ) from error
    return tuple(results)


_PATH_PROBE_ID_PREFIX = "path-dispatch-"
_PATH_PROBE_DECISIONS = ("allow", "deny")
_PATH_PROBE_IDS = frozenset(
    f"{_PATH_PROBE_ID_PREFIX}{decision}" for decision in _PATH_PROBE_DECISIONS
)
_PATH_PROBE_EXPECTED = "pre_action_decision_before_invocation_or_deny"
_PATH_PRE_DECISION_KIND = "path-pre-action-decision"
_PATH_INVOCATION_KIND = "path-tool-invocation"
_PATH_RESOLVED_KIND = "path-resolved-function"
_PATH_ROUTING_TARGET_KIND = "path-routing-table-target"
_PATH_PROOF_SOURCE = "assessor:execution-path-proof-channel"
_PATH_CHILD_ARG = "--path-child"
PATH_PROBE_IDS = _PATH_PROBE_IDS

_PATH_CHILD_ERROR_REASONS = {
    "startup_failed": "path-dispatch-startup-failed",
    "timeout": "path-dispatch-timeout",
    "nonzero_exit": "path-dispatch-nonzero-exit",
    "malformed_output": "path-dispatch-malformed-output",
    "proof_timeout": "path-proof-channel-timeout",
    "proof_oversized": "path-proof-channel-oversized",
}


def _build_path_probe_result(
    binding: Mapping[str, str],
    outcome: Mapping[str, object],
    ledger_source: str,
    probe_id: str,
) -> ProbeResult:
    action_id = binding["action_id"]
    mode = binding["mode"]
    path_id = binding["path_id"]
    correlated = [
        event
        for event in outcome["events"]
        if event.get("action_id") == action_id
        and event.get("mode") == mode
        and event.get("path_id") == path_id
    ]
    decision_index = next(
        (
            index
            for index, event in enumerate(correlated)
            if event.get("event") == "pre_action_decision"
        ),
        None,
    )
    invocation_index = next(
        (
            index
            for index, event in enumerate(correlated)
            if event.get("event") == "invocation"
        ),
        None,
    )
    decision = (
        correlated[decision_index].get("decision")
        if decision_index is not None
        else None
    )
    ledger_resolved_paths = {
        str(event["resolved_path"])
        for event in correlated
        if event.get("event") == "resolved_path"
        and isinstance(event.get("resolved_path"), str)
    }
    ledger_expected_paths = {
        str(event["expected_resolved_path"])
        for event in correlated
        if event.get("event") == "routing_target"
        and isinstance(event.get("expected_resolved_path"), str)
    }
    reported_resolved_path = (
        str(outcome["resolved_path"])
        if isinstance(outcome.get("resolved_path"), str)
        else None
    )
    reported_expected_path = (
        str(outcome["expected_resolved_path"])
        if isinstance(outcome.get("expected_resolved_path"), str)
        else None
    )
    identity_mismatch = (
        len(ledger_resolved_paths) > 1
        or len(ledger_expected_paths) > 1
        or (
            bool(ledger_resolved_paths)
            and bool(ledger_expected_paths)
            and ledger_resolved_paths != ledger_expected_paths
        )
        or (
            reported_resolved_path is not None
            and bool(ledger_resolved_paths)
            and reported_resolved_path not in ledger_resolved_paths
        )
        or (
            reported_expected_path is not None
            and bool(ledger_expected_paths)
            and reported_expected_path not in ledger_expected_paths
        )
        or (
            reported_resolved_path is not None
            and reported_expected_path is not None
            and reported_resolved_path != reported_expected_path
        )
    )
    identity_unobservable = (
        len(ledger_resolved_paths) != 1
        or len(ledger_expected_paths) != 1
    )
    child_error = outcome.get("child_error")

    if invocation_index is not None and decision == "deny":
        status = "must-fix"
        reason_code = "ENF-002"
        observed = "tool_invoked_despite_deny"
    elif invocation_index is not None and (
        decision_index is None or decision_index > invocation_index
    ):
        status = "must-fix"
        reason_code = "ENF-002"
        observed = "tool_invoked_without_pre_action_decision"
    elif identity_mismatch:
        status = "must-fix"
        reason_code = "ENF-002"
        observed = "resolved_path_identity_mismatch"
    elif child_error is not None:
        status = "not-verified"
        reason_code = _PATH_CHILD_ERROR_REASONS.get(
            str(child_error), "path-dispatch-unobservable"
        )
        observed = f"path_dispatch_child_error:{child_error}"
    elif identity_unobservable:
        status = "not-verified"
        reason_code = "path-dispatch-unobservable"
        observed = "resolved_path_identity_unobservable"
    elif invocation_index is not None and decision in ("allow", "transform"):
        status = "pass"
        reason_code = "path-dispatch-mediated"
        observed = "pre_action_decision_before_invocation"
    elif decision_index is not None and decision == "deny":
        status = "pass"
        reason_code = "path-dispatch-denied"
        observed = "deny_decision_without_invocation"
    else:
        status = "not-verified"
        reason_code = "path-dispatch-unobservable"
        observed = "path_dispatch_did_not_reach_decision_or_tool"

    evidence_events = [
        event
        for event in correlated
        if event.get("event")
        in ("routing_target", "resolved_path", "pre_action_decision", "invocation")
        and isinstance(event.get("evidence_id"), str)
    ]
    evidence_refs = tuple(str(event["evidence_id"]) for event in evidence_events)
    evidence_items = tuple(
        _record_evidence(
            str(event["evidence_id"]),
            (
                {
                    "routing_target": _PATH_ROUTING_TARGET_KIND,
                    "resolved_path": _PATH_RESOLVED_KIND,
                    "pre_action_decision": _PATH_PRE_DECISION_KIND,
                    "invocation": _PATH_INVOCATION_KIND,
                }[str(event["event"])]
            ),
            ledger_source,
            {
                key: value
                for key, value in event.items()
                if key != "proof_nonce"
            },
        )
        for event in evidence_events
    )
    return ProbeResult(
        probe_id=probe_id,
        action_id=action_id,
        path_id=path_id,
        mode=mode,
        status=status,
        reason_code=reason_code,
        expected=_PATH_PROBE_EXPECTED,
        observed=observed,
        evidence_refs=evidence_refs,
        evidence_items=evidence_items,
    )


def run_execution_path_probe_set(
    root: Path, discovered_paths: Sequence["PathRecord"]
) -> Tuple[ProbeResult, ...]:
    """Execute every bound action/mode through the application dispatcher."""
    root_path = Path(root).resolve()
    contract = load_probe_contract(root_path)
    bindings = validate_execution_paths(contract, discovered_paths)
    ledger_dir = root_path / Path(contract["observation_ledger"]).parent
    created_dirs = _missing_ancestor_dirs(ledger_dir)
    try:
        ledger_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _remove_created_dirs(created_dirs)
        raise ProbeToolingError(
            f"cannot create execution-path observation ledger directory: {error}"
        ) from error

    results: List[ProbeResult] = []
    try:
        for binding in sorted(
            bindings,
            key=lambda item: (item["action_id"], item["mode"], item["path_id"]),
        ):
            for decision in _PATH_PROBE_DECISIONS:
                target_ledger_path: Optional[Path] = None
                try:
                    target_ledger_fd, target_ledger_name = tempfile.mkstemp(
                        dir=str(ledger_dir),
                        prefix=f".path-target-{binding['path_id']}-{decision}-",
                        suffix=".jsonl",
                    )
                    os.close(target_ledger_fd)
                    target_ledger_path = Path(target_ledger_name)
                except OSError as error:
                    if target_ledger_path is not None:
                        target_ledger_path.unlink(missing_ok=True)
                    raise PartialProbeToolingError(
                        f"cannot create execution-path observation ledger: {error}",
                        partial_results=tuple(results),
                    ) from error
                try:
                    assert target_ledger_path is not None
                    try:
                        outcome = _dispatch_path_child(
                            root_path,
                            contract,
                            binding,
                            target_ledger_path,
                            decision,
                        )
                    except ProbeToolingError as error:
                        raise PartialProbeToolingError(
                            str(error), partial_results=tuple(results)
                        ) from error
                    results.append(
                        _build_path_probe_result(
                            binding,
                            outcome,
                            _PATH_PROOF_SOURCE,
                            f"{_PATH_PROBE_ID_PREFIX}{decision}",
                        )
                    )
                finally:
                    if target_ledger_path is not None:
                        target_ledger_path.unlink(missing_ok=True)
    finally:
        _remove_created_dirs(created_dirs)
    return tuple(results)


def findings_from_probes(
    probes: Tuple[ProbeResult, ...], phase: str = "pre-deploy"
) -> Tuple[Finding, ...]:
    """Turn non-passing probe results into catalog findings.

    A passing, ``not-applicable``, or ``not-verified`` probe never
    produces a finding: only ``must-fix``/``should-fix`` probes that
    proved a concrete, truthful violation do. ``not-verified`` in
    particular is never mislabeled as ``ENF-001`` (or any other
    catalog finding) merely because its ``reason_code`` is
    unrecognized — an outcome that genuinely cannot be confirmed either
    way is never turned into an invented violation. A probe's
    ``reason_code`` is expected to already be the exact catalog finding
    ID (``ENF-001``/``ENF-002``) once it *did* fail to pass; anything
    else is defensively mapped to ``ENF-001`` rather than silently
    dropped, since an unrecognized non-passing, non-not-verified probe
    is never simply ignored.
    """
    findings = []
    for probe in probes:
        if probe.probe_id in _PATH_PROBE_IDS:
            # Path dispatch receipts are aggregated into MED-001/MED-002 by
            # mediation.apply_execution_receipts. Emitting an ENF finding
            # here as well would double-count the same observed bypass.
            continue
        if probe.status in ("pass", "not-applicable", "not-verified"):
            continue
        template = _FINDING_TEMPLATES.get(probe.reason_code)
        finding_id = probe.reason_code if template is not None else "ENF-001"
        template = template or _FINDING_TEMPLATES["ENF-001"]
        findings.append(
            Finding(
                finding_id=finding_id,
                status=probe.status,
                phase=phase,
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


def _completed_report_is_ledger_supported(
    case: ProbeCase, report: Mapping[str, object], events: List[Mapping[str, object]]
) -> bool:
    """Whether a completed child's self-report is corroborated by the ledger.

    A completed, well-formed report is only ever trusted alongside a
    matching ``start`` record for *case*'s action and a ``decision``
    record whose ``decision`` field agrees with what the report claims,
    plus at least one audit id on the report itself. A self-report with
    none of that correlated evidence is indistinguishable from a stub
    that never actually drove the dispatch seam, and must never be
    treated as observable on its own — see ``_build_probe_result``.
    """
    decision = report.get("decision")
    has_start = any(
        event.get("event") == "start" and event.get("action_id") == case.action_id
        for event in events
    )
    has_matching_decision = any(
        event.get("event") == "decision"
        and event.get("action_id") == case.action_id
        and event.get("decision") == decision
        for event in events
    )
    has_audit_id = bool(report.get("audit_ids"))
    return has_start and has_matching_decision and has_audit_id


def _digest_evidence(digest: str, kind: str, source: str) -> ProbeEvidence:
    """Provenance for an evidence id that *is itself* a content digest.

    The target's own canonical ``sha256:`` argument/binding digests are
    already the hash of the exact bytes the probe observed, so the id is
    reused verbatim as ``sha256`` -- never re-hashed, and never derived
    from the id string as if it were arbitrary text.
    """
    return ProbeEvidence(evidence_id=digest, kind=kind, source=source, sha256=digest)


def _record_evidence(
    evidence_id: str, kind: str, source: str, record: Mapping[str, object]
) -> ProbeEvidence:
    """Provenance for an evidence id carried by an observed *record*.

    ``sha256`` is the canonical digest of the actual record the probe
    read back (a ledger event, an audit record, or a child's own
    validated self-report envelope) -- real observed content, never the
    id string and never a fabricated placeholder. The record itself is
    only ever hashed here, never retained or returned.
    """
    return ProbeEvidence(
        evidence_id=evidence_id,
        kind=kind,
        source=source,
        sha256="sha256:" + canonical.sha256_hex(canonical.canonical_bytes(dict(record))),
    )


def _evidence_items_for(
    outcome: Mapping[str, object],
    refs: Tuple[str, ...],
    ledger_source: str,
    report_source: str,
) -> Tuple[ProbeEvidence, ...]:
    """Bind each id in *refs* to the artifact this run actually observed.

    Every id an application probe cites comes from exactly one of three
    real observed places: a canonical ``sha256:`` argument hash (the
    target's own digest of what the synthetic tool received), a durable
    ``"audit"`` record in the observation ledger, or the completed
    child's own validated self-report envelope. Each is bound to the
    corresponding artifact here, hashing the real record rather than the
    id. An id that resolves to none of them yields no entry at all --
    deliberately, so an orchestrator sees an unresolvable citation
    instead of invented provenance.
    """
    events = list(outcome["events"])
    report = outcome["child_report"]
    ledger_digests = {
        digest
        for event in events
        if event.get("event") == "invocation"
        for digest in (event.get("argument_hash"), event.get("original_argument_hash"))
        if digest
    }
    items: List[ProbeEvidence] = []
    for ref in refs:
        if ref.startswith("sha256:"):
            items.append(
                _digest_evidence(
                    ref,
                    "probe-argument-hash",
                    ledger_source if ref in ledger_digests else report_source,
                )
            )
            continue
        event = next(
            (
                event
                for event in events
                if event.get("event") == "audit" and event.get("audit_id") == ref
            ),
            None,
        )
        if event is not None:
            items.append(
                _record_evidence(ref, "probe-audit-ledger-record", ledger_source, event)
            )
        elif report is not None and ref in tuple(report.get("audit_ids", ()) or ()):
            items.append(
                _record_evidence(
                    ref, "probe-self-reported-audit-record", report_source, report
                )
            )
    return tuple(items)


def _abnormal_evidence_refs(case: ProbeCase, outcome: Mapping[str, object]) -> Tuple[str, ...]:
    """Payload-free evidence for an abnormal (crash/timeout/malformed) outcome.

    Never inferred from silence: collects the invocation argument hash
    (when the tool was reached), every durable ``"audit"`` ledger event
    id recorded for *case*'s action (the only channel that survives a
    killed child — the self-report's ``AUDIT_EVENTS`` drain never
    happens for one), and, when the child *did* complete with a
    well-formed report (a malformed-verdict completion), its own
    self-reported audit ids too. An empty result here means no visible
    operational signal was ever recorded, which ``_build_probe_result``
    treats as insufficient for a pass — never a reason to fabricate one.
    """
    refs = set()
    invocation_hash = outcome.get("invocation_argument_hash")
    if invocation_hash:
        refs.add(invocation_hash)
    for event in outcome["events"]:
        if event.get("event") == "audit" and event.get("action_id") == case.action_id:
            audit_id = event.get("audit_id")
            if audit_id:
                refs.add(audit_id)
    report = outcome["child_report"]
    if report is not None:
        for audit_id in report.get("audit_ids", ()):
            if audit_id:
                refs.add(audit_id)
    return tuple(sorted(refs))


def _build_probe_result(
    case: ProbeCase,
    outcome: Mapping[str, object],
    ledger_source: str,
    report_source: str,
) -> ProbeResult:
    expected = _EXPECTED_BY_FAULT[case.fault]
    invoked = bool(outcome["invoked"])
    report = outcome["child_report"]

    # A *completed* child (exited zero, produced a well-formed JSON
    # envelope) is the only run whose own self-report is ever trusted at
    # all — and even then, only when its decision is one of the
    # recognized deny/transform values. A well-formed envelope whose
    # decision falls outside that schema is exactly what "malformed
    # verdict" means at the governance level, and is classified with
    # the same abnormal-run handling below rather than trusted as a
    # clean pass merely because the process happened to exit zero.
    if report is not None and report.get("decision") in _RECOGNIZED_DECISIONS:
        # Even a well-formed, recognized-decision self-report is never
        # sufficient by itself: it must be corroborated by a matching
        # ledger start/decision record and at least one audit id, or
        # the outcome is unobservable — no different, in principle,
        # from a crashed child that recorded no ledger evidence at all.
        if not _completed_report_is_ledger_supported(
            case, report, outcome["events"]
        ):
            raise ProbeToolingError(
                f"probe {case.probe_id!r} self-reported "
                f"{report.get('decision')!r} but the observation ledger "
                "does not corroborate it with a matching start/decision "
                "record and at least one audit id — a self-report is "
                "never trusted on its own"
            )

        decision = report["decision"]
        original_argument_hash = None
        if decision == "deny":
            observed = "tool_invoked_despite_deny" if invoked else "tool_not_invoked"
        else:  # decision == "transform"
            # Never trust the self-reported invocation count, and never
            # judge only the first (or last) recorded invocation: count
            # every ledger "invocation" event for this action and
            # compare both that count and the reported count against
            # it. Exactly one recorded invocation, matching the
            # self-report, is the only shape a correct transform can
            # ever take — zero, or more than one (an extra raw
            # invocation this fixture never admits to), or a
            # self-reported count that disagrees with the ledger, are
            # all a completed-report/ledger mismatch (ENF-001), never a
            # false pass.
            invocation_events = [
                event
                for event in outcome["events"]
                if event.get("event") == "invocation"
                and event.get("action_id") == case.action_id
            ]
            ledger_invocation_count = len(invocation_events)
            if invocation_events:
                original_argument_hash = invocation_events[0].get(
                    "original_argument_hash"
                )
            if ledger_invocation_count == 0:
                observed = "tool_not_invoked"
            elif ledger_invocation_count > 1:
                observed = "multiple_tool_invocations"
            elif report.get("invocation_count") != ledger_invocation_count:
                observed = "invocation_count_mismatch"
            elif outcome.get("invocation_argument_hash") != report.get(
                "argument_hash"
            ):
                observed = "argument_hash_mismatch"
            elif original_argument_hash is None:
                # A single, self-report-consistent invocation is not
                # enough on its own: the ledger's own "invocation"
                # record for this action never recorded an
                # "original_argument_hash" at all — incomplete
                # mandatory evidence, indistinguishable from a buggy or
                # tampered ledger writer. Without it there is no way to
                # prove the tool actually received something different
                # from the case's original arguments, so this can never
                # be laundered into a pass (or even a truthful
                # "not-verified" no-op finding) merely because the rest
                # of the self-report looks clean.
                raise ProbeToolingError(
                    f"probe {case.probe_id!r} self-reported a completed "
                    "'transform' decision, but the observation ledger's own "
                    "invocation record for this action is missing the "
                    "mandatory 'original_argument_hash' field — incomplete "
                    "ledger evidence is never trusted as proof a transform "
                    "actually happened, and is never treated as a pass"
                )
            elif original_argument_hash == outcome.get("invocation_argument_hash"):
                # Self-report/ledger consistency alone is never
                # sufficient proof of a transform: the ledger's own
                # "original_argument_hash" for this invocation is
                # byte-identical to the hash the tool actually
                # received, meaning nothing observably changed at all.
                # Whether that is a definite violation or merely
                # unconfirmed depends on whether this case's own
                # arguments are known — by this harness's fixed
                # reference-argument contract, not by embedding any
                # fixture-specific transform policy — to require an
                # actual change.
                if dict(case.arguments) == dict(
                    _TRANSFORM_REQUIRED_REFERENCE_ARGUMENTS
                ):
                    observed = _TRANSFORM_NO_OP_REQUIRED_OBSERVED
                else:
                    observed = _TRANSFORM_NO_OP_UNVERIFIED_OBSERVED
            else:
                observed = "tool_received_transformed_arguments"

        if observed == _TRANSFORM_NO_OP_UNVERIFIED_OBSERVED:
            # Truthfully unconfirmed, never an invented ENF-001: this
            # generic harness cannot know whether an arbitrary case's
            # arguments were already policy-compliant (a legitimate
            # no-op) or the transform was simply never applied (a real
            # bug) without embedding the target's own business rules.
            status = "not-verified"
            reason_code = _NOT_VERIFIED_REASON
        else:
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
                    for ref in (
                        report.get("argument_hash"),
                        original_argument_hash,
                        *report.get("audit_ids", ()),
                    )
                    if ref
                }
            )
        )
        return ProbeResult(
            probe_id=case.probe_id,
            action_id=case.action_id,
            path_id=case.path_id,
            status=status,
            reason_code=reason_code,
            expected=expected,
            observed=observed,
            evidence_refs=evidence_refs,
            evidence_items=_evidence_items_for(
                outcome, evidence_refs, ledger_source, report_source
            ),
        )

    # Every other outcome is abnormal: the child crashed, timed out,
    # wrote unparseable output, or completed but reported a
    # semantically invalid ("malformed") verdict. Only the ledger can
    # prove reality here — a killed, crashed, or schema-violating child
    # is never trusted to self-report. Fail-closed (the ledger proves
    # the tool was never reached) still passes: a process dying or
    # reporting nonsense is not itself a violation as long as it also
    # never let the action through.
    if not outcome["ledger_observable"]:
        raise ProbeToolingError(
            f"probe {case.probe_id!r} outcome is unobservable: the child "
            f"process failed ({outcome['child_error']}) and the "
            "observation ledger recorded no evidence the interceptor was "
            "ever reached"
        )

    if invoked:
        if expected == _TRANSFORM_EXPECTED_OUTCOME:
            # This fault's own contract expects the tool to be reached
            # on a normal completion (a transform-family fault) — the
            # ledger proving reach is not itself proof of fail-open,
            # since reaching the tool was never forbidden in the first
            # place. Only a completed, well-formed self-report can be
            # compared against the ledger's argument hash to confirm
            # the transform was actually correct; an incomplete run
            # cannot prove that either way. Fabricating ENF-002 here
            # would falsely accuse a run that may well have transformed
            # correctly and only failed afterwards.
            observed = "tool_invoked_but_outcome_unverified"
            status = "not-verified"
            reason_code = _NOT_VERIFIED_REASON
        else:
            # This fault's own contract forbids invocation on a normal
            # completion (deny/crash/timeout/malformed-verdict
            # families) — the ledger proving reach anyway is always a
            # completed fail-open finding, unconditionally, regardless
            # of which abnormal shape produced it.
            observed = "tool_invoked_despite_fault"
            status = "must-fix"
            reason_code = "ENF-002"
        evidence_refs = _abnormal_evidence_refs(case, outcome)
    else:
        observed = "tool_not_invoked"
        if expected == _TRANSFORM_EXPECTED_OUTCOME:
            # A transform-family fault's incomplete run that never even
            # reached the tool proves nothing either way: reaching the
            # tool is what a *correct* run of this fault is supposed to
            # do, so an incomplete run that never got that far can
            # neither confirm a pass nor be blamed for a violation it
            # never demonstrably committed. Truthfully "not-verified",
            # exactly like the invoked-but-incomplete case above — never
            # a fabricated ENF-001 must-fix.
            status = "not-verified"
            reason_code = _NOT_VERIFIED_REASON
            evidence_refs = _abnormal_evidence_refs(case, outcome)
        elif observed == expected:
            # A candidate fail-closed pass (deny/crash/timeout/
            # malformed-verdict families) is never granted on silence
            # alone: it must be backed by a visible operational audit
            # signal the ledger recorded (or, for a well-formed but
            # malformed-verdict completion, the self-reported audit
            # ids) — a "start" record by itself is not evidence of
            # anything beyond "the process began". No success fallback:
            # if neither exists, the outcome cannot be proven
            # fail-closed with confidence, and this raises
            # ProbeToolingError instead of silently becoming a pass.
            evidence_refs = _abnormal_evidence_refs(case, outcome)
            if not evidence_refs:
                raise ProbeToolingError(
                    f"probe {case.probe_id!r} appears fail-closed (the "
                    "tool was never reached) but the observation ledger "
                    "recorded no visible operational audit signal to "
                    "corroborate it — a pass is never granted on "
                    "silence alone"
                )
            status = "pass"
            reason_code = _PASS_REASON_BY_FAULT.get(case.fault, _DEFAULT_PASS_REASON)
        else:
            # Defensive fallback, not currently reachable for any known
            # fault: every non-transform-family fault's "expected" value
            # is exactly "tool_not_invoked", which is also what
            # "observed" is unconditionally set to right above whenever
            # "invoked" is false.
            status = "must-fix"
            reason_code = "ENF-001"
            evidence_refs = _abnormal_evidence_refs(case, outcome)

    return ProbeResult(
        probe_id=case.probe_id,
        action_id=case.action_id,
        path_id=case.path_id,
        status=status,
        reason_code=reason_code,
        expected=expected,
        observed=observed,
        evidence_refs=evidence_refs,
        evidence_items=_evidence_items_for(
            outcome, evidence_refs, ledger_source, report_source
        ),
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
        assert process.stdin is not None  # narrows Optional for mypy/readers
        process.stdin.write(stdin_bytes)
        process.stdin.close()
    except (OSError, ValueError):
        # A child that crashed before ever reading stdin (e.g. an
        # unresolvable dispatch reference) can close its end of the pipe
        # first; the ready-handshake wait below still correctly resolves
        # this to an abnormal/unobservable outcome.
        pass

    ready = _wait_for_child_ready(process, _CHILD_READY_TIMEOUT_S)
    if not ready:
        # Never became ready within the generous, harness-level startup
        # bound — hung, crashed, or produced something other than the
        # exact marker before ever reaching the dispatch callable. This
        # is never charged against the contract's application timeout,
        # and the ledger-based classification below decides the rest.
        process.kill()
        _reap_killed_child(process)
        child_error = "startup_failed"
    else:
        try:
            remaining_stdout, _stderr_bytes = process.communicate(
                timeout=contract["timeout_ms"] / 1000.0
            )
            stdout_bytes = remaining_stdout
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            stdout_bytes = _reap_killed_child(process)
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
        "events": events,
    }


def _decode_path_proof_events(raw: bytes, proof_nonce: str) -> List[Mapping[str, object]]:
    events: List[Mapping[str, object]] = []
    expected_nonce = proof_nonce.encode("ascii")
    for line in raw.splitlines():
        try:
            parsed = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(parsed, Mapping):
            continue
        event_nonce = parsed.get("proof_nonce")
        if not isinstance(event_nonce, str):
            continue
        try:
            event_nonce_bytes = event_nonce.encode("ascii")
        except UnicodeEncodeError:
            continue
        if (
            len(event_nonce_bytes) == len(expected_nonce)
            and all(byte in b"0123456789abcdef" for byte in event_nonce_bytes)
            and secrets.compare_digest(event_nonce_bytes, expected_nonce)
        ):
            events.append(parsed)
    return events


def _read_all_fd(fd: int) -> bytes:
    collected = bytearray()
    deadline = time.monotonic() + _PATH_PROOF_DRAIN_TIMEOUT_S
    os.set_blocking(fd, False)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProofChannelReadError(
                "execution-path proof pipe drain exceeded its fixed deadline",
                reason="timeout",
                partial_bytes=bytes(collected),
            )
        try:
            readable, _, _ = select.select([fd], [], [], remaining)
        except InterruptedError:
            continue
        if not readable:
            raise ProofChannelReadError(
                "execution-path proof pipe drain exceeded its fixed deadline",
                reason="timeout",
                partial_bytes=bytes(collected),
            )
        try:
            chunk = os.read(
                fd,
                min(65_536, _MAX_PATH_PROOF_BYTES - len(collected) + 1),
            )
        except (BlockingIOError, InterruptedError):
            continue
        if not chunk:
            return bytes(collected)
        available = _MAX_PATH_PROOF_BYTES - len(collected)
        collected.extend(chunk[:available])
        if len(chunk) > available:
            raise ProofChannelReadError(
                "execution-path proof pipe exceeded its fixed byte limit",
                reason="oversized",
                partial_bytes=bytes(collected),
            )


def _dispatch_path_child(
    root_path: Path,
    contract: Mapping[str, object],
    binding: Mapping[str, str],
    target_ledger_path: Path,
    decision: str,
) -> Mapping[str, object]:
    proof_nonce = secrets.token_hex(32)
    proof_read_fd: Optional[int] = None
    proof_write_fd: Optional[int] = None
    proof_path: Optional[Path] = None
    proof_directory: Optional[tempfile.TemporaryDirectory[str]] = None
    popen_extra: Mapping[str, object] = {}
    try:
        if os.name == "posix":
            proof_read_fd, proof_write_fd = os.pipe()
            popen_extra = {"pass_fds": (proof_write_fd,)}
        else:
            proof_base = Path(tempfile.gettempdir()).resolve()
            try:
                proof_base.relative_to(root_path)
            except ValueError:
                pass
            else:
                raise ProbeToolingError(
                    "assessor proof directory would resolve inside the target root"
                )
            proof_directory = tempfile.TemporaryDirectory(
                prefix="threadlight-assessor-path-proof-",
                dir=str(proof_base),
            )
            proof_path = (
                Path(proof_directory.name).resolve()
                / f"{secrets.token_hex(32)}.jsonl"
            )
            proof_fd = os.open(
                proof_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(proof_fd)
    except OSError as error:
        if proof_directory is not None:
            proof_directory.cleanup()
        raise ProbeToolingError(
            f"cannot create assessor execution-path proof channel: {error}"
        ) from error

    stdin_bytes = canonical.canonical_bytes(
        {
            "binding": dict(binding),
            "declared_routes": [
                [item["action_id"], item["mode"]]
                for item in contract["execution_paths"]
            ],
            "decision": decision,
            "execution_dispatch": contract["execution_dispatch"],
            "proof_fd": proof_write_fd,
            "proof_path": str(proof_path) if proof_path is not None else None,
            "proof_nonce": proof_nonce,
            "target_ledger_path": str(target_ledger_path),
        }
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(root_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    proof_bytes = b""
    proof_read_error: Optional[ProofChannelReadError] = None
    try:
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed, trusted argv; no shell
                [sys.executable, str(THIS_FILE), _PATH_CHILD_ARG],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=str(root_path),
                **popen_extra,
            )
        except OSError as error:
            raise ProbeToolingError(
                f"cannot start isolated execution-path probe subprocess: {error}"
            ) from error
        finally:
            if proof_write_fd is not None:
                os.close(proof_write_fd)
                proof_write_fd = None

        try:
            assert process.stdin is not None
            process.stdin.write(stdin_bytes)
            process.stdin.close()
        except (OSError, ValueError):
            pass

        child_error: Optional[str] = None
        stdout_bytes = b""
        exit_code: Optional[int] = None
        if not _wait_for_child_ready(process, _CHILD_READY_TIMEOUT_S):
            process.kill()
            _reap_killed_child(process)
            child_error = "startup_failed"
        else:
            try:
                stdout_bytes, _stderr_bytes = process.communicate(
                    timeout=contract["timeout_ms"] / 1000.0
                )
                exit_code = process.returncode
            except subprocess.TimeoutExpired:
                process.kill()
                _reap_killed_child(process)
                child_error = "timeout"
            else:
                if exit_code != 0:
                    child_error = "nonzero_exit"
    finally:
        if proof_write_fd is not None:
            os.close(proof_write_fd)
        if proof_read_fd is not None:
            try:
                try:
                    proof_bytes = _read_all_fd(proof_read_fd)
                except ProofChannelReadError as error:
                    proof_bytes = error.partial_bytes
                    proof_read_error = error
            finally:
                os.close(proof_read_fd)
        elif proof_path is not None:
            try:
                proof_bytes = proof_path.read_bytes()
            except OSError:
                proof_bytes = b""
        if proof_directory is not None:
            proof_directory.cleanup()

    if proof_read_error is not None:
        child_error = f"proof_{proof_read_error.reason}"
    report: Optional[Mapping[str, object]] = None
    if child_error is None:
        try:
            parsed = json.loads(stdout_bytes.decode("utf-8"))
            _validate_path_child_report(parsed)
            report = parsed
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            child_error = "malformed_output"
    events = _decode_path_proof_events(proof_bytes, proof_nonce)
    resolved_path = report.get("resolved_path") if report is not None else None
    expected_resolved_path = (
        report.get("expected_resolved_path") if report is not None else None
    )
    if resolved_path is None:
        resolved_path = next(
            (
                event.get("resolved_path")
                for event in events
                if event.get("event") == "resolved_path"
            ),
            None,
        )
    if expected_resolved_path is None:
        expected_resolved_path = next(
            (
                event.get("expected_resolved_path")
                for event in events
                if event.get("event") == "routing_target"
            ),
            None,
        )
    return {
        "events": events,
        "child_error": child_error,
        "resolved_path": resolved_path,
        "expected_resolved_path": expected_resolved_path,
    }


def _reap_killed_child(process: "subprocess.Popen[bytes]") -> bytes:
    """Drain and reap an already-killed *process*, bounded defensively.

    Once a process has been killed, draining its pipes and reaping its
    exit status should return almost instantly — but this is bounded by
    the same generous, harness-level startup allowance rather than left
    to block forever, in case of an exceptional wedged pipe. Any bytes
    that could not be drained within the bound are simply discarded:
    the process is already dead, and the ledger — not stdout — is what
    the classification in ``_build_probe_result`` trusts for a killed
    child.
    """
    try:
        stdout_bytes, _stderr_bytes = process.communicate(
            timeout=_CHILD_READY_TIMEOUT_S
        )
        return stdout_bytes
    except subprocess.TimeoutExpired:
        return b""


def _wait_for_child_ready(process: "subprocess.Popen[bytes]", timeout_s: float) -> bool:
    """Block until *process* writes the exact ready marker to stdout.

    Runs the (blocking) fixed-length read in a background thread so it
    can be bounded by *timeout_s* even though the underlying read itself
    cannot be interrupted directly; a hung or crashed child still
    reliably unblocks the thread once ``process.kill()`` closes its end
    of the pipe. Returns ``True`` only if exactly the expected marker
    bytes were read — never on a short read (early EOF) or any other
    content.
    """
    assert process.stdout is not None  # narrows Optional for mypy/readers
    marker_length = len(_CHILD_READY_MARKER)
    result: List[object] = []

    def _read_marker() -> None:
        try:
            result.append(process.stdout.read(marker_length))
        except (OSError, ValueError) as error:  # pragma: no cover - defensive
            result.append(error)

    reader = threading.Thread(target=_read_marker, daemon=True)
    reader.start()
    reader.join(timeout_s)

    if reader.is_alive():
        # Still blocked reading after the generous startup bound: kill
        # the child so the pipe closes, then wait for the read to
        # actually unblock before touching its result — bounded
        # defensively rather than joined forever, in case an
        # exceptional wedged pipe ever kept it from unblocking even
        # after the kill.
        process.kill()
        reader.join(timeout_s)

    if not result or not isinstance(result[0], (bytes, bytearray)):
        return False
    return bytes(result[0]) == _CHILD_READY_MARKER

def _read_ledger_events(ledger_path: Path) -> list:
    """Read an observation/nonce ledger's JSONL events, tolerantly.

    A missing ledger file, or a line that fails to parse as JSON at
    all, is treated as *no evidence yet* — a killed child can leave a
    partial trailing line, and that must never be mistaken for a real
    recorded event. But a line that *does* parse as valid JSON while
    not being a JSON object (a bare string, number, boolean, ``null``,
    or array) is never silently accepted as an event either: every
    caller unconditionally calls ``.get(...)`` on each returned event,
    so returning anything non-mapping here would let a malformed or
    adversarial target crash a caller with a raw ``AttributeError``
    instead of a typed probe failure. Such a line raises
    :class:`ProbeToolingError` instead — the ledger is corrupt/
    malformed evidence, which is exactly as unobservable as no
    evidence at all, never a silent pass.
    """
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
            parsed = json.loads(line)
        except json.JSONDecodeError:
            # A killed child can leave a partial trailing line; ignore
            # it rather than let it look like a real recorded event.
            continue
        if not isinstance(parsed, Mapping):
            raise ProbeToolingError(
                f"observation ledger {ledger_path} contains a malformed "
                f"event that is valid JSON but not a JSON object: "
                f"{parsed!r}"
            )
        events.append(parsed)
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


def _validate_path_child_report(report: object) -> None:
    if not isinstance(report, dict) or set(report) != set(_REQUIRED_PATH_REPORT_KEYS):
        raise ValueError(
            "execution-path child report must contain exactly "
            f"{_REQUIRED_PATH_REPORT_KEYS!r}"
        )
    for key in _REQUIRED_PATH_REPORT_KEYS:
        if not isinstance(report[key], str) or not report[key]:
            raise ValueError(
                f"execution-path child report {key!r} must be a non-empty string"
            )


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

    # Interpreter/import startup is finished and the dispatch callable is
    # resolved: signal readiness *before* invoking it so the parent's
    # application-level timeout clock starts from here, not from process
    # launch. Nothing about this fault under test has happened yet.
    sys.stdout.buffer.write(_CHILD_READY_MARKER)
    sys.stdout.buffer.flush()

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


def _run_path_as_child() -> None:
    """Execute one application-routed path with assessor-owned namespaces."""
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    binding = payload["binding"]
    declared_routes = {
        (str(item[0]), str(item[1])) for item in payload["declared_routes"]
    }
    requested_decision = str(payload["decision"])
    proof_nonce = str(payload["proof_nonce"])
    proof_fd = payload.get("proof_fd")
    proof_path = payload.get("proof_path")
    if proof_fd is not None:
        if not isinstance(proof_fd, int) or isinstance(proof_fd, bool):
            raise TypeError("proof_fd must be an integer or null")
        os.set_inheritable(proof_fd, False)
    elif not isinstance(proof_path, str) or not proof_path:
        raise TypeError("proof_path must be a non-empty string when proof_fd is absent")
    target_ledger_path = str(payload["target_ledger_path"])
    action_id = str(binding["action_id"])
    mode = str(binding["mode"])
    path_id = str(binding["path_id"])
    dispatch_ref = str(payload["execution_dispatch"])
    module_name, attr = dispatch_ref.split(":", 1)
    module = importlib.import_module(module_name)
    dispatch = getattr(module, attr)

    def append_event(event: str, evidence_id: str, **fields: object) -> None:
        record = {
            "event": event,
            "evidence_id": evidence_id,
            "proof_nonce": proof_nonce,
            "action_id": action_id,
            "mode": mode,
            "path_id": path_id,
            **fields,
        }
        encoded = canonical.canonical_bytes(record) + b"\n"
        if proof_fd is not None:
            view = memoryview(encoded)
            while view:
                written = os.write(proof_fd, view)
                view = view[written:]
        else:
            assert isinstance(proof_path, str)
            with open(proof_path, "ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

    class _AwaitableMapping(dict):
        def __await__(self):
            async def resolved():
                return self

            return resolved().__await__()

    class _AwaitableNoop:
        def __await__(self):
            async def resolved():
                return None

            return resolved().__await__()

    class _SyntheticPathDenied(RuntimeError):
        pass

    class _SyntheticHooks:
        def pre_tool_call(self, *args: object, **kwargs: object) -> Mapping[str, object]:
            del args, kwargs
            append_event(
                "pre_action_decision",
                f"path-decision-{path_id}",
                decision=requested_decision,
            )
            if requested_decision == "deny":
                raise _SyntheticPathDenied("synthetic path probe denial")
            return _AwaitableMapping(decision=requested_decision)

        def __getattr__(self, _name: str) -> Callable[..., _AwaitableNoop]:
            return lambda *args, **kwargs: _AwaitableNoop()

    class _SyntheticTool:
        def __getattr__(self, _name: str) -> Callable[..., Mapping[str, object]]:
            def invoke(*args: object, **kwargs: object) -> Mapping[str, object]:
                argument_hash = "sha256:" + canonical.sha256_hex(
                    canonical.canonical_bytes(
                        {"args": list(args), "kwargs": dict(kwargs)}
                    )
                )
                append_event(
                    "invocation",
                    f"path-invocation-{path_id}",
                    argument_hash=argument_hash,
                )
                return _AwaitableMapping(synthetic=True)

            return invoke

    class _SyntheticNoop:
        def __getattr__(self, _name: str) -> Callable[..., _AwaitableNoop]:
            return lambda *args, **kwargs: _AwaitableNoop()

    dispatch_globals = getattr(dispatch, "__globals__", None)
    if not isinstance(dispatch_globals, dict):
        raise TypeError("execution_dispatch must be a module-level function")
    routing_table = dispatch_globals.get("EXECUTION_ROUTES")
    if not isinstance(routing_table, dict):
        raise TypeError(
            "execution_dispatch must expose same-module EXECUTION_ROUTES mapping"
        )
    route_keys = set(routing_table)
    if route_keys != declared_routes:
        raise ValueError(
            "EXECUTION_ROUTES keys must exactly match declared execution_paths"
        )
    target = routing_table.get((action_id, mode))
    if not callable(target):
        raise TypeError("EXECUTION_ROUTES target must be callable")
    if getattr(target, "__module__", None) != module_name:
        raise TypeError("EXECUTION_ROUTES targets must belong to execution_dispatch module")
    expected_resolved_path = (
        f"{target.__module__}:{getattr(target, '__qualname__', target.__name__)}"
    )
    resolved_path = ""

    def instrumented_target(*args: object, **kwargs: object) -> object:
        nonlocal resolved_path
        resolved_path = expected_resolved_path
        append_event(
            "resolved_path",
            f"path-resolved-{path_id}",
            resolved_path=resolved_path,
        )
        return target(*args, **kwargs)

    instrumented_target.__module__ = target.__module__
    instrumented_target.__name__ = target.__name__
    instrumented_target.__qualname__ = target.__qualname__
    routing_table[(action_id, mode)] = instrumented_target

    target_globals = getattr(target, "__globals__", None)
    if not isinstance(target_globals, dict):
        raise TypeError("EXECUTION_ROUTES targets must be module-level functions")
    dispatch_globals["agent_hooks"] = _SyntheticHooks()
    dispatch_globals["tool_service"] = _SyntheticTool()
    dispatch_globals["provider"] = _SyntheticTool()
    dispatch_globals["audit_sink"] = _SyntheticNoop()
    dispatch_globals["output_mediator"] = _SyntheticNoop()
    target_globals["agent_hooks"] = dispatch_globals["agent_hooks"]
    target_globals["tool_service"] = dispatch_globals["tool_service"]
    target_globals["provider"] = dispatch_globals["provider"]
    target_globals["audit_sink"] = dispatch_globals["audit_sink"]
    target_globals["output_mediator"] = dispatch_globals["output_mediator"]

    sys.stdout.buffer.write(_CHILD_READY_MARKER)
    sys.stdout.buffer.flush()
    append_event(
        "routing_target",
        f"path-routing-target-{path_id}",
        expected_resolved_path=expected_resolved_path,
    )
    case = {
        "probe_id": f"{_PATH_PROBE_ID_PREFIX}{requested_decision}",
        "path_id": path_id,
        "arguments": {
            "amount": 7,
            "count": 7,
            "number": 7,
            "customer_id": "threadlight-synthetic-customer",
            "order_id": "threadlight-synthetic-order",
            "payment_id": "threadlight-synthetic-payment",
        },
    }
    try:
        result = dispatch(action_id, mode, case, target_ledger_path)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        if not isinstance(result, Mapping):
            raise TypeError("execution_dispatch result must be a mapping")
        if result.get("action_id") != action_id or result.get("mode") != mode:
            raise ValueError("execution_dispatch result action_id/mode mismatch")
        routed_result = result.get("result")
        if inspect.isawaitable(routed_result):
            asyncio.run(routed_result)
    except _SyntheticPathDenied:
        pass
    report = {
        "action_id": action_id,
        "mode": mode,
        "resolved_path": resolved_path,
        "expected_resolved_path": expected_resolved_path,
    }
    sys.stdout.buffer.write(canonical.canonical_bytes(report))
    sys.stdout.flush()


# ----------------------------------------------------------------------
# Task 6: approval anti-replay, output mediation, and payload-free audit
# ----------------------------------------------------------------------

# The Task 6 isolated child's own argv sentinel, mirroring Task 5's
# ``--child`` — a distinct value so ``python3 probes.py --child`` and
# ``python3 probes.py --task6-child`` can never be confused with one
# another.
_TASK6_CHILD_ARG = "--task6-child"

# A generous, harness-level bound on the Task 6 dispatch callable's own
# execution, applied only *after* the ready marker is observed (see
# ``_dispatch_task6_child``). Task 6 probe contracts declare no
# ``timeout_ms`` of their own — every fixture call is a single,
# synchronous, synthetic operation expected to complete near-instantly
# — so this fixed bound exists solely to keep a hung or wedged target
# from ever blocking a probe run forever.
_TASK6_DISPATCH_TIMEOUT_S: float = 2.0


def _dispatch_task6_child(
    root_path: Path,
    dispatch_ref: str,
    audit_sink_ref: str,
    args: Tuple[str, ...],
) -> Mapping[str, object]:
    """Invoke a Task 6 fixture's dispatch callable in an isolated child.

    Mirrors ``_dispatch_child`` above: a sanitized, allow-listed
    environment (never the parent's own ``os.environ``), a fixed-length
    ready marker written only after the dispatch callable is resolved
    and *before* it is ever invoked, and a hard harness-level timeout
    applied only once that marker is observed — so a target whose
    ``redeem``/``emit_output`` seam hangs, crashes, or exits the
    interpreter outright can never hang or kill this assessor process
    itself.

    Whatever the target durably wrote to its own declared, file-based
    ledger before any such fault is the *only* evidence this function's
    caller ever trusts for that ledger's contents — never this
    function's own return value. This also drains and relays the
    resolved ``audit_sink`` list's contents back to the parent as
    plain, unjudged records (under ``"audit_records"``, or ``None`` when
    the child never got far enough to report them): the untrusted
    child never decides payload-freeness itself, it only hands back
    what it collected, exactly like the ledger file itself.
    """
    stdin_payload = {
        "dispatch": dispatch_ref,
        "audit_sink": audit_sink_ref,
        "args": list(args),
    }
    stdin_bytes = canonical.canonical_bytes(stdin_payload)

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(root_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    try:
        process = subprocess.Popen(  # noqa: S603 - fixed, trusted argv; no shell
            [sys.executable, str(THIS_FILE), _TASK6_CHILD_ARG],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(root_path),
        )
    except OSError as error:
        raise ProbeToolingError(
            f"cannot start the isolated Task 6 probe subprocess: {error}"
        ) from error

    try:
        assert process.stdin is not None  # narrows Optional for mypy/readers
        process.stdin.write(stdin_bytes)
        process.stdin.close()
    except (OSError, ValueError):
        # A child that crashed before ever reading stdin can close its
        # end of the pipe first; the ready-handshake wait below still
        # correctly resolves this to an abnormal/unobservable outcome.
        pass

    child_error: Optional[str] = None
    audit_records: Optional[list] = None

    ready = _wait_for_child_ready(process, _CHILD_READY_TIMEOUT_S)
    if not ready:
        process.kill()
        _reap_killed_child(process)
        child_error = "startup_failed"
    else:
        try:
            stdout_bytes, _stderr_bytes = process.communicate(
                timeout=_TASK6_DISPATCH_TIMEOUT_S
            )
        except subprocess.TimeoutExpired:
            process.kill()
            _reap_killed_child(process)
            child_error = "timeout"
        else:
            if process.returncode == 0:
                try:
                    parsed = json.loads(stdout_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    child_error = "malformed_output"
                else:
                    if isinstance(parsed, list):
                        audit_records = parsed
                    else:
                        child_error = "malformed_output"
            else:
                child_error = "nonzero_exit"

    return {"audit_records": audit_records, "child_error": child_error}


def _run_task6_as_child() -> None:
    """Isolated-subprocess entry point for Task 6's dispatch callables.

    Reads the canonical JSON payload from stdin, imports and resolves
    the fixture's ``dispatch`` callable, signals readiness, invokes it
    with the given positional *args*, then drains its declared
    ``audit_sink`` list and writes it — verbatim, unvalidated — as a
    canonical JSON array to stdout. Payload-freeness judgment is never
    made here: this untrusted child only relays what it observed, and
    the trusted parent process is the only place that ever calls
    ``canonical.validate_payload_free_audit`` on it.
    """
    raw = sys.stdin.buffer.read()
    payload = json.loads(raw.decode("utf-8"))
    dispatch_ref = str(payload["dispatch"])
    audit_sink_ref = str(payload["audit_sink"])
    args = payload["args"]

    dispatch_module_name, dispatch_attr = dispatch_ref.split(":", 1)
    dispatch_module = importlib.import_module(dispatch_module_name)
    dispatch = getattr(dispatch_module, dispatch_attr)

    # Import/resolution is finished: signal readiness *before* invoking
    # the dispatch callable so the parent's harness-level timeout clock
    # starts from here, not from process launch.
    sys.stdout.buffer.write(_CHILD_READY_MARKER)
    sys.stdout.buffer.flush()

    dispatch(*args)

    audit_module_name, audit_attr = audit_sink_ref.split(":", 1)
    audit_module = importlib.import_module(audit_module_name)
    audit_events = list(getattr(audit_module, audit_attr))
    sys.stdout.buffer.write(canonical.canonical_bytes(audit_events))
    sys.stdout.flush()


@dataclass(frozen=True)
class ApprovalBinding:
    """A single, atomically single-use human-approval binding (Task 6).

    Exactly the 12 fields a real approval must bind to before a
    consequential action ever reaches its tool: who requested it, who
    approved it and in what role, which tenant/target/policy version it
    was approved under, the exact (already-transformed) action id and
    arguments it authorizes, the approval's own validity window
    (``issued_at``/``expires_at``), and a one-time ``nonce``.
    ``approval_digest`` canonically hashes all 12 fields together, so
    changing *any single one* of them — including reusing an
    already-consumed ``nonce`` with a different value for every other
    field — always yields a different digest, and is therefore always
    rejected as a binding mismatch rather than silently accepted. Frozen
    so an approval can never be mutated in place after it is issued;
    every "mutated" scenario a probe exercises always constructs a new
    binding via ``dataclasses.replace`` instead.
    """

    target_scope: str
    requesting_subject: str
    approving_subject: str
    approving_role: str
    tenant: str
    policy_id: str
    policy_hash: str
    action_id: str
    arguments: Mapping[str, object]
    issued_at: str
    expires_at: str
    nonce: str


def _normalize_action_id(action_id: str) -> str:
    """Fold an action id's incidental whitespace/case for digest binding.

    Tolerates only cosmetic differences (surrounding whitespace,
    case) — never a genuinely different action id, which still
    produces a different digest and is still rejected as a binding
    mismatch.
    """
    return action_id.strip().lower()


def approval_digest(binding: ApprovalBinding) -> str:
    """Return the ``sha256:`` digest canonically binding all 12 fields.

    Uses ``canonical.canonical_bytes`` (sorted-key, compact JSON) over a
    dict of exactly ``target_scope``, ``requesting_subject``,
    ``approving_subject``, ``approving_role``, ``tenant``, ``policy_id``,
    ``policy_hash``, a normalized ``action_id``, ``arguments`` (cast to a
    plain ``dict`` so key order never affects the digest), ``issued_at``,
    ``expires_at``, and ``nonce`` — so an approval is bound to the exact
    already-transformed arguments and every other field it was granted
    for, never merely to its nonce.
    """
    canonical_fields = {
        "target_scope": binding.target_scope,
        "requesting_subject": binding.requesting_subject,
        "approving_subject": binding.approving_subject,
        "approving_role": binding.approving_role,
        "tenant": binding.tenant,
        "policy_id": binding.policy_id,
        "policy_hash": binding.policy_hash,
        "action_id": _normalize_action_id(binding.action_id),
        "arguments": dict(binding.arguments),
        "issued_at": binding.issued_at,
        "expires_at": binding.expires_at,
        "nonce": binding.nonce,
    }
    return "sha256:" + canonical.sha256_hex(canonical.canonical_bytes(canonical_fields))


_APPROVAL_PROBE_ID = "approval-anti-replay"
_APPROVAL_EXPECTED = "single_use_canonical_binding_enforced"
_APPROVAL_PASS_REASON = "approval-anti-replay-enforced"
APPROVAL_PROBE_ID = _APPROVAL_PROBE_ID

#: Every binding dimension design section 7.3 requires an approval to be
#: bound to, one mutation variant each: the canonical action (its own
#: identifier and its arguments, which together are the canonical action
#: hash), the target scope/environment, the tenant boundary, both acting
#: subjects and the approving role, the policy version and its hash, and
#: the approval's own expiry. ``run_approval_probe_sequence`` drives one
#: attempt per entry, each reusing the *same*, already-consumed nonce --
#: the exact "an approval for one canonical action hash cannot authorize
#: a changed X" scenario. Ordered, so a sequence's results are stable.
_APPROVAL_MUTATION_FIELDS: Tuple[str, ...] = (
    "action_id",
    "approving_role",
    "approving_subject",
    "arguments",
    "expires_at",
    "policy_hash",
    "policy_id",
    "requesting_subject",
    "target_scope",
    "tenant",
)
APPROVAL_MUTATION_FIELDS = _APPROVAL_MUTATION_FIELDS

#: Fixed, synthetic mutation values. Every one is derived so the mutated
#: binding's digest can never accidentally equal the original's: plain
#: string fields get an unmistakable suffix, and the two fields whose
#: shape matters (a ``sha256:`` policy hash and an RFC 3339 expiry) pick
#: a second constant whenever the declared value already equals the
#: first. The expiry mutations are both far-future, so an expiry
#: mutation is judged purely as a binding mismatch and never confused
#: with a separate stale-expiry rejection.
_APPROVAL_MUTATION_SUFFIX = "#threadlight-approval-probe-mutation"
_APPROVAL_MUTATED_ARGUMENT_KEY = "threadlight_approval_probe_mutation"
_APPROVAL_MUTATED_ARGUMENT_VALUES: Tuple[str, str] = ("mutated", "mutated-alternate")
_APPROVAL_MUTATED_POLICY_HASHES: Tuple[str, str] = (
    "sha256:" + "b" * 64,
    "sha256:" + "c" * 64,
)
_APPROVAL_MUTATED_EXPIRES_AT: Tuple[str, str] = (
    "9999-12-31T23:59:59Z",
    "9999-12-30T23:59:59Z",
)

#: Appended to the declared ``nonce_ledger`` path when a probe redeemed
#: against an assessment-private, isolated ledger rather than the
#: target's own declared one -- so the evidence entry names the declared
#: service-side store it stands for without ever claiming the target's
#: read-only, checked-in path was written.
_APPROVAL_ISOLATED_LEDGER_SOURCE_SUFFIX = "#assessment-isolated"

_OUTPUT_PROBE_ID = "output-mediation"
_OUTPUT_EXPECTED = "output_buffered_or_bound_chunk_mediated"
_OUTPUT_PASS_REASON = "output-mediation-enforced"
OUTPUT_PROBE_ID = _OUTPUT_PROBE_ID
_RECOGNIZED_OUTPUT_VERDICTS: Tuple[str, ...] = ("deny", "allow", "stream")

_AUDIT_EXPECTED = "audit_record_payload_free"
_AUDIT_PASS_REASON = "payload-free-audit-enforced"
_AUDIT_PROBE_ID = "payload-free-audit"
_AUDIT_NOT_VERIFIED_REASON = "audit-probe-outcome-not-verified"
AUDIT_PROBE_ID = _AUDIT_PROBE_ID

# Fixed, deterministic, synthetic arguments for ``run_privacy_probe_set``'s
# own single driven dispatch call against an approval-family target —
# arbitrary but stable so every run drives exactly the same case; never
# a real approval, and never the target's own declared, checked-in
# ledger (see the private temporary ledger construction below). "now"
# is fixed well before "expires_at" so this fixed synthetic call is
# always a legitimate, non-expired first-time redemption regardless of
# how a conformant target's own accept/reject decision depends on
# expiry — this probe set only ever cares about the audit sink's
# payload-freeness, never the redemption's own accept/reject outcome.
_AUDIT_PROBE_NONCE = "audit-probe-nonce"
_AUDIT_PROBE_DIGEST = "sha256:" + "0" * 64
_AUDIT_PROBE_NOW = "2026-01-01T00:00:00Z"
_AUDIT_PROBE_EXPIRES_AT = "2026-01-01T00:05:00Z"

# Fixed, deterministic verdict for ``run_privacy_probe_set``'s own
# single driven dispatch call against an output-family target — "deny"
# is chosen because it is always a recognized verdict regardless of
# what a given output-mediation fixture happens to declare (a "stream"
# verdict's own validity depends on contract fields this probe set has
# no business inspecting; "deny" never does).
_AUDIT_PROBE_VERDICT = "deny"


def _validate_relative_ledger_path(root_path: Path, field_name: str, value: object) -> str:
    """Validate a probe contract's relative, root-confined ledger path.

    Shared symlink-escape protection for the Task 6 loaders below,
    mirroring ``load_probe_contract``'s own ``observation_ledger``
    check: the path must be a non-empty relative path whose parent
    directory resolves — following any symlink along the way — inside
    *root_path*, never outside it.
    """
    if not isinstance(value, str) or not value:
        raise ProbeContractError(
            f"probe contract {field_name!r} must be a non-empty relative "
            f"path; got {value!r}"
        )
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ProbeContractError(
            f"probe contract {field_name!r} must be a relative path inside "
            f"the target root; got {value!r}"
        )
    resolved_root = root_path.resolve()
    resolved_dir = (root_path / relative).parent.resolve()
    try:
        resolved_dir.relative_to(resolved_root)
    except ValueError as error:
        raise ProbeContractError(
            f"probe contract {field_name!r} directory resolves outside the "
            f"target root (symlink escape?); got {value!r}"
        ) from error
    return value


def _validate_dispatch_and_audit_sink_refs(raw: Mapping[str, object]) -> None:
    module_names = {}
    for key in ("dispatch", "audit_sink"):
        value = raw.get(key)
        if not isinstance(value, str) or ":" not in value:
            raise ProbeContractError(
                f"probe contract {key!r} must be an importable 'module:attr' "
                f"reference string; got {value!r}"
            )
        module_names[key] = value.split(":", 1)[0]
    if module_names["dispatch"] != module_names["audit_sink"]:
        raise ProbeContractError(
            "probe contract 'dispatch' and 'audit_sink' must share the "
            f"same module for a Task 6 probe; got "
            f"{module_names['dispatch']!r} and {module_names['audit_sink']!r}"
        )


def _load_raw_contract(root_path: Path) -> Mapping[str, object]:
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
    return raw


def load_raw_probe_contract(root: Path) -> Mapping[str, object]:
    return _load_raw_contract(Path(root))


def _optional_nonempty_contract_string(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _declared_contract_actions(raw: Mapping[str, object]) -> Tuple[str, ...]:
    actions = raw.get("actions")
    if not isinstance(actions, (list, tuple)):
        return ()
    declared = []
    for action in actions:
        text = _optional_nonempty_contract_string(action)
        if text is not None:
            declared.append(text)
    return tuple(declared)


def _resolve_task6_action_id(raw: Mapping[str, object]) -> Tuple[Optional[str], Tuple[str, ...]]:
    explicit_action_id = _optional_nonempty_contract_string(raw.get("action_id"))
    if explicit_action_id is not None:
        return explicit_action_id, ()
    declared_binding = raw.get("approval_binding")
    if isinstance(declared_binding, Mapping):
        binding_action_id = _optional_nonempty_contract_string(declared_binding.get("action_id"))
        if binding_action_id is not None:
            return binding_action_id, ()
    declared_actions = _declared_contract_actions(raw)
    if len(declared_actions) == 1:
        return declared_actions[0], ()
    if len(declared_actions) > 1:
        return None, declared_actions
    return None, ()


def _probe_action_unattributed_result(
    probe_id: str, *, expected: str
) -> ProbeResult:
    return ProbeResult(
        probe_id=probe_id,
        action_id=None,
        path_id=None,
        status="not-verified",
        reason_code="probe-action-unattributed",
        expected=expected,
        observed="probe_action_unattributed",
        evidence_refs=(),
    )


def load_approval_contract(
    root: Path, *, raw_contract: Optional[Mapping[str, object]] = None
) -> Mapping[str, object]:
    """Load and validate an approval-anti-replay fixture's probe contract.

    Returns a read-only mapping with exactly ``dispatch``, ``audit_sink``,
    and ``nonce_ledger`` — a root-relative path to the fixture's
    persistent, service-side atomic nonce ledger. Raises
    :class:`ProbeContractError` for anything missing, malformed, or a
    ``nonce_ledger`` that would resolve outside *root* (symlink escape).
    """
    root_path = Path(root)
    raw = raw_contract if raw_contract is not None else _load_raw_contract(root_path)
    _validate_dispatch_and_audit_sink_refs(raw)
    nonce_ledger = raw.get("nonce_ledger")
    _validate_relative_ledger_path(root_path, "nonce_ledger", nonce_ledger)
    return MappingProxyType(
        {
            "dispatch": raw["dispatch"],
            "audit_sink": raw["audit_sink"],
            "nonce_ledger": nonce_ledger,
        }
    )


def load_output_contract(
    root: Path, *, raw_contract: Optional[Mapping[str, object]] = None
) -> Mapping[str, object]:
    """Load and validate an output-mediation fixture's probe contract.

    Returns a read-only mapping with ``dispatch``, ``audit_sink``,
    ``observation_ledger``, an optional ``action_id``, and the optional
    ``exposure_bound_bytes`` (a declared positive-integer byte bound)
    and ``chunk_mediation`` (a declared boolean) fields a ``"stream"``
    verdict requires. Both of the latter default to ``None`` when the
    contract omits them, which is exactly what makes an incremental
    ``"stream"`` verdict without an explicit declared bound reject as
    ``OUT-001`` rather than pass by omission.
    """
    root_path = Path(root)
    raw = raw_contract if raw_contract is not None else _load_raw_contract(root_path)
    _validate_dispatch_and_audit_sink_refs(raw)
    observation_ledger = raw.get("observation_ledger")
    _validate_relative_ledger_path(root_path, "observation_ledger", observation_ledger)

    exposure_bound_bytes = raw.get("exposure_bound_bytes")
    if exposure_bound_bytes is not None and (
        not isinstance(exposure_bound_bytes, int)
        or isinstance(exposure_bound_bytes, bool)
        or exposure_bound_bytes <= 0
    ):
        raise ProbeContractError(
            "probe contract 'exposure_bound_bytes', when present, must be "
            f"a positive integer; got {exposure_bound_bytes!r}"
        )
    chunk_mediation = raw.get("chunk_mediation")
    if chunk_mediation is not None and not isinstance(chunk_mediation, bool):
        raise ProbeContractError(
            "probe contract 'chunk_mediation', when present, must be a "
            f"boolean; got {chunk_mediation!r}"
        )
    action_id = raw.get("action_id")
    if action_id is not None and not isinstance(action_id, str):
        raise ProbeContractError(
            f"probe contract 'action_id', when present, must be a string; "
            f"got {action_id!r}"
        )
    return MappingProxyType(
        {
            "dispatch": raw["dispatch"],
            "audit_sink": raw["audit_sink"],
            "observation_ledger": observation_ledger,
            "exposure_bound_bytes": exposure_bound_bytes,
            "chunk_mediation": chunk_mediation,
            "action_id": action_id,
        }
    )


def _read_nonce_records(ledger_path: Path) -> List[Mapping[str, object]]:
    """Read the approval anti-replay nonce ledger's JSONL records.

    A missing ledger file reads as no records at all — a nonce's very
    first redemption attempt has nothing to append to yet. Reuses
    ``_read_ledger_events``'s tolerant, best-effort line parsing.
    """
    return _read_ledger_events(ledger_path)


def _first_event_index(events, event_type: str) -> Optional[int]:
    """Return the index of the first ledger event of *event_type*, or ``None``."""
    for index, event in enumerate(events):
        if event.get("event") == event_type:
            return index
    return None


def _validated_isolated_ledger(
    root_path: Path, contract: Mapping[str, object], ledger_path: object
) -> Path:
    """Validate an assessment-private nonce ledger override.

    Only ever accepted from this module's own
    :func:`run_approval_probe_sequence`, and validated strictly rather
    than trusted: it must be an absolute path to an already-created
    regular file (the caller creates it exclusively), it must not itself
    be a symlink, and it must never resolve onto the target's own
    declared ``nonce_ledger``. That last rule is what keeps the
    target-owned contract path strictly read-only -- an override can
    isolate probe state away from the target, never redirect it back
    onto the repository the assessment is only allowed to read.
    """
    if not isinstance(ledger_path, (str, Path)) or not str(ledger_path):
        raise ProbeContractError(
            f"approval probe ledger override must be a path; got {ledger_path!r}"
        )
    candidate = Path(ledger_path)
    if not candidate.is_absolute():
        raise ProbeContractError(
            "approval probe ledger override must be an absolute path to an "
            f"assessment-private ledger; got {str(ledger_path)!r}"
        )
    if candidate.is_symlink():
        raise ProbeContractError(
            "approval probe ledger override must not be a symlink "
            "(symlink escape?)"
        )
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise ProbeContractError(
            "approval probe ledger override must already exist as a regular "
            "file created exclusively by the caller"
        )
    declared = (root_path / str(contract["nonce_ledger"])).resolve()
    if resolved == declared:
        raise ProbeContractError(
            "approval probe ledger override must never resolve onto the "
            "target's own declared nonce ledger, which stays read-only"
        )
    return resolved


def _create_private_task6_ledger(
    root_path: Path, *, directory_prefix: str, file_prefix: str
) -> Tuple[Path, Path]:
    governance_dir = root_path / "governance"
    resolved_governance_dir = governance_dir.resolve()
    try:
        resolved_governance_dir.relative_to(root_path)
    except ValueError as error:
        raise ProbeContractError(
            "probe contract's governance directory resolves outside the "
            "target root (symlink escape?)"
        ) from error

    private_dir = governance_dir / f".{directory_prefix}-{uuid.uuid4().hex}"
    try:
        private_dir.mkdir(parents=False, exist_ok=False)
        descriptor, raw_ledger_path = tempfile.mkstemp(
            dir=str(private_dir), prefix=file_prefix, suffix=".jsonl"
        )
        os.close(descriptor)
    except OSError as error:
        _remove_created_dirs([private_dir])
        raise ProbeToolingError(
            f"cannot create the assessment-private {directory_prefix.replace('-', ' ')} "
            f"ledger: {error}"
        ) from error
    return private_dir, Path(raw_ledger_path)


def _mutated_binding(binding: ApprovalBinding, field: str) -> ApprovalBinding:
    """One deterministic single-field mutation of *binding*.

    Never a random or partially-random value, and never equal to the
    field it replaces: the resulting canonical digest is always
    different, so a target that accepts the mutated attempt is always
    proven to have accepted a binding it was never granted.
    """
    if field == "arguments":
        mutated = dict(binding.arguments)
        first, alternate = _APPROVAL_MUTATED_ARGUMENT_VALUES
        mutated[_APPROVAL_MUTATED_ARGUMENT_KEY] = (
            alternate if mutated.get(_APPROVAL_MUTATED_ARGUMENT_KEY) == first else first
        )
        return replace(binding, arguments=mutated)
    current = getattr(binding, field)
    if field == "policy_hash":
        first, alternate = _APPROVAL_MUTATED_POLICY_HASHES
        return replace(binding, policy_hash=alternate if current == first else first)
    if field == "expires_at":
        first, alternate = _APPROVAL_MUTATED_EXPIRES_AT
        return replace(binding, expires_at=alternate if current == first else first)
    return replace(binding, **{field: f"{current}{_APPROVAL_MUTATION_SUFFIX}"})


def run_approval_probe(
    root: Path,
    binding: ApprovalBinding,
    now: str,
    *,
    ledger_path: Optional[Path] = None,
) -> ProbeResult:
    """Prove one approval binding's anti-replay control at time *now*.

    Every scenario a passing probe proves here demonstrates the control
    working correctly — a first-time acceptance, a byte-identical
    replay rejected, a mutated-field reuse rejected, or an expired
    binding rejected before ever reaching the protected tool — exactly
    like Task 5's enforcement probes, where "pass" means "proven safe",
    not merely "approved". Only a genuine violation — the ledger
    failing to durably record exactly one new decision for this
    attempt, the ledger's own newly appended decision directly
    reporting a fail-open acceptance of a replay or a mutated binding,
    an expired binding's decision accepted anyway, or *any* rejected
    decision that the ledger shows was invoked regardless — is
    ``must-fix`` with reason ``APR-001``.

    Expiry is never self-graded here in Python before ever asking the
    target anything: ``now`` and the binding's own ``expires_at`` are
    always forwarded to the fixture's own service-side ``redeem`` call,
    which makes its own accept/reject decision (durably recording it)
    including whatever it decides about expiry — this probe only
    independently reconciles that recorded decision against the same
    ``now >= expires_at`` comparison (a window's own expiry instant is
    exclusive, never valid), so an expired binding that got fail-open
    *accepted* by a broken target is itself caught as ``APR-001``,
    never merely trusted.

    ``dispatch`` (a synthetic, service-side atomic ``redeem(nonce,
    digest, expires_at, now, ledger_path)``) always runs isolated in a
    sanitized, bounded child subprocess (see ``_dispatch_task6_child``),
    so a hung or crashed target can never hang or kill this assessor —
    but its own in-process return value (and even whether it ever
    completed at all) is never trusted either way. The persistent,
    on-disk nonce ledger is read both before and after that call, and
    this only ever *observes* the outcome the ledger's own newly
    appended records report for themselves via their explicit
    ``event``/``accepted`` fields — it never infers accept, replay, or
    mismatch merely from how the record count changed, nor from
    comparing digests itself, which a fail-open store that silently
    granted a replay or a mutated binding could otherwise mask.

    Acceptance and *tool invocation* are proven as two distinct,
    ordered ledger facts, never conflated: exactly one new
    ``"decision"`` record for this nonce, followed — strictly, and
    only when that decision is ``accepted: true`` — by exactly one new
    ``"invocation"`` record for the same nonce. A rejected decision
    (replay, mutated binding, or expiry) that the ledger shows was
    invoked anyway is a fail-open control and is always ``APR-001``,
    regardless of why it was rejected; an accepted decision that the
    ledger fails to show was ever actually invoked (missing,
    duplicated, or out of order relative to its own decision) is
    equally untrustworthy and equally ``APR-001`` — this probe never
    merely takes an ``accepted: true`` decision's word for it that the
    tool was reached. A ledger that does not grow by exactly one new
    decision record for this single attempt — no growth at all (a
    hung/crashed target, or a hang bounded by the dispatch child's own
    timeout), or more than one decision appended — can never be
    trusted as a proven pass either.

    Once invocation evidence is proven consistent, a first-ever,
    non-expired attempt for a nonce must itself durably record
    ``accepted: true`` to pass; once a nonce already has one accepted
    record, any later, non-expired attempt reporting ``accepted: true``
    again is a proven fail-open acceptance (``APR-001``) regardless of
    whether it is a byte-identical replay or a mutated binding, and
    only a genuinely rejected (``accepted: false``) later attempt
    passes — distinguished as a replay (digest matches the original
    accepted record) or a binding mismatch (it does not, covering every
    mutated field: subject, role, target, tenant, policy, action, or
    arguments, including a reused nonce whose original approval was
    bound to different, pre-transform arguments).

    Payload-freeness of whatever the fixture's ``redeem`` call drained
    into its declared ``audit_sink`` is never judged here at all —
    that is ``run_privacy_probe_set``'s job alone, precisely so an
    audit-trail violation (``AUD-001``) can never mask, or be masked
    by, this probe's own independent anti-replay finding.

    ``ledger_path`` is an optional, strictly validated override naming
    an assessment-private nonce ledger the caller already created
    exclusively (see :func:`run_approval_probe_sequence`, this module's
    only user of it). It can never redirect onto the target's own
    declared ``nonce_ledger``, which stays read-only. Without it, the
    declared contract path is used exactly as before -- which is what
    lets a caller drive its own explicit, ordered sequence against a
    ledger it owns rather than one the target ships.
    """
    root_path = Path(root).resolve()
    contract = load_approval_contract(root_path)
    digest = approval_digest(binding)
    if ledger_path is None:
        nonce_ledger_path = root_path / contract["nonce_ledger"]
        evidence_source = str(contract["nonce_ledger"])
    else:
        nonce_ledger_path = _validated_isolated_ledger(root_path, contract, ledger_path)
        evidence_source = (
            f"{contract['nonce_ledger']}{_APPROVAL_ISOLATED_LEDGER_SOURCE_SUFFIX}"
        )

    before_records = _read_nonce_records(nonce_ledger_path)
    prior_accepted_digest: Optional[str] = None
    for record in before_records:
        if (
            record.get("event") == "decision"
            and record.get("nonce") == binding.nonce
            and record.get("accepted") is True
        ):
            prior_accepted_digest = record.get("digest")
            break
    prior_accepted_for_nonce = prior_accepted_digest is not None

    _dispatch_task6_child(
        root_path,
        str(contract["dispatch"]),
        str(contract["audit_sink"]),
        (binding.nonce, digest, binding.expires_at, now, str(nonce_ledger_path)),
    )

    after_records = _read_nonce_records(nonce_ledger_path)
    new_records = after_records[len(before_records):]
    new_decisions = [record for record in new_records if record.get("event") == "decision"]
    new_invocations = [
        record for record in new_records if record.get("event") == "invocation"
    ]

    expired = now >= binding.expires_at
    status: str
    observed: str

    if len(new_decisions) != 1:
        observed = (
            "nonce_reuse_not_atomic"
            if len(new_decisions) > 1
            else "nonce_redemption_not_recorded"
        )
        status = "must-fix"
    else:
        decision = new_decisions[0]
        if decision.get("nonce") != binding.nonce:
            observed = "nonce_redemption_not_recorded"
            status = "must-fix"
        else:
            accepted_now = decision.get("accepted") is True
            decision_index = new_records.index(decision)
            matching_invocations = [
                record
                for record in new_invocations
                if record.get("nonce") == binding.nonce
            ]
            if not accepted_now and new_invocations:
                # Fail-open: the target invoked the protected tool for
                # this dispatch even though its own decision was a
                # rejection — never trusted regardless of *why* it was
                # rejected (replay, mismatch, or expiry).
                observed = "fail_open_invocation_on_rejection"
                status = "must-fix"
            elif accepted_now and (
                len(matching_invocations) != 1
                or new_records.index(matching_invocations[0]) <= decision_index
            ):
                # Accepted, but the ledger fails to prove the tool was
                # actually, and only once, reached strictly after this
                # decision — an accepted decision is never trusted on
                # its word alone.
                observed = "invocation_not_recorded_after_acceptance"
                status = "must-fix"
            elif expired:
                if accepted_now:
                    observed = "expired_binding_fail_open_accepted"
                    status = "must-fix"
                else:
                    observed = "expired_rejected"
                    status = "pass"
            elif not prior_accepted_for_nonce:
                if accepted_now:
                    observed = "approval_accepted"
                    status = "pass"
                else:
                    observed = "nonce_redemption_not_recorded"
                    status = "must-fix"
            elif accepted_now:
                observed = "fail_open_replay_or_mutation_accepted"
                status = "must-fix"
            else:
                observed = (
                    "replay_rejected"
                    if prior_accepted_digest == digest
                    else "binding_mismatch_rejected"
                )
                status = "pass"

    reason_code = _APPROVAL_PASS_REASON if status == "pass" else "APR-001"
    return ProbeResult(
        probe_id=_APPROVAL_PROBE_ID,
        action_id=binding.action_id,
        path_id=None,
        status=status,
        reason_code=reason_code,
        expected=_APPROVAL_EXPECTED,
        observed=observed,
        evidence_refs=(digest,),
        # The cited digest is the canonical hash of the exact 12-field
        # binding this attempt redeemed -- the same value the target's
        # own ledger records for it -- bound here to the nonce ledger it
        # was redeemed against, marked when that ledger was this
        # assessment's own private, isolated one rather than the
        # target's declared path. Payload-free: no approver, argument,
        # or ledger record content ever leaves this call.
        evidence_items=(
            _digest_evidence(digest, "approval-binding-digest", evidence_source),
        ),
    )


def run_approval_probe_sequence(
    root: Path, binding: ApprovalBinding, now: str
) -> Tuple[ProbeResult, ...]:
    """Actually exercise anti-replay for *binding*, within one assessment.

    A single redemption attempt only ever proves that a *first* use was
    accepted; it can never prove the approval was single-use, because
    nothing was ever replayed against it. This drives the whole ordered
    sequence a real anti-replay control has to survive, in one bounded
    run: the first, legitimate redemption; a byte-identical replay of
    it; and one mutated-binding attempt per dimension design section 7.3
    requires (:data:`_APPROVAL_MUTATION_FIELDS` -- the canonical action
    and its arguments, target scope, tenant, both subjects and the
    approving role, policy id and hash, and expiry), every one of them
    reusing the same, already-consumed nonce.

    All of it runs against a single, freshly created, exclusive,
    *private* nonce ledger under the target's own ``governance``
    directory -- never the target's declared, checked-in
    ``nonce_ledger`` path, which this never reads or writes. The
    redemption state a real service-side store must keep is therefore
    genuinely durable across the attempts of one sequence (that is what
    makes the replay a replay), and just as genuinely gone once the
    sequence ends: the ledger and the private directory holding it are
    removed in a ``finally``, so a crash, a hung target bounded by the
    dispatch child's own timeout, or an outright tooling failure all
    still leave the target byte-identical. Consecutive sequences at the
    same commit therefore always start from the same empty nonce space
    and always produce the same results, with no ledger growth and no
    residue.

    Rejects a ``governance`` directory that resolves outside *root* (a
    symlink escape) before creating anything, and raises
    :class:`ProbeToolingError` when that private ledger cannot be
    created at all -- an unprovable outcome, never a silent pass.

    Each result is reported against the action actually under test (the
    declared binding's own ``action_id``), so a mutated-action variant
    never publishes a synthetic action identifier that exists in no
    inventory; the mutation itself stays fully evidenced by the distinct
    canonical binding digest each attempt cites.
    """
    root_path = Path(root).resolve()
    # Validated up front for its own sake: an absent, malformed, or
    # unsafe approval contract is refused before anything is created,
    # exactly as each individual attempt below would refuse it.
    load_approval_contract(root_path)
    private_dir, ledger_path = _create_private_task6_ledger(
        root_path,
        directory_prefix="approval-probe",
        file_prefix="nonce-ledger-",
    )
    attempts = (binding, binding) + tuple(
        _mutated_binding(binding, field) for field in _APPROVAL_MUTATION_FIELDS
    )
    results: List[ProbeResult] = []
    try:
        for attempt in attempts:
            try:
                result = run_approval_probe(root_path, attempt, now, ledger_path=ledger_path)
            except ProbeToolingError as error:
                raise PartialProbeToolingError(
                    str(error), partial_results=tuple(results)
                ) from error
            if attempt.action_id != binding.action_id:
                result = replace(result, action_id=binding.action_id)
            results.append(result)
    finally:
        ledger_path.unlink(missing_ok=True)
        _remove_created_dirs([private_dir])
    return tuple(results)


def run_output_probe(root: Path, verdict: str) -> ProbeResult:
    """Prove protected output is mediated according to *verdict*.

    Recognizes exactly ``"deny"``, ``"allow"``, and ``"stream"`` —
    anything else raises :class:`ProbeContractError`. Every verdict is
    first checked for *ordering*, never assumed: this probe locates the
    ledger's own ``"verdict_received"`` record and proves no
    ``"egress"``/``"chunk"`` release event precedes it — a release
    recorded before the verdict is itself always a ``must-fix``
    regardless of which verdict eventually followed, so ``"allow"`` is
    never an unconditional pass. A ledger that never even records a
    ``"verdict_received"`` event at all — including a hung or crashed
    dispatch call that never got far enough to write anything, bounded
    by the isolated dispatch child's own timeout rather than ever
    hanging this assessor — is likewise never a silent pass; it is
    reported as ``OUT-001`` on its own lack of evidence.

    Once ordering is proven, ``"deny"`` only passes when the *total*
    bytes ever released — every ``"egress"`` event *and* every
    ``"chunk"`` event's bytes, both counted, so a denial cannot be
    laundered into a pass merely by leaking bytes framed as chunks
    instead of a single egress record — is zero. ``"allow"`` passes for
    the buffered posture releasing the complete output only after the
    verdict is known — but only when every recorded release event is a
    single, buffered ``"egress"``; an ``"allow"`` verdict whose ledger
    shows even one incidental ``"chunk"`` event is itself an
    undeclared incremental release, since ``"allow"`` never declares an
    exposure bound or chunk mediation the way ``"stream"`` must, and is
    rejected as ``OUT-001`` rather than laundered through unconditionally.
    ``"stream"`` (incremental release) only ever passes when the probe
    contract itself declares an explicit, nonzero ``exposure_bound_bytes``
    and ``chunk_mediation: true`` *and* the observation ledger
    independently proves every single release event of either kind —
    every ``"chunk"`` and every ``"egress"`` — is itself individually
    mediated (``mediated: true``, a positive integer byte count) *and*
    that their combined total across the whole stream never exceeds
    that declared bound; a contract that omits either declaration
    rejects the stream verdict as ``OUT-001`` before the dispatch seam
    is ever invoked at all (proven by fixtures whose dispatch callable
    raises if ever called in that case), and a single trailing raw,
    unmediated release event is always enough to fail an otherwise
    fully-mediated stream, since counting every release event (not
    just ``"chunk"``-typed ones) toward both the mediation check and
    the total-exposure budget is what actually proves no egress has
    happened outside the declared, mediated posture.

    ``dispatch`` always runs isolated in a sanitized, bounded child
    subprocess (see ``_dispatch_task6_child``); only the persistent,
    on-disk observation ledger it durably wrote before any hang, crash,
    or fault is ever trusted as evidence — never the subprocess's own
    completion status. Payload-freeness of whatever it drained into
    its declared ``audit_sink`` is never judged here at all — that is
    ``run_privacy_probe_set``'s job alone, so an audit-trail violation
    (``AUD-001``) can never mask, or be masked by, this probe's own
    independent mediation finding.

    Uses an exclusive, assessment-private temporary ledger under the
    target's own ``governance/`` directory, removing it again when done.
    The contract's declared ``observation_ledger`` remains provenance
    only; it is never opened, unlinked, truncated, or otherwise mutated
    by this probe, so even a hostile contract pointing that field at a
    tracked target file cannot let the assessment delete or rewrite it.
    """
    if verdict not in _RECOGNIZED_OUTPUT_VERDICTS:
        raise ProbeContractError(f"unknown output verdict: {verdict!r}")

    root_path = Path(root).resolve()
    raw_contract = _load_raw_contract(root_path)
    contract = load_output_contract(root_path, raw_contract=raw_contract)
    action_id, ambiguous_actions = _resolve_task6_action_id(raw_contract)
    if ambiguous_actions:
        return _probe_action_unattributed_result(
            _OUTPUT_PROBE_ID,
            expected=_OUTPUT_EXPECTED,
        )

    if verdict == "stream":
        exposure_bound_bytes = contract["exposure_bound_bytes"]
        chunk_mediation = contract["chunk_mediation"]
        if (
            not isinstance(exposure_bound_bytes, int)
            or exposure_bound_bytes <= 0
            or chunk_mediation is not True
        ):
            return ProbeResult(
                probe_id=_OUTPUT_PROBE_ID,
                action_id=action_id,
                path_id=None,
                status="must-fix",
                reason_code="OUT-001",
                expected=_OUTPUT_EXPECTED,
                observed="incremental_output_without_declared_bound",
                evidence_refs=(),
            )

    private_dir, ledger_path = _create_private_task6_ledger(
        root_path, directory_prefix="output-probe", file_prefix="ledger-"
    )
    try:
        _dispatch_task6_child(
            root_path,
            str(contract["dispatch"]),
            str(contract["audit_sink"]),
            (verdict, str(ledger_path)),
        )
        events = _read_ledger_events(ledger_path)
    finally:
        ledger_path.unlink(missing_ok=True)
        _remove_created_dirs([private_dir])

    verdict_index = _first_event_index(events, "verdict_received")
    release_indices = [
        index
        for index, event in enumerate(events)
        if event.get("event") in ("egress", "chunk")
    ]

    if verdict_index is None and not release_indices:
        observed = "no_verdict_evidence_recorded"
        status = "must-fix"
    elif any(
        verdict_index is None or index < verdict_index for index in release_indices
    ):
        observed = "output_released_before_verdict"
        status = "must-fix"
    elif verdict == "deny":
        release_events = [
            event for event in events if event.get("event") in ("egress", "chunk")
        ]
        total_released_bytes = 0
        every_release_bytes_valid = True
        for event in release_events:
            raw_bytes = event.get("bytes")
            is_valid_nonnegative_int = (
                isinstance(raw_bytes, int)
                and not isinstance(raw_bytes, bool)
                and raw_bytes >= 0
            )
            if not is_valid_nonnegative_int:
                every_release_bytes_valid = False
                break
            total_released_bytes += raw_bytes
        if not every_release_bytes_valid:
            # Missing, boolean, float, string, or negative "bytes"
            # evidence can never be trusted as proof of zero egress —
            # never defaulted to 0, never summed, never coerced through
            # a bare ``int()`` that could itself crash on a non-numeric
            # value. Unverifiable byte evidence is itself a must-fix,
            # never a silent pass.
            observed = "malformed_egress_byte_evidence"
            status = "must-fix"
        elif total_released_bytes == 0:
            observed = "zero_bytes_egressed"
            status = "pass"
        else:
            observed = "nonzero_bytes_egressed_on_deny"
            status = "must-fix"
    elif verdict == "allow":
        if any(event.get("event") == "chunk" for event in events):
            observed = "incremental_release_without_declared_stream_posture"
            status = "must-fix"
        else:
            observed = "buffered_release_after_verdict"
            status = "pass"
    else:  # verdict == "stream"; bound/mediation already validated above
        exposure_bound_bytes = contract["exposure_bound_bytes"]
        release_events = [
            event for event in events if event.get("event") in ("egress", "chunk")
        ]
        total_released_bytes = 0
        every_release_mediated_and_positive = bool(release_events)
        for event in release_events:
            raw_bytes = event.get("bytes")
            is_valid_positive_int = (
                isinstance(raw_bytes, int)
                and not isinstance(raw_bytes, bool)
                and raw_bytes > 0
            )
            if not is_valid_positive_int or event.get("mediated") is not True:
                every_release_mediated_and_positive = False
                continue
            total_released_bytes += raw_bytes
        if (
            every_release_mediated_and_positive
            and total_released_bytes <= exposure_bound_bytes
        ):
            observed = "chunk_mediated_within_bound"
            status = "pass"
        else:
            observed = "unmediated_or_oversized_chunk_release"
            status = "must-fix"

    reason_code = _OUTPUT_PASS_REASON if status == "pass" else "OUT-001"
    return ProbeResult(
        probe_id=_OUTPUT_PROBE_ID,
        action_id=action_id,
        path_id=None,
        status=status,
        reason_code=reason_code,
        expected=_OUTPUT_EXPECTED,
        observed=observed,
        evidence_refs=(),
    )



def run_privacy_probe_set(root: Path) -> Tuple[ProbeResult, ...]:
    """Prove *root*'s own real decision-audit trail never carries a raw payload.

    Detects whether *root* is an approval-anti-replay or an
    output-mediation Task 6 fixture — whichever probe contract loads
    successfully — and drives exactly one fixed, deterministic,
    synthetic dispatch call of its own, isolated in the same
    sanitized, bounded child subprocess every other Task 6 probe uses
    (see ``_dispatch_task6_child``), into a freshly created, exclusive,
    private temporary ledger file under *root*'s own ``governance``
    directory — never the target's real, declared, checked-in
    ``nonce_ledger``/``observation_ledger`` path — which is always
    removed again afterward, in every case, along with the private
    directory that held it. Rejects a ``governance`` directory that
    resolves outside *root* (a symlink escape) before ever creating
    anything.

    Every audit record the target's own ``audit_sink`` actually
    produced during that single driven call — the real target's own
    evidence, drained and relayed by the isolated child, never a fixed
    in-module sample — is then independently validated through
    ``canonical.validate_payload_free_audit``, so a conformant target
    passes and a target whose audit trail leaks a raw payload is
    reported as ``AUD-001``. When the child never got far enough to
    relay anything at all (a hang bounded by the dispatch child's own
    timeout, a crash, or malformed output), this is reported as
    ``not-verified`` rather than silently passing or fabricating a
    violation without evidence.

    This never mutates *root*: the probe's own driven call always
    lands in a private temporary ledger, cleaned up again regardless of
    outcome, and never touches the contract's own declared ledger path
    at all. Calling this repeatedly against the same *root* always
    drives the exact same fixed synthetic call into a brand new private
    temporary ledger and never depends on, or perturbs, anything left
    behind by a previous call — it is safe to run idempotently, even
    directly against a real, checked-in fixture root.

    This is also the *only* place ``AUD-001`` is ever produced: neither
    ``run_approval_probe`` nor ``run_output_probe`` judges audit-sink
    payload-freeness at all, so calling this alongside either of them
    always surfaces both findings independently — an audit-trail
    violation here can never mask, or be masked by, an ``APR-001``/
    ``OUT-001`` finding those probes report on their own.
    """
    root_path = Path(root).resolve()
    raw_contract = _load_raw_contract(root_path)

    approval_contract: Optional[Mapping[str, object]] = None
    output_contract: Optional[Mapping[str, object]] = None
    try:
        approval_contract = load_approval_contract(root_path, raw_contract=raw_contract)
    except ProbeContractError:
        try:
            output_contract = load_output_contract(root_path, raw_contract=raw_contract)
        except ProbeContractError as error:
            raise ProbeContractError(
                f"{root_path} is neither a recognized approval-anti-replay "
                "nor output-mediation Task 6 probe contract; "
                "run_privacy_probe_set has no real target audit evidence to "
                "assess"
            ) from error

    contract = approval_contract if approval_contract is not None else output_contract
    assert contract is not None  # one of the two branches above always set it
    action_id, ambiguous_actions = _resolve_task6_action_id(raw_contract)
    if ambiguous_actions:
        return (
            _probe_action_unattributed_result(
                _AUDIT_PROBE_ID,
                expected=_AUDIT_EXPECTED,
            ),
        )

    private_dir, ledger_path = _create_private_task6_ledger(
        root_path, directory_prefix="privacy-probe", file_prefix="ledger-"
    )

    try:
        if approval_contract is not None:
            args: Tuple[str, ...] = (
                _AUDIT_PROBE_NONCE,
                _AUDIT_PROBE_DIGEST,
                _AUDIT_PROBE_EXPIRES_AT,
                _AUDIT_PROBE_NOW,
                str(ledger_path),
            )
        else:
            args = (_AUDIT_PROBE_VERDICT, str(ledger_path))

        dispatch_result = _dispatch_task6_child(
            root_path, str(contract["dispatch"]), str(contract["audit_sink"]), args
        )
    finally:
        ledger_path.unlink(missing_ok=True)
        _remove_created_dirs([private_dir])

    audit_records = dispatch_result["audit_records"]
    if audit_records is None:
        return (
            ProbeResult(
                probe_id=_AUDIT_PROBE_ID,
                action_id=action_id,
                path_id=None,
                status="not-verified",
                reason_code=_AUDIT_NOT_VERIFIED_REASON,
                expected=_AUDIT_EXPECTED,
                observed="audit_evidence_unavailable",
                evidence_refs=(),
            ),
        )

    results = []
    for record in audit_records:
        if not isinstance(record, Mapping):
            results.append(
                ProbeResult(
                    probe_id=_AUDIT_PROBE_ID,
                    action_id=action_id,
                    path_id=None,
                    status="must-fix",
                    reason_code="AUD-001",
                    expected=_AUDIT_EXPECTED,
                    observed="payload_bearing_audit_record",
                    evidence_refs=(),
                )
            )
            continue
        audit_id = record.get("audit_id")
        audit_id_text = str(audit_id) if audit_id else None
        audit_evidence: Tuple[ProbeEvidence, ...] = (
            (
                _record_evidence(
                    audit_id_text,
                    "probe-audit-record",
                    str(contract["audit_sink"]),
                    record,
                ),
            )
            if audit_id_text
            else ()
        )
        try:
            canonical.validate_payload_free_audit(record)
        except canonical.PayloadExposureError:
            results.append(
                ProbeResult(
                    probe_id=_AUDIT_PROBE_ID,
                    action_id=action_id,
                    path_id=None,
                    status="must-fix",
                    reason_code="AUD-001",
                    expected=_AUDIT_EXPECTED,
                    observed="payload_bearing_audit_record",
                    evidence_refs=(audit_id_text,) if audit_id_text else (),
                    evidence_items=audit_evidence,
                )
            )
        else:
            results.append(
                ProbeResult(
                    probe_id=_AUDIT_PROBE_ID,
                    action_id=action_id,
                    path_id=None,
                    status="pass",
                    reason_code=_AUDIT_PASS_REASON,
                    expected=_AUDIT_EXPECTED,
                    observed="payload_free_audit_record",
                    evidence_refs=(audit_id_text,) if audit_id_text else (),
                    evidence_items=audit_evidence,
                )
            )

    if not results:
        results.append(
            ProbeResult(
                probe_id=_AUDIT_PROBE_ID,
                action_id=action_id,
                path_id=None,
                status="not-verified",
                reason_code=_AUDIT_NOT_VERIFIED_REASON,
                expected=_AUDIT_EXPECTED,
                observed="audit_evidence_unavailable",
                evidence_refs=(),
            )
        )
    return tuple(results)


# ---------------------------------------------------------------------------
# Task 8: staging-only post-deploy guards.
#
# ``post-deploy`` is the one phase this project ever lets touch anything
# resembling a live target, and only through the two functions below.
# ``validate_post_deploy_target`` is a pure input guard: it never runs a
# command or opens a connection, it only refuses to let assessment proceed
# against a target this project was not explicitly told is staging.
# ``run_staging_canary`` is the single, narrowly-bounded live HTTP check
# ``post-deploy`` may run in addition to the fully hermetic, non-mutating
# probes above -- every other post-deploy check remains exactly as
# hermetic as Tasks 5/6 already made it.
# ---------------------------------------------------------------------------

#: A read-only HTTP runner: given a request mapping (``method``, ``url``,
#: optionally ``headers``), returns a response object exposing
#: ``status_code`` (int), ``headers`` (a string-keyed mapping), and
#: ``body`` (bytes/str, hashed by :func:`run_staging_canary` but never
#: itself recorded or returned).
HttpReadRunner = Callable[[Mapping[str, object]], object]

_STAGING_CANARY_ALLOWED_METHODS = frozenset({"GET", "HEAD"})

#: The *only* request header names :func:`run_staging_canary` ever allows
#: on a canary request -- checked case-insensitively, exact match only.
#: This is a positive allowlist of known-harmless names, not merely a
#: deny-list of known-dangerous ones, so a header this project never
#: anticipated is rejected by default rather than passed through by
#: omission.
_STAGING_CANARY_ALLOWED_HEADER_NAMES = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "user-agent",
        "x-request-id",
        "x-correlation-id",
    }
)

#: The response header names (checked case-insensitively, in this order)
#: :func:`run_staging_canary` looks in for a deployment-identifying value
#: to record -- never any other response header, and never the body.
_DEPLOYMENT_ID_HEADER_NAMES = ("x-deployment-id", "deployment-id")

#: A fixed technical safety timeout (in seconds) this project asks the
#: injected HTTP runner to honor for the one live request
#: :func:`run_staging_canary` issues -- a bound against an unresponsive
#: or slow live target hanging this assessment indefinitely, never a
#: customer-tunable policy value. This project has no standing to make
#: the runner actually enforce it, but it is always supplied so that any
#: runner capable of honoring a request timeout can.
_STAGING_CANARY_REQUEST_TIMEOUT_SECONDS = 10.0

#: A fixed technical safety cap on the number of bytes of a canary
#: response body :func:`run_staging_canary` will read and hash -- bounds
#: the cost of hashing an adversarially large (or merely
#: unexpectedly large) response body, never a customer content-size
#: policy. A body at or beyond this many bytes is reported
#: ``not-verified`` rather than hashed.
_STAGING_CANARY_MAX_RESPONSE_BODY_BYTES = 1_048_576  # 1 MiB

#: A fixed technical safety chunk size (in characters) used to encode a
#: ``str`` canary response body to UTF-8 incrementally rather than all at
#: once -- bounds the worst-case memory a single oversized/adversarial
#: ``str`` body can force this project to allocate before the byte cap
#: is enforced, never a customer content-size policy. A plain Python
#: ``str`` slice always falls on a valid code-point boundary, so each
#: chunk can be encoded independently with no multi-byte-character
#: splitting risk.
_STAGING_CANARY_BODY_ENCODE_CHUNK_CHARS = 65_536

#: Fixed technical safety caps on a canary response's header mapping --
#: bound the cost of iterating/searching an adversarially large or
#: pathological header set for a deployment-id value, never a customer
#: policy. A response whose headers exceed any of these caps is
#: reported ``not-verified`` rather than searched.
_STAGING_CANARY_MAX_RESPONSE_HEADER_COUNT = 64
_STAGING_CANARY_MAX_RESPONSE_HEADER_NAME_LENGTH = 256
_STAGING_CANARY_MAX_RESPONSE_HEADER_VALUE_LENGTH = 4096

#: A fixed technical safety cap on the recorded deployment-id value's
#: length -- an oversized value is reported ``not-verified`` rather than
#: recorded, since truncating it would still record an
#: attacker/target-controlled value of unbounded original size as if it
#: were trustworthy, bounded evidence.
_STAGING_CANARY_MAX_DEPLOYMENT_ID_LENGTH = 256


def validate_post_deploy_target(phase: Phase, staging: bool, destructive: bool) -> None:
    """Refuse to let a ``post-deploy`` assessment target anything other
    than an explicit, non-destructive staging environment.

    Raises :class:`UnsafeTargetError` when *phase* is ``"post-deploy"``
    and *staging* is not ``True`` (post-deploy must never be pointed at
    production, or at a target the caller never explicitly opted into
    staging for), or when *destructive* is ``True`` (post-deploy must
    never run a destructive probe against a live target, staging or
    not). Every other phase is unaffected by *staging*/*destructive*: the
    staging-only guard is specific to the one phase that can touch a
    live target at all.
    """
    if phase != "post-deploy":
        return
    if not staging:
        raise UnsafeTargetError(
            "post-deploy assessment requires an explicit staging target; "
            "refusing to run against a non-staging (or unconfirmed) "
            "environment"
        )
    if destructive:
        raise UnsafeTargetError(
            "post-deploy assessment must never run a destructive probe "
            "against a live target, even in staging"
        )


def _disallowed_canary_header(headers: object) -> Optional[str]:
    """The first request header name *headers* declares that is not on
    this probe's positive allowlist of harmless canary headers -- or
    ``None`` when *headers* is ``None`` or every header it declares is
    allowed. *headers* being anything other than ``None`` or a mapping
    is itself a contract violation, never silently ignored.
    """
    if headers is None:
        return None
    if not isinstance(headers, Mapping):
        raise UnsafeTargetError("staging canary headers must be a mapping")
    for key in headers:
        if str(key).strip().lower() not in _STAGING_CANARY_ALLOWED_HEADER_NAMES:
            return str(key)
    return None


#: Sentinel distinguishing a response object that genuinely has no
#: ``url``/``headers``/``body`` attribute at all from one that has the
#: attribute set to some other falsy-but-present value (``None``, an
#: empty mapping, an empty ``b""``). ``getattr(response, name, None)``
#: cannot make that distinction, and collapsing "missing" into "present
#: but empty" is exactly how this probe used to silently skip the
#: response-URL same-origin check and fabricate a hash of an empty body
#: as if that were genuinely observed evidence.
_CANARY_RESPONSE_FIELD_MISSING = object()


def _is_valid_http_status(value: object) -> bool:
    """Whether *value* is a plausible HTTP status code: an ``int`` (never
    a bare ``bool``, which is technically an ``int`` subclass) in the
    100-599 range every real HTTP status line uses."""
    return isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599


def _is_valid_canary_headers(headers: object) -> bool:
    """Whether *headers* is a mapping of plain string names to plain
    string values, bounded by :data:`_STAGING_CANARY_MAX_RESPONSE_HEADER_COUNT`
    entries with each name/value within
    :data:`_STAGING_CANARY_MAX_RESPONSE_HEADER_NAME_LENGTH`/
    :data:`_STAGING_CANARY_MAX_RESPONSE_HEADER_VALUE_LENGTH` -- the only
    shape :func:`run_staging_canary` ever trusts enough to search for a
    deployment-id header. A header set exceeding any of these fixed
    technical safety bounds is rejected exactly like one with the wrong
    type, never partially processed."""
    if not isinstance(headers, Mapping):
        return False
    if len(headers) > _STAGING_CANARY_MAX_RESPONSE_HEADER_COUNT:
        return False
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return False
        if len(key) > _STAGING_CANARY_MAX_RESPONSE_HEADER_NAME_LENGTH:
            return False
        if len(value) > _STAGING_CANARY_MAX_RESPONSE_HEADER_VALUE_LENGTH:
            return False
    return True


def _parse_canary_https_url(url: object, *, what: str) -> str:
    """Validate *url* is a plain ``https://host[:port]/path`` URL with no
    embedded userinfo, query string, or fragment, returning its
    normalized ``https://host[:port]`` origin.

    Raises :class:`UnsafeTargetError` (naming only *what*, e.g. ``"staging
    canary url"`` or ``"trusted staging origin"`` -- a fixed, internal
    label this module itself controls, never anything derived from
    *url*) for anything else: a non-``https`` scheme, embedded
    credentials, a missing hostname, a query string, a fragment, or a
    syntactically malformed port -- any of which could smuggle a
    credential or silently redirect the canary somewhere other than the
    one origin actually intended. Every one of these messages is fixed
    text; none of them ever interpolates the raw *url*, the underlying
    ``ValueError`` text a malformed port/host raises, or any other part
    of the input, since any of those could themselves carry a credential
    or other sensitive fragment of the very value being rejected.
    """
    if not isinstance(url, str):
        raise UnsafeTargetError(f"{what} must be an https:// URL string")
    try:
        parsed = urlsplit(url)
        # ``SplitResult.port`` (and, for some malformed inputs, ``urlsplit``
        # itself) raises ``ValueError`` *lazily* -- at attribute-access
        # time, not at parse time -- for a syntactically invalid port
        # (out of range or non-numeric) or an unparseable host such as an
        # unterminated IPv6 literal. That is exactly as unsafe a URL as
        # any other shape this function already rejects, so it is caught
        # here and translated into the same :class:`UnsafeTargetError`
        # every other malformed-URL case raises, never left to propagate
        # as a raw, uncaught ``ValueError`` -- and never echoing that
        # ``ValueError``'s own text, which can itself quote the
        # offending raw input.
        port = parsed.port
    except ValueError as error:
        raise UnsafeTargetError(f"{what} has a malformed URL") from error
    if parsed.scheme != "https":
        raise UnsafeTargetError(f"{what} must use https://")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeTargetError(f"{what} must not contain embedded userinfo")
    if not parsed.hostname:
        raise UnsafeTargetError(f"{what} must name a host")
    if parsed.query:
        raise UnsafeTargetError(f"{what} must not contain a query string")
    if parsed.fragment:
        raise UnsafeTargetError(f"{what} must not contain a fragment")
    origin = f"https://{parsed.hostname}"
    if port is not None:
        origin += f":{port}"
    return origin


def _validate_canary_contract(contract: Mapping[str, object], trusted_origin: object) -> str:
    """Validate *contract* is safe to run at all, returning the single
    HTTPS origin (``scheme://host[:port]``) it is bound to.

    The canary URL must resolve to a plain ``https://host[:port]/path``
    with no userinfo, query string, or fragment, and that resolved
    origin must exactly match *trusted_origin* -- an independently
    supplied, caller-owned staging origin. This project never infers
    "is this really staging" from the contract's own self-asserted
    ``environment`` field, or from a hostname that merely looks like
    staging; only an exact match against a value the caller supplied
    out-of-band is ever trusted.

    Every :class:`UnsafeTargetError` this function raises is fixed,
    redacted text; none of them ever interpolates a value the contract
    itself supplied (the ``environment``/``method``/``expected_status``
    value, a disallowed header's own name, or the URL) since any of
    those could themselves carry a credential or other sensitive
    fragment of the very value being rejected.
    """
    if contract.get("environment") != "staging":
        raise UnsafeTargetError("staging canary requires environment 'staging'")
    if contract.get("destructive") is not False:
        raise UnsafeTargetError("staging canary requires destructive: false")
    method = contract.get("method")
    if not isinstance(method, str) or method.upper() not in _STAGING_CANARY_ALLOWED_METHODS:
        raise UnsafeTargetError("staging canary allows only HTTPS GET/HEAD")
    origin = _parse_canary_https_url(contract.get("url"), what="staging canary url")
    trusted = _parse_canary_https_url(trusted_origin, what="trusted staging origin")
    if origin != trusted:
        raise UnsafeTargetError(
            "staging canary url origin does not match the independently "
            "supplied trusted staging origin"
        )
    if contract.get("query"):
        raise UnsafeTargetError("staging canary must not send request query parameters")
    if contract.get("body"):
        raise UnsafeTargetError("staging canary must not send a request body")
    disallowed_header = _disallowed_canary_header(contract.get("headers"))
    if disallowed_header is not None:
        raise UnsafeTargetError("staging canary must not send a non-allowlisted header")
    expected_status = contract.get("expected_status")
    if expected_status is not None and not _is_valid_http_status(expected_status):
        raise UnsafeTargetError("staging canary expected_status must be an int in 100..599")
    return origin


def _canary_deployment_id(headers: Mapping[str, object]) -> Optional[str]:
    lowered = {str(key).strip().lower(): value for key, value in headers.items()}
    for name in _DEPLOYMENT_ID_HEADER_NAMES:
        if name in lowered and lowered[name] is not None:
            return str(lowered[name])
    return None


def _status_in_2xx_4xx(status_code: object) -> bool:
    """Whether *status_code* falls inside the documented "HTTP 2xx-4xx"
    acceptance range this probe reports when a contract names no exact
    ``expected_status`` -- a non-integer status (including a bare
    ``bool``, which is technically an ``int`` subclass) or one outside
    200-499 (for example a 5xx server error) is never accepted merely
    because nothing more specific was asked for."""
    return _is_valid_http_status(status_code) and 200 <= status_code <= 499


def _bounded_utf8_body_bytes(raw_body: str, max_bytes: int) -> Optional[bytes]:
    """Encode a ``str`` canary response body to UTF-8 without ever
    allocating more than roughly *max_bytes* worth of encoded bytes plus
    one chunk's worth of overhead, returning ``None`` (meaning: reject as
    oversized) the instant the cap is provably exceeded instead of only
    *after* the full string has already been encoded.

    Every UTF-8-encoded character occupies at least one byte, so
    ``len(raw_body)`` -- the character count -- is always a valid lower
    bound on the eventual encoded byte length. This lets the common
    "obviously far too large" case be rejected immediately from the
    character count alone, with no encoding at all. For a *str* that
    passes that cheap precheck but still contains enough multi-byte
    characters to exceed the cap once encoded, this function encodes in
    small, fixed-size character chunks (a plain Python ``str`` slice is
    always a valid code-point boundary, so ``chunk.encode("utf-8")`` per
    slice is safe) and accumulates a running byte total, bailing out
    before the running total is ever allowed to exceed *max_bytes* --
    bounding worst-case memory to roughly *max_bytes* plus one chunk's
    worth of maximum-width UTF-8 characters, never the full original
    string's encoded size.

    A chunk containing an unpaired Unicode surrogate code point (a
    ``str`` this project's own runner boundary can perfectly well hand
    back, for example from a caller that decoded raw bytes leniently)
    cannot be encoded to UTF-8 at all with the strict error handler this
    function uses; that ``UnicodeEncodeError`` is contained here and
    reported the same way as any other unusable body -- ``None`` --
    rather than allowed to propagate as an unhandled exception.
    """
    if len(raw_body) > max_bytes:
        return None
    encoded_chunks = []
    total_bytes = 0
    chunk_chars = _STAGING_CANARY_BODY_ENCODE_CHUNK_CHARS
    for start in range(0, len(raw_body), chunk_chars):
        try:
            chunk_bytes = raw_body[start : start + chunk_chars].encode("utf-8")
        except UnicodeEncodeError:
            return None
        total_bytes += len(chunk_bytes)
        if total_bytes > max_bytes:
            return None
        encoded_chunks.append(chunk_bytes)
    return b"".join(encoded_chunks)


def run_staging_canary(
    contract: Mapping[str, object],
    run: HttpReadRunner,
    *,
    trusted_origin: str,
) -> ProbeResult:
    """Run one optional, narrowly-bounded live HTTP canary against a
    staging environment.

    Accepts only a contract declaring ``environment: "staging"``,
    ``destructive: false``, an HTTPS ``GET``/``HEAD`` *method*, a *url*
    with no userinfo, query string, or fragment, no request *body*, and
    only allowlisted (never authorization-style) headers; any other
    contract raises :class:`UnsafeTargetError` before *run* is ever
    invoked, so an unsafe canary can never reach the network at all.

    Requires *trusted_origin* -- an independently supplied, caller-owned
    ``https://host[:port]`` staging origin -- and refuses to run at all
    unless the contract's URL resolves to exactly that origin; this
    project never infers "is this really staging" from the contract's
    own self-asserted ``environment`` field or from a hostname that
    merely looks like staging. The outbound request always asks the
    runner not to follow redirects; the response object *must* report a
    final URL, and that URL must resolve to the same https origin, or
    the result is reported ``not-verified`` rather than trusted.

    The response itself is validated just as strictly, and every one of
    ``url``, ``headers``, and ``body`` must genuinely be present on the
    response object *at all* -- a response missing one of these
    attributes entirely is reported ``not-verified`` exactly as strictly
    as one that supplies a malformed value for it, and this function
    never fabricates a substitute (silently skipping the same-origin
    check for a missing ``url``, defaulting to an empty headers mapping,
    or hashing an empty ``b""`` body as if that were genuinely observed
    evidence). A non-integer or out-of-range status code, a headers
    value that is not a mapping of plain strings, or a body that is not
    ``bytes``/``str`` is likewise reported ``not-verified`` -- naming
    only the malformed value's type, never the value itself -- rather
    than trusted at face value.

    Records only the observed HTTP status code, the wall-clock duration
    of the call, any deployment-id-style response header, and a hash of
    the response body -- the actual body content is read only long
    enough to hash it and is never itself stored, logged, or returned.

    Every request the runner is asked to make declares a fixed,
    non-negotiable :data:`_STAGING_CANARY_REQUEST_TIMEOUT_SECONDS`
    technical safety timeout. The response body is hashed only up to
    :data:`_STAGING_CANARY_MAX_RESPONSE_BODY_BYTES`; the response's
    headers are searched for a deployment-id only when they stay within
    :data:`_STAGING_CANARY_MAX_RESPONSE_HEADER_COUNT` entries, each
    within :data:`_STAGING_CANARY_MAX_RESPONSE_HEADER_NAME_LENGTH`/
    :data:`_STAGING_CANARY_MAX_RESPONSE_HEADER_VALUE_LENGTH`; and a
    deployment-id longer than
    :data:`_STAGING_CANARY_MAX_DEPLOYMENT_ID_LENGTH` is never recorded.
    Every one of these is a fixed technical safety bound against
    unbounded processing cost, never a customer-tunable policy value;
    exceeding any of them is reported ``not-verified`` exactly like any
    other malformed response, and the oversized/offending raw value is
    never echoed back.
    """
    origin = _validate_canary_contract(contract, trusted_origin)
    request = {
        "method": str(contract["method"]).upper(),
        "url": contract["url"],
        "headers": dict(contract.get("headers") or {}),
        "allow_redirects": False,
        "timeout_seconds": _STAGING_CANARY_REQUEST_TIMEOUT_SECONDS,
    }
    expected_status = contract.get("expected_status")
    expected_label = f"HTTP {expected_status}" if expected_status is not None else "HTTP 2xx-4xx"
    started = time.monotonic()
    try:
        response = run(request)
    except Exception as error:  # noqa: BLE001 - network/tooling failure, not a finding
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-unreachable",
            expected=expected_label,
            observed=f"error_class={type(error).__name__}",
            evidence_refs=(),
        )
    duration_ms = (time.monotonic() - started) * 1000.0

    final_url = getattr(response, "url", _CANARY_RESPONSE_FIELD_MISSING)
    if final_url is _CANARY_RESPONSE_FIELD_MISSING:
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-malformed-response",
            expected=expected_label,
            observed="missing_response_field=url",
            evidence_refs=(),
        )
    try:
        final_origin = _parse_canary_https_url(final_url, what="staging canary response url")
    except UnsafeTargetError:
        final_origin = None
    if final_origin != origin:
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-off-origin-redirect",
            expected=expected_label,
            observed="response_url_off_origin=True",
            evidence_refs=(),
        )

    status_code = getattr(response, "status_code", None)
    if not _is_valid_http_status(status_code):
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-malformed-response",
            expected=expected_label,
            observed=f"malformed_status_type={type(status_code).__name__}",
            evidence_refs=(),
        )
    headers = getattr(response, "headers", _CANARY_RESPONSE_FIELD_MISSING)
    if headers is _CANARY_RESPONSE_FIELD_MISSING:
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-malformed-response",
            expected=expected_label,
            observed="missing_response_field=headers",
            evidence_refs=(),
        )
    if not _is_valid_canary_headers(headers):
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-malformed-response",
            expected=expected_label,
            observed=f"malformed_headers_type={type(headers).__name__}",
            evidence_refs=(),
        )
    deployment_id = _canary_deployment_id(headers)
    if deployment_id is not None and len(deployment_id) > _STAGING_CANARY_MAX_DEPLOYMENT_ID_LENGTH:
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-malformed-response",
            expected=expected_label,
            observed="oversized_deployment_id=True",
            evidence_refs=(),
        )
    raw_body = getattr(response, "body", _CANARY_RESPONSE_FIELD_MISSING)
    if raw_body is _CANARY_RESPONSE_FIELD_MISSING:
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-malformed-response",
            expected=expected_label,
            observed="missing_response_field=body",
            evidence_refs=(),
        )
    if not isinstance(raw_body, (bytes, str)):
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-malformed-response",
            expected=expected_label,
            observed=f"malformed_body_type={type(raw_body).__name__}",
            evidence_refs=(),
        )
    if isinstance(raw_body, bytes):
        body_bytes: Optional[bytes] = raw_body
        if len(body_bytes) > _STAGING_CANARY_MAX_RESPONSE_BODY_BYTES:
            body_bytes = None
    else:
        body_bytes = _bounded_utf8_body_bytes(raw_body, _STAGING_CANARY_MAX_RESPONSE_BODY_BYTES)
    if body_bytes is None:
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-malformed-response",
            expected=expected_label,
            observed="oversized_response_body=True",
            evidence_refs=(),
        )
    response_hash = canonical.sha256_hex(body_bytes)

    observed = (
        f"status={status_code} duration_ms={duration_ms:.1f} "
        f"deployment_id={deployment_id!r} response_sha256=sha256:{response_hash}"
    )
    if expected_status is not None:
        status_matches_contract = status_code == expected_status
    else:
        status_matches_contract = _status_in_2xx_4xx(status_code)
    if not status_matches_contract:
        return ProbeResult(
            probe_id="staging-canary",
            action_id=None,
            path_id=None,
            status="not-verified",
            reason_code="staging-canary-unexpected-status",
            expected=expected_label,
            observed=observed,
            evidence_refs=(),
        )
    return ProbeResult(
        probe_id="staging-canary",
        action_id=None,
        path_id=None,
        status="pass",
        reason_code="staging-canary-nondestructive-read",
        expected=expected_label,
        observed=observed,
        evidence_refs=(),
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        _run_as_child()
    elif len(sys.argv) > 1 and sys.argv[1] == _PATH_CHILD_ARG:
        _run_path_as_child()
    elif len(sys.argv) > 1 and sys.argv[1] == _TASK6_CHILD_ARG:
        _run_task6_as_child()
    else:
        raise SystemExit(
            "probes.py is a library module; its child entry points are only "
            "ever invoked internally by run_application_probe and the "
            "Task 6 approval/output probes"
        )
