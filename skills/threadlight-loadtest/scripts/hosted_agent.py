"""Load driver for a Foundry hosted agent on the Invocations protocol (GHCP runtime).

An injected ``LoadAdapter`` for ``loadtest.run_loadtest``: the budget,
production and endpoint gates run first, then this adapter drives the real
hosted-agent endpoint once through explicit phases and returns one sample per
request. Nothing here is mocked: requests go to
``{project}/agents/{agent}/endpoint/protocols/invocations?api-version=v1`` with
an Entra token (``https://ai.azure.com/.default``) and stream SSE back.

Phases make cold and warm explicit:

* ``session_mode: "new"`` gives every request a fresh ``agent_session_id``.
  Hosted agents run one VM-isolated sandbox per session, so each of these
  requests starts a sandbox (cold) -- see
  https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#scaling-and-right-sizing
* ``session_mode: "per-user"`` gives each virtual user one session for the
  whole phase: its first request starts the sandbox, the rest reuse it (warm).
  Every request sends ``"new_conversation": true`` so the conversation does
  not grow from request to request.

Per request it records only timings and identifiers: client latency, time to
response headers, client time to first token (first streamed text), HTTP
status, an error class, the invocation id from the ``done`` event and the
number of tool calls seen. Prompts and answers never leave this module.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

PHASE_KEYS = frozenset({"name", "concurrency", "requests", "session_mode", "pause_before_s", "cold"})
SESSION_MODES = ("new", "per-user")
TOKEN_SCOPE = "https://ai.azure.com/.default"
_TEXT_EVENTS = ("assistant.message_delta", "assistant.streaming_delta", "assistant.message")
MAX_TOTAL_REQUESTS = 2000


class PhaseError(ValueError):
    """A phase definition is malformed (a caller mistake, raised before any request)."""


def validate_phases(phases: Any) -> list[dict]:
    if not isinstance(phases, list) or not phases:
        raise PhaseError("phases must be a non-empty list")
    names, total = set(), 0
    for index, phase in enumerate(phases):
        if not isinstance(phase, dict):
            raise PhaseError(f"phases[{index}] must be an object")
        unknown = set(phase) - PHASE_KEYS
        if unknown:
            raise PhaseError(f"phases[{index}] has unknown key(s): {', '.join(sorted(unknown))}")
        name = phase.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise PhaseError(f"phases[{index}].name must be a unique non-empty string")
        names.add(name)
        for key in ("concurrency", "requests"):
            value = phase.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise PhaseError(f"phases[{index}].{key} must be a positive integer")
        if phase.get("session_mode", "per-user") not in SESSION_MODES:
            raise PhaseError(f"phases[{index}].session_mode must be one of {SESSION_MODES}")
        pause = phase.get("pause_before_s", 0)
        if isinstance(pause, bool) or not isinstance(pause, (int, float)) or pause < 0:
            raise PhaseError(f"phases[{index}].pause_before_s must be a nonnegative number")
        total += phase["requests"]
    if total > MAX_TOTAL_REQUESTS:
        raise PhaseError(f"phases request {total} requests in total; the cap is {MAX_TOTAL_REQUESTS}")
    return [dict(phase, session_mode=phase.get("session_mode", "per-user"),
                 cold=bool(phase.get("cold", phase.get("session_mode") == "new"))) for phase in phases]


def default_phases(*, max_concurrency: int = 10, steady_requests: int = 200, cold_requests: int = 3,
                   ramp_steps: Sequence[int] = (1, 2, 5), ramp_requests: int = 10) -> list[dict]:
    """Cold sandboxes first, then a ramp, then steady concurrency (all per-user sessions)."""
    phases: list[dict] = [{"name": "cold", "concurrency": 1, "requests": cold_requests, "session_mode": "new"}]
    for step in ramp_steps:
        if step < max_concurrency:
            phases.append({"name": f"ramp-{step}", "concurrency": step, "requests": max(step, ramp_requests)})
    phases.append({"name": "steady", "concurrency": max_concurrency, "requests": steady_requests})
    return validate_phases(phases)


def phase_request_count(phases: Iterable[Mapping[str, Any]]) -> int:
    return sum(int(p["requests"]) for p in phases)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class RequestRecord:
    phase: str
    cold_phase: bool
    worker: int
    session_id: str
    started_at: str
    client_ms: float
    status: Optional[int]
    error_class: Optional[str]
    headers_ms: Optional[float] = None
    first_token_ms: Optional[float] = None
    invocation_id: Optional[str] = None
    tool_events: int = 0
    session_request_index: int = 0

    @property
    def success(self) -> bool:
        return self.error_class is None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__} | {"success": self.success}


class TokenProvider:
    """Thread-safe Entra token cache over an azure-identity credential."""

    def __init__(self, credential: Any, scope: str = TOKEN_SCOPE) -> None:
        self._credential, self._scope = credential, scope
        self._lock = threading.Lock()
        self._token: Any = None

    def __call__(self) -> str:
        with self._lock:
            if self._token is None or self._token.expires_on - time.time() < 300:
                self._token = self._credential.get_token(self._scope)
            return self._token.token


def iter_sse(lines: Iterable[str]):
    """Yield ``(event_name, data)`` per SSE frame; ``data`` is parsed JSON or ``None``."""
    event, data = None, []
    for raw in lines:
        line = raw.rstrip("\r") if isinstance(raw, str) else raw.decode("utf-8", "replace").rstrip("\r")
        if not line:
            if data or event:
                text = "\n".join(data)
                try:
                    parsed = json.loads(text) if text else None
                except ValueError:
                    parsed = None
                yield event or "message", parsed
            event, data = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data or event:
        text = "\n".join(data)
        try:
            yield event or "message", (json.loads(text) if text else None)
        except ValueError:
            yield event or "message", None


def invocations_url(project_endpoint: str, agent_name: str) -> str:
    return f"{project_endpoint.rstrip('/')}/agents/{agent_name}/endpoint/protocols/invocations?api-version=v1"


def invoke_once(http: Any, url: str, token: str, prompt: str, session_id: str, *,
                timeout_s: float = 300.0, clock: Callable[[], float] = time.perf_counter) -> dict:
    """One streamed invocation. Returns timings and identifiers, never content."""
    started = clock()
    out: dict[str, Any] = {"status": None, "error_class": None, "headers_ms": None, "first_token_ms": None,
                           "invocation_id": None, "tool_events": 0}
    try:
        response = http.post(url, params={"agent_session_id": session_id},
                             json={"input": prompt, "new_conversation": True},
                             headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
                             stream=True, timeout=(15, timeout_s))
    except Exception as exc:  # noqa: BLE001 - classified, never raised into the run
        out["error_class"] = "timeout" if "timeout" in type(exc).__name__.lower() else "transport"
        out["client_ms"] = (clock() - started) * 1000
        return out
    try:
        out["status"] = response.status_code
        out["headers_ms"] = (clock() - started) * 1000
        header_id = response.headers.get("x-agent-invocation-id") if hasattr(response, "headers") else None
        if response.status_code != 200:
            out["error_class"] = "throttled" if response.status_code == 429 else f"http_{response.status_code}"
        else:
            done = False
            for event, data in iter_sse(response.iter_lines(decode_unicode=True)):
                if event == "done":
                    done = True
                    if isinstance(data, dict):
                        out["invocation_id"] = str(data.get("invocation_id") or "") or None
                    break
                kind = data.get("type") if isinstance(data, dict) else None
                if kind in ("error", "session.error"):
                    out["error_class"] = "agent_error"
                elif kind == "tool.execution_start":
                    out["tool_events"] += 1
                elif kind in _TEXT_EVENTS and out["first_token_ms"] is None:
                    payload = data.get("data") or {}
                    if payload.get("content") or payload.get("delta_content") or payload.get("deltaContent"):
                        out["first_token_ms"] = (clock() - started) * 1000
            if not done and out["error_class"] is None:
                out["error_class"] = "stream_incomplete"
        out["invocation_id"] = out["invocation_id"] or header_id
    except Exception as exc:  # noqa: BLE001
        out["error_class"] = out["error_class"] or ("timeout" if "timeout" in type(exc).__name__.lower()
                                                    else "stream_error")
    finally:
        close = getattr(response, "close", None)
        if close:
            close()
    out["client_ms"] = (clock() - started) * 1000
    return out


@dataclass
class PhaseRunner:
    """Runs phases with a bounded worker pool; ``invoke`` is injectable for tests."""

    invoke: Callable[[str, str], dict]
    prompts: Sequence[str]
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    sleep: Callable[[float], None] = time.sleep
    records: list = field(default_factory=list)
    phase_windows: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.prompts:
            raise PhaseError("at least one prompt is required")
        self._prompt_index = 0
        self._lock = threading.Lock()

    def _next_prompt(self) -> str:
        with self._lock:
            prompt = self.prompts[self._prompt_index % len(self.prompts)]
            self._prompt_index += 1
            return prompt

    def _session_id(self, phase: str, worker: int, seq: int, mode: str) -> str:
        suffix = f"{worker}-{seq}" if mode == "new" else f"{worker}"
        return f"tlperf-{self.run_id}-{phase}-{suffix}"[:63]

    def run_phase(self, phase: Mapping[str, Any]) -> None:
        if phase.get("pause_before_s"):
            self.sleep(float(phase["pause_before_s"]))
        remaining = [int(phase["requests"])]
        counter_lock = threading.Lock()

        def worker(index: int) -> None:
            seq = 0
            while True:
                with counter_lock:
                    if remaining[0] <= 0:
                        return
                    remaining[0] -= 1
                session_id = self._session_id(phase["name"], index, seq, phase["session_mode"])
                started_at = _now_iso()
                result = self.invoke(self._next_prompt(), session_id)
                record = RequestRecord(
                    phase=phase["name"], cold_phase=bool(phase["cold"]), worker=index, session_id=session_id,
                    started_at=started_at, client_ms=float(result.get("client_ms") or 0.0),
                    status=result.get("status"), error_class=result.get("error_class"),
                    headers_ms=result.get("headers_ms"), first_token_ms=result.get("first_token_ms"),
                    invocation_id=result.get("invocation_id"), tool_events=int(result.get("tool_events") or 0),
                    session_request_index=0 if phase["session_mode"] == "new" else seq)
                with self._lock:
                    self.records.append(record)
                seq += 1

        start = time.time()
        with ThreadPoolExecutor(max_workers=int(phase["concurrency"])) as pool:
            for future in [pool.submit(worker, i) for i in range(int(phase["concurrency"]))]:
                future.result()
        self.phase_windows[phase["name"]] = {"concurrency": int(phase["concurrency"]),
                                             "requests": int(phase["requests"]),
                                             "session_mode": phase["session_mode"], "cold": bool(phase["cold"]),
                                             "duration_s": round(time.time() - start, 3)}

    def run(self, phases: Sequence[Mapping[str, Any]]) -> list:
        for phase in validate_phases(list(phases)):
            self.run_phase(phase)
        return self.records


class HostedAgentAdapter:
    """``LoadAdapter`` that drives the hosted agent, then joins its telemetry.

    ``join`` is called once with the request records after the run and returns
    ``{invocation_id: {"tokens": int, "cold_start_ms": float|None}}`` (see
    ``waterfall.join_tokens``). When it is missing or fails, samples carry
    ``tokens: 0`` and the adapter reports ``partial``: an unmeasured token
    count is never presented as zero tokens.
    """

    name = "foundry-hosted-invocations"

    def __init__(self, runner: PhaseRunner, phases: Sequence[Mapping[str, Any]],
                 join: Optional[Callable[[list], dict]] = None) -> None:
        self.runner, self.phases, self.join = runner, validate_phases(list(phases)), join
        self.joined: dict = {}
        self.join_error: Optional[str] = None

    def run(self, profile: Mapping[str, Any]) -> dict:
        records = self.runner.run(self.phases)
        if self.join is not None:
            try:
                self.joined = self.join(records) or {}
            except Exception as exc:  # noqa: BLE001
                self.join_error = f"telemetry join failed: {type(exc).__name__}"
        samples = []
        for record in records:
            joined = self.joined.get(record.invocation_id or "", {})
            sample = {"latency_ms": round(record.client_ms, 1), "success": record.success,
                      "tokens": int(joined.get("tokens") or 0), "observed_at": record.started_at}
            if record.cold_phase and joined.get("cold_start_ms") is not None:
                sample["cold_start_latency_ms"] = round(float(joined["cold_start_ms"]), 1)
            samples.append(sample)
        matched = sum(1 for r in records if r.invocation_id in self.joined)
        if not records:
            return {"status": "partial", "samples": [], "error": "no requests were sent"}
        if self.join is None or self.join_error or matched == 0:
            return {"status": "partial", "samples": samples,
                    "error": self.join_error or "no server telemetry joined: tokens per request not measured"}
        return {"status": "complete", "samples": samples}
