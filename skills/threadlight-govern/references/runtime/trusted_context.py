"""Optional cooperative trusted-host evidence, never model or middleware claims.

Backend adapters record actual read results in the native run's private scope.
Only a selected pre-tool gate invokes the producer. Its bounded JSON snapshot is
bound to the native action/principal/session and expires within thirty seconds.
The effect adapter must use the returned revision in a backend conditional write;
this API cannot attest hostile Python code or make an external database atomic.
"""
from dataclasses import dataclass
from datetime import datetime
import json


@dataclass(frozen=True)
class TrustedContextSnapshot:
    facts: dict
    expires_at: datetime


def bounded_copy(value):
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(wire.encode()) > 65536:
        raise ValueError("trusted_context_limit")
    return json.loads(wire)


def record_trusted_read(name, result):
    from .maf_agent_hooks_acs import _execution
    state = _execution.get()
    if state is None or "trusted_reads" not in state:
        return
    try:
        if not isinstance(name, str) or len(name) > 128 or len(state["trusted_reads"]) >= 32:
            raise ValueError("trusted_context_limit")
        record = bounded_copy({"name": name, "result": result})
        bounded_copy([*state["trusted_reads"], record])
        state["trusted_reads"].append(record)
    except Exception:
        # A failed observation cannot make an unbound read unavailable.
        state["trusted_reads_failed"] = True


def trusted_effect_snapshot():
    from .maf_agent_hooks_acs import _effect_authorization, GovernedToolUnavailable
    authorization = _effect_authorization.get()
    if authorization is None:
        raise GovernedToolUnavailable("threadlight:trusted_context_unavailable")
    value = authorization[3].get("trusted")
    if value is None:
        raise GovernedToolUnavailable("threadlight:trusted_context_unavailable")
    return bounded_copy(value)
