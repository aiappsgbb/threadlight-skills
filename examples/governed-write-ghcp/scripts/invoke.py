#!/usr/bin/env python3
"""Invoke the governed-write GHCP hosted agent and keep a write-once evidence record.

* Every SSE frame is appended to ``--evidence`` (new path per invocation, O_EXCL).
* A host-owned ``event: governance_pending`` frame (emitted by the generated GHCP
  relay, not by the model) is saved verbatim to ``--pending`` for the human
  reviewer's ``threadlight-review-action --pending``. Pending means NO effect.
* ``--resume <pending.json>`` builds the resume prompt from that host record:
  the exact proposed arguments plus ``governance_operation_id``. The id selects
  the original operation; the gateway still re-checks policy, facts, identity
  and consumes the one-use human grant before the effect.

Never retry an invocation whose outcome is unknown; reconcile first.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time


def write_once(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8")


def resume_prompt(pending):
    if pending.get("status") != "pending_approval":
        raise ValueError("not_a_pending_approval_record")
    arguments = dict(pending["proposed_arguments"], governance_operation_id=pending["operation_id"])
    return ("The human Approver has decided operation " + pending["operation_id"] + ". "
            "Do NOT call returns_get_case and do not change any value: keep this original expected_etag "
            "even if the case has changed since. Call returns_apply_decision exactly once with exactly these "
            "arguments and nothing else, then report the tool result: " + json.dumps(arguments, sort_keys=True))


def frames(lines):
    event, data = "message", []
    for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)


def main():
    import requests
    from azure.identity import AzureCliCredential

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent", default="returns-governed")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--pending", type=Path, help="where to save a governance_pending record")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prompt")
    group.add_argument("--resume", type=Path, help="pending.json produced by an earlier invocation")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    prompt = args.prompt or resume_prompt(json.loads(args.resume.read_text()))
    url = (f"{args.project_endpoint.rstrip('/')}/agents/{args.agent}"
           "/endpoint/protocols/invocations?api-version=v1")
    token = AzureCliCredential().get_token("https://ai.azure.com/.default").token
    started = time.time()
    text, pending, completed = [], None, False
    with write_once(args.evidence) as evidence:
        def record(kind, body):
            evidence.write(json.dumps({"t": round(time.time() - started, 3), "kind": kind, **body}) + "\n")
            evidence.flush()
            os.fsync(evidence.fileno())
        record("intent", {"url": url, "input": prompt})
        response = requests.post(url, json={"input": prompt}, stream=True, timeout=(10, args.timeout),
                                 headers={"Authorization": "Bearer " + token})
        record("http", {"status": response.status_code, "request_id": response.headers.get("x-request-id")})
        response.raise_for_status()
        for event, data in frames(response.iter_lines(decode_unicode=True)):
            record("frame", {"event": event, "data": data})
            body = json.loads(data)
            if event == "governance_pending":
                pending = body
            elif event == "done":
                completed = True
            elif body.get("type") == "assistant.message":
                text.append(body.get("data", {}).get("content", ""))
            elif body.get("type") == "error":
                record("result", {"state": "unknown", "reason": body.get("message")})
        record("result", {"completed": completed, "pending": pending is not None})
    if pending is not None:
        if not args.pending:
            raise SystemExit("governance_pending received: rerun with --pending to keep it")
        with write_once(args.pending) as handle:
            json.dump(pending, handle, indent=2)
    print("\n".join(t for t in text if t))
    print(json.dumps({"completed": completed, "pending_saved": str(args.pending) if pending else None,
                      "seconds": round(time.time() - started, 1)}))


if __name__ == "__main__":
    main()
