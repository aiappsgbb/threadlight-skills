#!/usr/bin/env python3
"""Warm the governed-write services before a demo and scale them back to zero after.

``warm``: min replicas 1 on the control plane, gateway, business API and pilot MCP
server, then waits for each public readiness signal (business ``/health``, gateway
per-binding ``/health``). ``cool``: min replicas 0 on the same apps, so nothing is
left always on between workshops (the first request after cooling pays a cold start).

Why: a scale-to-zero business API took ~26 s to start in the first live run; the
gateway's bounded downstream call timed out and correctly reported an UNKNOWN
outcome although the business write then committed once. The hosted agent's first
invocation after idling also returned HTTP 500 once. Warm before showing it
(``--project-endpoint`` sends a tool-free ping to the agent and retries).
Uses the operator's Azure CLI login; changes only replica settings.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request


def az(*args):
    return subprocess.run(["az", *args, "--only-show-errors"], check=True, capture_output=True, text=True).stdout


def ready(url, check):
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.status == 200 and check(json.loads(response.read()))
    except Exception:
        return False


def ping_agent(endpoint, agent, attempts=4):
    """One tool-free invocation; it asks for no tool call and records nothing."""
    token = json.loads(az("account", "get-access-token", "--scope", "https://ai.azure.com/.default",
                          "-o", "json"))["accessToken"]
    url = f"{endpoint.rstrip('/')}/agents/{agent}/endpoint/protocols/invocations?api-version=v1"
    body = json.dumps({"input": "Reply only with the word READY. Do not call any tool."}).encode()
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                if response.status == 200 and b"event: done" in response.read():
                    print(f"ready: agent {agent} (attempt {attempt})")
                    return
        except Exception as error:
            print(f"agent ping attempt {attempt}: {type(error).__name__}")
        time.sleep(30)
    raise SystemExit(f"agent {agent} not ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("warm", "cool"))
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--apps", nargs="+", required=True,
                        help="container app names: control, gateway, business, pilot MCP")
    parser.add_argument("--gateway-url", help="https://<gateway>/mcp (warm: wait for binding health)")
    parser.add_argument("--business-url", help="https://<business> (warm: wait for /health)")
    parser.add_argument("--project-endpoint", help="Foundry project endpoint (warm: ping the hosted agent)")
    parser.add_argument("--agent", default="returns-governed")
    args = parser.parse_args()
    replicas = "1" if args.action == "warm" else "0"
    for app in args.apps:
        az("containerapp", "update", "-n", app, "-g", args.resource_group, "--min-replicas", replicas, "-o", "none")
        print(f"{app}: min replicas {replicas}")
    if args.action == "cool":
        return
    checks = []
    if args.business_url:
        checks.append((args.business_url.rstrip("/") + "/health", lambda body: body.get("status") == "ready"))
    if args.gateway_url:
        checks.append((args.gateway_url.rsplit("/", 1)[0] + "/health",
                       lambda body: body.get("status") == "ready"
                       and all(b["healthy"] for b in body.get("bindings", {}).values())))
    deadline = time.time() + 600
    for url, check in checks:
        while not ready(url, check):
            if time.time() > deadline:
                raise SystemExit(f"not ready: {url}")
            time.sleep(10)
        print(f"ready: {url}")
    if args.project_endpoint:
        ping_agent(args.project_endpoint, args.agent)


if __name__ == "__main__":
    main()
