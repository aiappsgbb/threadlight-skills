# Canonical client for the long-running mode (store + background + stream,
# durable response id, cursor reconnect, snapshot reset).
#
# Source of truth for `../../README.md § Long-running mode (opt-in, crash-resilient)`.
# Pair it with long_running.py. Standard library only, so a pilot UI, an
# orchestrator or a test harness can vendor it.
#
# Protocol rules it applies (Foundry AgentServer Responses 2.4.0):
#   - Start with store=true, background=true, stream=true. Without stream=true
#     at creation the response can never be replayed (HTTP 400 on ?stream=true).
#   - Persist response_id, agent_session_id (x-agent-session-id header) and the
#     last sequence_number BEFORE acting on an event, so a crash of the CLIENT
#     loses nothing either.
#   - Reconnect with GET /responses/{id}?stream=true&starting_after=<last seq>.
#   - If the stream ends without a terminal event, read GET /responses/{id}: the
#     stored response is authoritative (after a hosted session stop the replay
#     stream was seen to stop at the pre-stop events).
#   - Snapshot reset: a response.in_progress after the first one carries the
#     recovered snapshot. Replace the accumulated output with its output items
#     (same ids) instead of appending, so recovered items are never duplicated.
#
# Hosted endpoint (Entra token, scope https://ai.azure.com/.default):
#   {project_endpoint}/agents/{agent}/endpoint/protocols/openai/responses?api-version=v1
# Local: http://localhost:8088/responses
#
# Usage:
#   python long_running_client.py start  --endpoint URL --input "..." --state run.json [--follow]
#   python long_running_client.py follow --state run.json [--deadline 3600]

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator

TERMINAL_EVENTS = {"response.completed", "response.failed", "response.cancelled", "response.incomplete"}
TERMINAL_STATUS = {"completed", "failed", "cancelled", "incomplete"}


def _bearer() -> str | None:
    """Entra token for a hosted endpoint; None for a local agent."""
    token = os.environ.get("THREADLIGHT_RESPONSES_TOKEN")
    if token:
        return token
    if os.environ.get("THREADLIGHT_RESPONSES_AUTH", "") != "entra":
        return None
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential().get_token("https://ai.azure.com/.default").token


def _url(endpoint: str, suffix: str = "", **query: Any) -> str:
    parts = urllib.parse.urlsplit(endpoint)
    params = dict(urllib.parse.parse_qsl(parts.query))
    params.update({k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in query.items()})
    return urllib.parse.urlunsplit(parts._replace(path=parts.path.rstrip("/") + suffix,
                                                  query=urllib.parse.urlencode(params)))


def _request(url: str, body: dict | None = None, timeout: float = 60):
    headers = {"Accept": "text/event-stream"}
    token = _bearer()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers,
                                                         method="POST" if data else "GET"), timeout=timeout)


def _events(response) -> Iterator[dict[str, Any]]:
    for raw in response:
        line = raw.decode().strip()
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
            yield json.loads(line[5:])


class RunState:
    """The durable client record: enough to reconnect after any crash."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}

    def save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)

    def apply(self, event: dict[str, Any]) -> None:
        """Advance the cursor and the accumulated output, applying the snapshot-reset rule."""
        kind = event.get("type")
        response = event.get("response") or {}
        if response.get("id"):
            self.data["response_id"] = response["id"]
        if kind == "response.in_progress":
            seen = self.data.get("in_progress_seen", 0)
            if seen:
                self.data["output"] = list(response.get("output") or [])
                self.data["snapshot_resets"] = self.data.get("snapshot_resets", 0) + 1
            self.data["in_progress_seen"] = seen + 1
        elif kind == "response.output_item.done":
            item = event.get("item") or {}
            output = [i for i in self.data.setdefault("output", []) if i.get("id") != item.get("id")]
            self.data["output"] = output + [item]
        if kind in TERMINAL_EVENTS:
            self.data["status"] = response.get("status") or kind.split(".", 1)[1]
            self.data["output"] = list(response.get("output") or self.data.get("output") or [])
        if isinstance(event.get("sequence_number"), int):
            self.data["last_sequence"] = event["sequence_number"]
        self.save()


def start(endpoint: str, text: str, state_path: Path, *, model: str = "agent",
          follow_stream: bool = False, on_event: Callable[[dict], None] | None = None) -> RunState:
    state = RunState(state_path)
    state.data = {"endpoint": endpoint, "started_at": time.time()}
    body = {"model": model, "input": text, "store": True, "background": True, "stream": True}
    with _request(_url(endpoint), body) as response:
        state.data["agent_session_id"] = response.headers.get("x-agent-session-id")
        for event in _events(response):
            state.apply(event)
            if on_event:
                on_event(event)
            if state.data.get("response_id") and not follow_stream:
                break  # durable id recorded: background work continues without us
            if event.get("type") in TERMINAL_EVENTS:
                break
    if not state.data.get("response_id"):
        raise RuntimeError("no response id in the stream")
    return state


def _adopt_snapshot(state: RunState) -> None:
    """The stored response is authoritative when the stream ends without a terminal event.

    Observed on a hosted agent (2026-11-21): after a session stop and recovery the
    replay stream ended at the pre-stop events while GET /responses/{id} reported
    the recovered run as completed. Never rely on the stream alone.
    """
    try:
        url = _url(state.data["endpoint"], "/" + state.data["response_id"])
        with _request(url, timeout=60) as response:
            snapshot = json.loads(response.read())
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return
    if snapshot.get("status") in TERMINAL_STATUS:
        state.data["status"] = snapshot["status"]
        state.data["output"] = list(snapshot.get("output") or [])
        state.data["completed_from_snapshot"] = True
        state.save()


def follow(state_path: Path, *, deadline: float = 3600, backoff: float = 2.0,
           on_event: Callable[[dict], None] | None = None) -> RunState:
    """Reconnect from the persisted cursor until a terminal status or the deadline."""
    state = RunState(state_path)
    stop = time.monotonic() + deadline
    while state.data.get("status") not in TERMINAL_STATUS and time.monotonic() < stop:
        url = _url(state.data["endpoint"], "/" + state.data["response_id"], stream=True,
                   starting_after=state.data.get("last_sequence", -1))
        try:
            with _request(url, timeout=min(300, max(1, stop - time.monotonic()))) as response:
                for event in _events(response):
                    state.apply(event)
                    if on_event:
                        on_event(event)
                    if event.get("type") in TERMINAL_EVENTS:
                        break
        except (urllib.error.URLError, OSError, TimeoutError):
            pass  # session waking up or agent restarting; the GET itself wakes a stopped session
        if state.data.get("status") not in TERMINAL_STATUS:
            _adopt_snapshot(state)
        if state.data.get("status") not in TERMINAL_STATUS:
            time.sleep(backoff)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Long-running Responses client (durable id, reconnect)")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("start")
    s.add_argument("--endpoint", required=True)
    s.add_argument("--input", required=True)
    s.add_argument("--state", required=True, type=Path)
    s.add_argument("--follow", action="store_true")
    f = sub.add_parser("follow")
    f.add_argument("--state", required=True, type=Path)
    f.add_argument("--deadline", type=float, default=3600)
    args = parser.parse_args(argv)
    if args.command == "start":
        state = start(args.endpoint, args.input, args.state)
        if args.follow:
            state = follow(args.state)
    else:
        state = follow(args.state, deadline=args.deadline)
    summary = {k: state.data.get(k) for k in ("response_id", "agent_session_id", "status",
                                              "last_sequence", "snapshot_resets")}
    summary["output_items"] = len(state.data.get("output") or [])
    print(json.dumps(summary, indent=2))
    return 0 if state.data.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
