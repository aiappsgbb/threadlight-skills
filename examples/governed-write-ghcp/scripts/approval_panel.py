#!/usr/bin/env python3
"""Serve the operator queue with one-click Approve / Reject for the approver.

Serves a queue folder written by ``review_queue.py --panel`` on 127.0.0.1 only.
The page's Approve or Reject button posts ``{operation_id, approved}`` here; this
server finds the exact saved pending record, signs the approver in with their own
Entra account (interactive browser, delegated ``Governance.Approve``) and records
the decision through the control plane's one-use ``decide`` call. It never
executes the business write: the agent's resume does, after the gateway consumes
the grant. The control plane remains the authority (approver subject, role,
expiry, one use); this server only refuses cross-site posts, unknown operations
and a second click.

Only for deployments whose approval channel is ``delegated``. With the native
Outlook channel the control plane accepts decisions only from the email.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import http.server
import json
from pathlib import Path
import secrets
import threading

MAX_BODY = 4096


def _records(queue):
    records = {}
    for path in sorted(Path(queue).glob("pending-*.json")):
        record = json.loads(path.read_text())
        records[record["operation_id"]] = record
    return records


def serve(queue, *, port, decider, host="127.0.0.1"):
    queue = Path(queue).resolve()
    records = _records(queue)
    decided, lock = set(), threading.Lock()
    token = secrets.token_urlsafe(32)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(queue), **kwargs)

        def log_message(self, *args):
            pass

        def _json(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path.split("?")[0] != "/data/cases.json":
                return super().do_GET()
            doc = json.loads((queue / "data/cases.json").read_text())
            doc["config"]["approval"] = {"mode": "panel", "url": "/approve", "token": token}
            self._json(200, doc)

        def do_POST(self):
            bound = self.server.server_address[1]
            origins = {f"http://127.0.0.1:{bound}", f"http://localhost:{bound}"}
            if (self.path != "/approve" or self.headers.get("Origin") not in origins
                    or not hmac.compare_digest(self.headers.get("X-Threadlight-Panel", ""), token)):
                return self._json(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                if not 0 < length <= MAX_BODY:
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                operation, approved = body["operation_id"], body["approved"]
                if set(body) != {"operation_id", "approved"} or type(approved) is not bool \
                        or not isinstance(operation, str):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "operation_id_and_boolean_approved_required"})
            record = records.get(operation)
            if record is None:
                return self._json(404, {"error": "unknown_pending_operation"})
            with lock:
                if operation in decided:
                    return self._json(409, {"error": "already_decided_here"})
                try:
                    result = decider(record, approved)
                except Exception:  # noqa: BLE001 - never echo identity or service details to the page
                    return self._json(502, {"error": "review_not_completed"})
                decided.add(operation)
            return self._json(200, result)

    server = http.server.ThreadingHTTPServer((host, port), Handler)
    server.panel_token = token
    return server


def entra_decider(*, control_plane_url, scope, tenant, client_id, role):
    """Sign the approver in interactively and record their decision as them."""
    from azure.identity import InteractiveBrowserCredential
    from govern_control_plane.client import ApprovalUnavailable
    from govern_control_plane.review import decide, validate_pending_review

    def run(record, approved):
        validate_pending_review(record)
        with InteractiveBrowserCredential(tenant_id=tenant, client_id=client_id, timeout=180) as browser:
            token = browser.get_token(scope)

        class Delegated:
            async def get_token(self, requested):
                if requested != scope:
                    raise ApprovalUnavailable()
                return token

        return asyncio.run(decide(record, approved=approved, role=role, base_url=control_plane_url,
                                  scope=scope, credential=Delegated()))
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True, help="folder from review_queue.py --panel")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--control-plane-url", required=True)
    parser.add_argument("--scope", required=True, help="api://<control-api-id>/Governance.Approve")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--client-id", required=True, help="public client pre-authorized for Governance.Approve")
    parser.add_argument("--role", default="Approver")
    args = parser.parse_args()
    server = serve(args.queue, port=args.port, decider=entra_decider(
        control_plane_url=args.control_plane_url, scope=args.scope, tenant=args.tenant,
        client_id=args.client_id, role=args.role))
    print(f"approval panel: http://127.0.0.1:{server.server_address[1]}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
