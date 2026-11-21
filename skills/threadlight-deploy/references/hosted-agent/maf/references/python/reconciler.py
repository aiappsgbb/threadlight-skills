# Canonical pilot reconciler for long-running background responses.
#
# Source of truth for `../../README.md § Long-running mode (opt-in,
# crash-resilient)` → Reconciler. Pair it with long_running.py and
# long_running_client.py; standard library only.
#
# Why: a background response can stay non-terminal in two observed ways.
#   1. After a session stop or sandbox replacement, recovery runs only when a
#      request reaches that session. If nobody calls, the job sits idle.
#   2. Without `resilient_background=True`, a crashed background response is
#      never terminated and stays `in_progress` (a ghost).
# A pilot UI or orchestrator that just waits hangs in both cases.
#
# What it does, and only this (it OBSERVES and WAKES, never replays):
#   - tracks every started job: response_id, agent_session_id, start, deadline;
#   - polls GET {endpoint}/{response_id} with backoff for non-terminal jobs. The
#     GET is also what wakes a stopped hosted session so its recovery can run;
#   - when a job's deadline passes it marks the job `deadline_exceeded` in the
#     application, raises an alert and records the business effect as UNKNOWN
#     (reconcile it against the system of record; never re-run it);
#   - exposes `ui_state`: running, recovering (the job came back after the agent
#     was unreachable), completed / failed / cancelled / incomplete, or
#     deadline_exceeded.
# It never POSTs, cancels, retries or re-creates a response.
#
# Usage:
#   python reconciler.py track --jobs jobs.json --state run.json --deadline 3600
#   python reconciler.py run   --jobs jobs.json [--interval 15] [--max-seconds 86400]
#   python reconciler.py status --jobs jobs.json

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any, Callable

TERMINAL = {"completed", "failed", "cancelled", "incomplete"}


def _client():
    path = Path(__file__).with_name("long_running_client.py")
    spec = importlib.util.spec_from_file_location("long_running_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {"jobs": {}}


def _save(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


def track(jobs: Path, *, endpoint: str, response_id: str, deadline_seconds: float,
          agent_session_id: str | None = None, now: float | None = None) -> dict[str, Any]:
    """Record a started job. Call it as soon as the client has the response id."""
    now = time.time() if now is None else now
    data = _load(jobs)
    job = data["jobs"].setdefault(response_id, {
        "response_id": response_id, "endpoint": endpoint, "agent_session_id": agent_session_id,
        "started_at": now, "deadline_at": now + deadline_seconds, "state": "running",
        "ui_state": "running", "final": False, "polls": 0, "outage_seen": False,
        "recovering_seen": False, "effects": "not-touched", "output_items": 0,
    })
    _save(jobs, data)
    return job


def _observe(job: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    client = _client()
    try:
        with client._request(client._url(job["endpoint"], "/" + job["response_id"]), timeout=30) as response:
            snapshot = json.loads(response.read())
        return snapshot.get("status"), len(snapshot.get("output") or []), None
    except urllib.error.HTTPError as error:
        return None, None, f"http_{error.code}"
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
        return None, None, type(error).__name__


def reconcile_once(jobs: Path, *, now: float | None = None,
                   alert: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
    """One observation pass over every non-final job."""
    data = _load(jobs)
    for job in data["jobs"].values():
        if job["final"]:
            continue
        status, items, error = _observe(job)
        current = time.time() if now is None else now
        job["polls"] += 1
        job["last_polled_at"] = current
        job["last_error"] = error
        if items is not None:
            job["output_items"] = items
        if status in TERMINAL:
            job.update(state=status, ui_state=status, final=True)
        elif current >= job["deadline_at"]:
            # The application gives up waiting. The agent-side run is untouched and
            # whatever effect it reached is UNKNOWN until reconciled.
            job.update(state="deadline_exceeded", ui_state="deadline_exceeded", final=True,
                       effects="UNKNOWN", last_status=status)
            if alert:
                alert(dict(job))
        elif error is not None:
            job.update(state="unreachable", outage_seen=True)
        else:
            recovering = job["outage_seen"]
            job.update(state=status or "unknown", last_status=status,
                       ui_state="recovering" if recovering else "running")
            job["recovering_seen"] = job["recovering_seen"] or recovering
    _save(jobs, data)
    return list(data["jobs"].values())


def run(jobs: Path, *, interval: float = 15.0, max_interval: float = 120.0, max_seconds: float = 86400,
        alert: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
    """Poll with backoff until every job is final or max_seconds elapse."""
    stop = time.monotonic() + max_seconds
    wait = interval
    while True:
        result = reconcile_once(jobs, alert=alert)
        if all(job["final"] for job in result) or time.monotonic() >= stop:
            return result
        time.sleep(min(wait, max(0.0, stop - time.monotonic())))
        wait = min(max_interval, wait * 1.5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Observe-and-wake reconciler for background responses")
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("track")
    t.add_argument("--jobs", required=True, type=Path)
    t.add_argument("--state", required=True, type=Path, help="long_running_client state file")
    t.add_argument("--deadline", required=True, type=float, help="seconds from now")
    r = sub.add_parser("run")
    r.add_argument("--jobs", required=True, type=Path)
    r.add_argument("--interval", type=float, default=15.0)
    r.add_argument("--max-seconds", type=float, default=86400)
    s = sub.add_parser("status")
    s.add_argument("--jobs", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "track":
        state = json.loads(args.state.read_text())
        job = track(args.jobs, endpoint=state["endpoint"], response_id=state["response_id"],
                    agent_session_id=state.get("agent_session_id"), deadline_seconds=args.deadline)
        print(json.dumps(job, indent=2))
        return 0
    if args.command == "run":
        def alert(job):
            print("ALERT " + json.dumps({k: job[k] for k in ("response_id", "state", "effects")}),
                  file=sys.stderr, flush=True)
        result = run(args.jobs, interval=args.interval, max_seconds=args.max_seconds, alert=alert)
    else:
        result = list(_load(args.jobs)["jobs"].values())
    print(json.dumps(result, indent=2))
    return 0 if all(job["state"] == "completed" for job in result) else 1


if __name__ == "__main__":
    sys.exit(main())
