"""Invoke a GHCP SDK hosted agent via Invocations SSE endpoint (advanced/optional).

The PRIMARY invoke path is `azd ai agent invoke <name> '{"input": "..."}'
--protocol invocations --output raw` — see README.md § "Invoking the Agent".
This script is an optional SDK-level alternative for callers that need to
parse the raw SSE stream directly (e.g. a bot integration), not the
recommended first path.

Parses both assistant.message and assistant.message_delta events.
Use as a library or run directly to test an agent.

Usage:
    export AZURE_AI_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
    export AGENT_NAME="my-agent"
    # Required, write-once: a NEW path per invocation (the file is created
    # with O_EXCL and mode 0600; an existing path is rejected, never reused).
    export INVOCATION_EVIDENCE_FILE="$(mktemp -d)/invocation-evidence.jsonl"
    python invoke_agent.py "What is the capital of France?"
    # Durable turns only: status / final answer of an earlier turn by its id
    python invoke_agent.py --follow <invocation-id> [<agent-session-id>]

Dependency: ``operation_evidence`` is the canonical helper in
``references/hosted-agent/maf/references/python/operation_evidence.py``.
Inside the skill layout this script finds it automatically. When you copy
the script elsewhere, copy ``operation_evidence.py`` beside it or set
``PYTHONPATH=<threadlight-deploy>/references/hosted-agent/maf/references/python``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests
from azure.identity import DefaultAzureCredential

try:
    from operation_evidence import SSEFrames, begin_operation, error_metadata, safe_id
except ModuleNotFoundError as exc:
    if exc.name != "operation_evidence":
        raise
    _HELPER_DIR = Path(__file__).resolve().parents[2] / "maf" / "references" / "python"
    if not (_HELPER_DIR / "operation_evidence.py").is_file():
        raise ModuleNotFoundError(
            "operation_evidence is required: copy "
            "references/hosted-agent/maf/references/python/operation_evidence.py "
            "beside invoke_agent.py or add that directory to PYTHONPATH",
            name="operation_evidence",
        ) from exc
    sys.path.insert(0, str(_HELPER_DIR))
    from operation_evidence import SSEFrames, begin_operation, error_metadata, safe_id


def _follow_url(url: str, turn_id: str, session_id: str | None, after: int | None, *, stream: bool) -> str:
    """GET /invocations/{id}: status, or the turn's replay stream after a cursor."""
    base, _, query = url.partition("?")
    params = [p for p in query.split("&") if p.startswith("api-version=")]
    if stream:
        params.append("stream=true")
        if after is not None:
            params.append(f"starting_after={after}")
    if session_id:
        params.append(f"agent_session_id={session_id}")
    return f"{base.rstrip('/')}/{turn_id}" + ("?" + "&".join(params) if params else "")


def invoke_invocations(
    endpoint: str,
    token: str,
    agent_name: str,
    query: str,
    timeout: int = 600,
    *,
    record=None,
    invocations_url: str | None = None,
    reconnect_backoff: float = 2.0,
) -> str:
    """Invoke via Invocations SSE endpoint and extract response text.

    Parses both event types:
      - assistant.message: full final message (preferred)
      - assistant.message_delta: streaming content chunks (fallback)

    With durable turns (container THREADLIGHT_DURABLE_TURNS=1) the response
    carries ``x-threadlight-turn-id``. It is recorded before the body is read
    (``native_retrievable_id: true``); a dropped stream reconnects with
    ``GET /invocations/{id}?stream=true&starting_after=<last id>`` until the
    deadline, and ``--follow <id>`` re-attaches later. Without it the turn has
    no retrievable id and a drop leaves the outcome UNKNOWN.
    """
    if not callable(record):
        raise ValueError("A durable operation record is required before invoking")
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 1800:
        raise ValueError("Use a finite invocation deadline between 0 and 1800 seconds")
    # GA endpoint — no `Foundry-Features: *=V1Preview` header (removed for
    # GA, Azure/azure-dev PR #8866). `api-version=v1` is the current GA
    # literal for this endpoint.
    url = invocations_url or f"{endpoint.rstrip('/')}/agents/{agent_name}/endpoint/protocols/invocations?api-version=v1"
    begin_operation(record, target=url, intent={"input": query})
    deadline = time.monotonic() + timeout
    headers = {"Authorization": f"Bearer {token}"}

    def post_once(*args, **kwargs):
        try:
            return requests.post(*args, **kwargs)
        except requests.RequestException as error:
            record("invoke-unresolved", error_metadata(error))
            raise
    resp = post_once(
        url,
        json={"input": query},
        headers={**headers, "Content-Type": "application/json"},
        stream=True,
        timeout=(min(10, timeout), min(timeout, 60)),
    )
    try:
        record("http-response", {"status_code": resp.status_code,
                                "request_id": safe_id(resp.headers.get("x-request-id"))})
        turn_id = safe_id(resp.headers.get("x-threadlight-turn-id"))
        session_id = safe_id(resp.headers.get("x-agent-session-id"))
        if turn_id:
            record("durable-id", {"runtime_invocation_id": turn_id, "agent_session_id": session_id,
                                  "native_retrievable_id": True})
    except BaseException:
        resp.close()
        raise
    state = {"message": "", "delta": "", "tools": 0, "cursor": None}

    def consume(response) -> bool:
        frames = SSEFrames()
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if time.monotonic() >= deadline:
                raise TimeoutError("Invocation observation expired; reconcile original operation")
            if line.startswith("id:") and line[3:].strip().isdigit():
                state["cursor"] = int(line[3:].strip())
            frame = frames.feed(line)
            if frame is None:
                continue
            event_name, event = frame
            if event_name == "done":
                record("runtime-completed", {"runtime_invocation_id": safe_id(event.get("invocation_id")),
                                              "native_retrievable_id": bool(turn_id),
                                              "status": safe_id(event.get("status")), "effect": "UNKNOWN"})
                if event.get("status") == "failed":
                    raise RuntimeError("Runtime turn failed; original effect remains unknown")
                return True
            event_type = event.get("type", "")
            if event_type in ("error", "session.error"):
                raise RuntimeError("Runtime invocation failed; original effect remains unknown")
            content = event.get("data", {}).get("content", "")
            if event_type == "assistant.message" and content:
                state["message"] = content
            elif event_type == "assistant.message_delta" and content:
                state["delta"] += content
            elif event_type == "tool.execution_start":
                state["tools"] += 1
        return False

    try:
        while True:
            try:
                if resp is None:
                    raise ConnectionError("reconnect failed")
                if consume(resp):
                    break
                interrupted = RuntimeError("Invocation stream ended without completion")
            except (requests.RequestException, ConnectionError) as error:
                interrupted = error
            finally:
                if resp is not None:
                    resp.close()
            if not turn_id:
                raise RuntimeError("Invocation stream ended without completion; reconcile original operation") \
                    from interrupted
            if time.monotonic() >= deadline:
                raise TimeoutError("Invocation observation expired; follow the turn by its id")
            record("stream-interrupted", {"runtime_invocation_id": turn_id, "cursor": state["cursor"],
                                          **error_metadata(interrupted)})
            time.sleep(reconnect_backoff)
            try:
                resp = requests.get(_follow_url(url, turn_id, session_id, state["cursor"], stream=True),
                                    headers=headers, stream=True,
                                    timeout=(10, max(1, min(60, deadline - time.monotonic()))))
            except requests.RequestException as error:
                resp = None
                record("reconnect-failed", error_metadata(error))
                continue
            record("reconnected", {"runtime_invocation_id": turn_id, "status_code": resp.status_code})
    except (ValueError, RuntimeError, TimeoutError, requests.RequestException) as error:
        record("invoke-unresolved", error_metadata(error))
        raise

    return state["message"] if state["message"] else state["delta"]


def follow_invocation(url: str, token: str, turn_id: str, session_id: str | None = None) -> dict:
    """Status of a durable turn: {invocation_id, status, answer?, native_retrievable_id}."""
    resp = requests.get(_follow_url(url, turn_id, session_id, None, stream=False),
                        headers={"Authorization": f"Bearer {token}"}, timeout=(10, 60))
    resp.raise_for_status()
    return resp.json()


def main():
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    agent_name = os.environ.get("AGENT_NAME", "my-agent")
    evidence_path = os.environ.get("INVOCATION_EVIDENCE_FILE", "").strip()

    if not endpoint:
        print("ERROR: Set AZURE_AI_PROJECT_ENDPOINT")
        sys.exit(1)
    if len(sys.argv) >= 3 and sys.argv[1] == "--follow":
        # Read-only status of a durable turn; optional third argument: agent_session_id.
        url = f"{endpoint.rstrip('/')}/agents/{agent_name}/endpoint/protocols/invocations?api-version=v1"
        token = DefaultAzureCredential().get_token("https://ai.azure.com/.default").token
        print(json.dumps(follow_invocation(url, token, sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None),
                         indent=2))
        return
    if not evidence_path:
        print("ERROR: Set INVOCATION_EVIDENCE_FILE to a new, write-once path per "
              "invocation, e.g. \"$(mktemp -d)/invocation-evidence.jsonl\"")
        sys.exit(1)

    def _reused():
        print(f"ERROR: INVOCATION_EVIDENCE_FILE {evidence_path} already exists; the "
              "evidence file is write-once. Use a new path for this invocation and "
              "keep the existing file as the record of the earlier attempt.")
        sys.exit(1)

    if os.path.lexists(evidence_path):
        _reused()

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Hello"

    credential = DefaultAzureCredential()
    token = credential.get_token("https://ai.azure.com/.default").token

    print(f"Invoking {agent_name}: {query[:80]}...")
    t0 = time.time()
    try:
        descriptor = os.open(evidence_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _reused()
    with os.fdopen(descriptor, "w", encoding="utf-8") as evidence:
        def record(event, data):
            evidence.write(json.dumps({"event": event, **data}) + "\n")
            evidence.flush()
            os.fsync(evidence.fileno())
        response = invoke_invocations(endpoint, token, agent_name, query, record=record)
    elapsed = time.time() - t0

    print(f"\n--- Response ({len(response)} chars, {elapsed:.1f}s) ---")
    print(response)


if __name__ == "__main__":
    main()
