#!/usr/bin/env python3
"""Offline crash-recovery harness for Responses-protocol hosted agents.

Runs the agent locally with an isolated ``AGENTSERVER_STATE_ROOT`` (the
AgentServer SDK then uses file-backed task, response and stream stores, the same
recovery code path as a hosted agent), posts a stored background streaming
request, SIGKILLs the process right after the first checkpoint, restarts it and
waits for a terminal status. It then asserts:

* the recovered entry has ``is_recovery`` and resumes at the first
  un-checkpointed phase;
* checkpointed phases are not executed again (per-phase completion counter);
* a full SSE replay has exactly one ``response.created``, a second
  ``response.in_progress`` (the snapshot reset) and one ``response.completed``.

The agent module must expose ``PHASES``, ``run_phase`` and ``main()`` and print
``threadlight.long_running {json}`` lifecycle lines (the long-running template in
threadlight-deploy/references/hosted-agent/maf does). ``--offline-phases``
replaces ``run_phase`` with deterministic timed phases, so no model or Azure
resource is used.

The output is LOCAL evidence. It never substitutes for a hosted crash test.

Usage::

    python crash_recovery.py --agent src/agent/container.py --offline-phases [--output evidence.json]
    python crash_recovery.py --agent <background-only agent> --offline-phases --expect no-recovery

Exit 0 when the observed behaviour matches ``--expect`` (default ``recovery``),
1 otherwise, 2 on a harness error.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PREFIX = "threadlight.long_running "
TERMINAL = {"completed", "failed", "cancelled", "incomplete"}
CLIENT = (Path(__file__).resolve().parents[2]
          / "threadlight-deploy/references/hosted-agent/maf/references/python/long_running_client.py")
LOCAL_LABEL = "local evidence (file-backed AgentServer stores), not hosted crash-test evidence"
CONTROL_LABEL = "background only, not crash-resilient: " + LOCAL_LABEL


class HarnessError(RuntimeError):
    pass


def _serve(agent: Path, offline: bool, phase_seconds: float) -> None:
    """Child process: load the agent module, optionally swap in offline phases, serve."""
    spec = importlib.util.spec_from_file_location("container", agent)
    module = importlib.util.module_from_spec(spec)
    sys.modules["container"] = module
    sys.path.insert(0, str(agent.parent))
    spec.loader.exec_module(module)
    if offline:
        async def run_phase(phase, instruction, input_text, prior):
            await asyncio.sleep(phase_seconds)
            return f"{phase}: offline result {len(prior) + 1} for {input_text!r}"
        module.run_phase = run_phase
    module.main()


class Agent:
    def __init__(self, agent: Path, workdir: Path, offline: bool, phase_seconds: float):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log = workdir / "agent.log"
        self.env = {**os.environ, "AGENTSERVER_STATE_ROOT": str(workdir / "agentserver-state"),
                    "PORT": str(self.port), "PYTHONUNBUFFERED": "1", "OTEL_SDK_DISABLED": "true"}
        for name in ("FOUNDRY_HOSTING_ENVIRONMENT", "FOUNDRY_PROJECT_ENDPOINT"):
            self.env.pop(name, None)
        self.command = [sys.executable, str(Path(__file__).resolve()), "--serve", str(agent),
                        "--phase-seconds", str(phase_seconds)] + (["--offline-phases"] if offline else [])
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        with self.log.open("a") as out:
            self.process = subprocess.Popen(self.command, env=self.env, stdout=out, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise HarnessError(f"agent exited at startup; see {self.log}")
            try:
                with urllib.request.urlopen(self.base + "/readiness", timeout=2):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        raise HarnessError("agent did not become ready in 30 s")

    def kill(self) -> None:
        os.kill(self.process.pid, signal.SIGKILL)
        self.process.wait()

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def lifecycle(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line[len(PREFIX):]) for line in self.log.read_text().splitlines()
                if line.startswith(PREFIX)]

    def get(self, path: str, timeout: float = 10) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=timeout) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()


def _client():
    spec = importlib.util.spec_from_file_location("long_running_client", CLIENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wait(condition, seconds: float, interval: float = 0.05):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(interval)
    return None


def run(agent_path: Path, *, offline: bool, phase_seconds: float, expect: str,
        recovery_wait: float, no_recovery_wait: float) -> dict[str, Any]:
    started = time.monotonic()
    workdir = Path(tempfile.mkdtemp(prefix="threadlight-crash-"))
    agent = Agent(agent_path, workdir, offline, phase_seconds)
    evidence: dict[str, Any] = {
        "evidence": "local", "label": CONTROL_LABEL if expect == "no-recovery" else LOCAL_LABEL,
        "agent": agent_path.name, "offline_phases": offline, "expect": expect,
    }
    try:
        client = _client()
        state_path = workdir / "client-state.json"
        agent.start()
        # The generated client: store + background + stream, durable id and cursor.
        response_id = client.start(agent.base + "/responses", "crash-recovery harness request",
                                   state_path).data["response_id"]
        evidence["response_id"] = response_id
        checkpoint = _wait(lambda: next((e for e in agent.lifecycle() if e["event"] == "checkpointed"), None),
                           60)
        if checkpoint is None:
            raise HarnessError("no checkpoint observed within 60 s; the agent emits no lifecycle lines")
        agent.kill()
        evidence["killed_after_checkpoint"] = checkpoint["phase"]
        agent.start()

        restarted = time.monotonic()

        def terminal():
            status, body = agent.get(f"/responses/{response_id}")
            snapshot = json.loads(body) if status == 200 else {}
            if snapshot.get("status") in TERMINAL:
                return snapshot
            if time.monotonic() - restarted > no_recovery_wait and not any(
                    e["event"] == "entry" and e.get("is_recovery") for e in agent.lifecycle()):
                return {"status": snapshot.get("status") or f"http_{status}", "output": snapshot.get("output")}
            return None

        snapshot = _wait(terminal, max(recovery_wait, no_recovery_wait), 0.2)
        if snapshot is None:
            status, body = agent.get(f"/responses/{response_id}")
            snapshot = json.loads(body) if status == 200 else {"status": f"http_{status}"}
        events = agent.lifecycle()
        evidence["entries"] = [{k: e.get(k) for k in ("is_recovery", "start_phase")}
                               for e in events if e["event"] == "entry"]
        def count(kind):
            counts: dict[str, int] = {}
            for event in events:
                if event["event"] == kind:
                    counts[str(event["phase"])] = counts.get(str(event["phase"]), 0) + 1
            return counts
        completions = count("phase_done")
        evidence["phase_completions"] = completions
        evidence["phase_starts"] = count("phase_start")  # the interrupted phase starts twice
        evidence["final_status"] = snapshot.get("status")
        evidence["output_items"] = len(snapshot.get("output") or [])

        status, body = agent.get(f"/responses/{response_id}?stream=true")
        types = [json.loads(line[5:]).get("type") for line in body.splitlines() if line.startswith("data:")]
        evidence["replay"] = {"http_status": status, **{name: types.count(name) for name in (
            "response.created", "response.in_progress", "response.completed", "response.failed")}}

        recovered = (evidence["final_status"] == "completed"
                     and any(entry["is_recovery"] for entry in evidence["entries"]))
        failures = []
        if recovered:
            killed = evidence["killed_after_checkpoint"]
            resumed = next(e for e in evidence["entries"] if e["is_recovery"])
            if resumed["start_phase"] != killed + 1:
                failures.append(f"resumed at phase {resumed['start_phase']}, expected {killed + 1}")
            reruns = {phase: count for phase, count in completions.items()
                      if int(phase) <= killed and count != 1}
            if reruns:
                failures.append(f"checkpointed phases executed again: {reruns}")
            followed = client.follow(state_path, deadline=30, backoff=0.5).data
            items = [item.get("id") for item in followed.get("output") or []]
            evidence["client"] = {"status": followed.get("status"), "output_items": len(items),
                                  "unique_item_ids": len(set(items)),
                                  "snapshot_resets": followed.get("snapshot_resets", 0)}
            if followed.get("status") != "completed" or len(items) != len(set(items)) \
                    or len(items) != evidence["output_items"]:
                failures.append(f"client reconnect did not converge on the snapshot: {evidence['client']}")
            replay = evidence["replay"]
            if (replay["response.created"], replay["response.completed"]) != (1, 1) \
                    or replay["response.in_progress"] != 2:
                failures.append(f"replay is not one created / reset in_progress / one completed: {replay}")
        evidence["verdict"] = ("recovered" if recovered and not failures
                               else "recovered_with_defects" if recovered else "not_recovered")
        evidence["failures"] = failures
        evidence["passed"] = evidence["verdict"] == ("recovered" if expect == "recovery" else "not_recovered")
    finally:
        agent.stop()
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 1)
        evidence["agent_log"] = str(agent.log)
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--agent", type=Path, help="agent module (e.g. src/agent/container.py)")
    parser.add_argument("--offline-phases", action="store_true", help="deterministic timed phases, no model")
    parser.add_argument("--phase-seconds", type=float, default=1.0)
    parser.add_argument("--expect", choices=("recovery", "no-recovery"), default="recovery")
    parser.add_argument("--recovery-wait", type=float, default=90.0, help="seconds to wait for a terminal status")
    parser.add_argument("--no-recovery-wait", type=float, default=30.0,
                        help="seconds to watch a non-resilient response before calling it not recovered")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.serve:
        _serve(args.serve.resolve(), args.offline_phases, args.phase_seconds)
        return 0
    if args.agent is None:
        parser.error("--agent is required")
    try:
        evidence = run(args.agent.resolve(), offline=args.offline_phases, phase_seconds=args.phase_seconds,
                       expect=args.expect, recovery_wait=args.recovery_wait,
                       no_recovery_wait=args.no_recovery_wait)
    except (HarnessError, OSError, urllib.error.URLError) as exc:
        evidence = {"evidence": "local", "label": LOCAL_LABEL, "verdict": "harness_error",
                    "error": str(exc), "passed": False}
        code = 2
    else:
        code = 0 if evidence["passed"] else 1
    text = json.dumps(evidence, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
