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


def invoke_invocations(
    endpoint: str,
    token: str,
    agent_name: str,
    query: str,
    timeout: int = 600,
    *,
    record=None,
) -> str:
    """Invoke via Invocations SSE endpoint and extract response text.

    Parses both event types:
      - assistant.message: full final message (preferred)
      - assistant.message_delta: streaming content chunks (fallback)
    """
    if not callable(record):
        raise ValueError("A durable operation record is required before invoking")
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 1800:
        raise ValueError("Use a finite invocation deadline between 0 and 1800 seconds")
    # GA endpoint — no `Foundry-Features: *=V1Preview` header (removed for
    # GA, Azure/azure-dev PR #8866). `api-version=v1` is the current GA
    # literal for this endpoint.
    url = f"{endpoint.rstrip('/')}/agents/{agent_name}/endpoint/protocols/invocations?api-version=v1"
    begin_operation(record, target=url, intent={"input": query})
    deadline = time.monotonic() + timeout
    def post_once(*args, **kwargs):
        try:
            return requests.post(*args, **kwargs)
        except requests.RequestException as error:
            record("invoke-unresolved", error_metadata(error))
            raise
    resp = post_once(
        url,
        json={"input": query},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        stream=True,
        timeout=(min(10, timeout), min(timeout, 60)),
    )
    try:
        record("http-response", {"status_code": resp.status_code,
                                "request_id": safe_id(resp.headers.get("x-request-id"))})
    except BaseException:
        resp.close()
        raise
    message_text = ""
    delta_text = ""
    tool_count = 0
    frames = SSEFrames()
    completed = False
    try:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if time.monotonic() >= deadline:
                raise TimeoutError("Invocation observation expired; reconcile original operation")
            frame = frames.feed(line)
            if frame is None:
                continue
            event_name, event = frame
            if event_name == "done":
                record("runtime-completed", {"runtime_invocation_id": safe_id(event.get("invocation_id")),
                                              "native_retrievable_id": False, "effect": "UNKNOWN"})
                completed = True
                break
            event_type = event.get("type", "")
            if event_type in ("error", "session.error"):
                raise RuntimeError("Runtime invocation failed; original effect remains unknown")
            content = event.get("data", {}).get("content", "")

            if event_type == "assistant.message" and content:
                message_text += content
            elif event_type == "assistant.message_delta" and content:
                delta_text += content
            elif event_type == "tool.execution_start":
                tool_count += 1
        if not completed:
            raise RuntimeError("Invocation stream ended without completion; reconcile original operation")
    except (ValueError, RuntimeError, TimeoutError, requests.RequestException) as error:
        record("invoke-unresolved", error_metadata(error))
        raise
    finally:
        resp.close()

    return message_text if message_text else delta_text


def main():
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    agent_name = os.environ.get("AGENT_NAME", "my-agent")
    evidence_path = os.environ.get("INVOCATION_EVIDENCE_FILE", "").strip()

    if not endpoint:
        print("ERROR: Set AZURE_AI_PROJECT_ENDPOINT")
        sys.exit(1)
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
