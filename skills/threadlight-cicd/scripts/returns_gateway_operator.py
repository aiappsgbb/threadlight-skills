#!/usr/bin/env python3
"""Compose the real govern_control_plane.operator admission wire; no business redispatch."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import uuid

import release_runner


def operate(operation, *, requester, invoke, valid_until=None):
    if operation not in {"inspect", "stop", "open"}:
        raise ValueError("unsupported admission operation")
    if operation == "open":
        until = release_runner._gate()._parse_rfc3339_datetime(valid_until)
        now = datetime.now(timezone.utc)
        if until is None or not now < until <= now + timedelta(seconds=300):
            raise ValueError("explicit current admission lease of at most 300 seconds required")
    inspect = {"operation": "inspect", "action": "returns_apply_decision", "requester": requester}
    current = invoke(inspect)
    if current.get("retry_authorized") is not False or current.get("state") not in {"missing", "open", "stopped"}:
        raise ValueError("unconfirmed native admission observation")
    if operation == "inspect":
        return current
    state = "open" if operation == "open" else "stopped"
    request = {**inspect, "operation": "admission", "state": state,
               "valid_until": valid_until if state == "open" else None,
               "reason_code": "release-admit" if state == "open" else "release-stop",
               "expected_record_hash": current.get("record_hash")}
    changed = invoke(request)
    if (changed.get("retry_authorized") is not False or changed.get("state") != state
            or not changed.get("record_hash")):
        raise ValueError("admission outcome unknown; inspect before any retry")
    observed = invoke(inspect)
    if (observed.get("retry_authorized") is not False or observed.get("state") != state
            or observed.get("record_hash") != changed["record_hash"]):
        raise ValueError("admission readback changed; no retry or closure claim")
    return observed


def authorize_open(root, requester):
    request = release_runner.load(Path(os.environ["THREADLIGHT_RELEASE_REQUEST"]))
    plan = release_runner.policy(root, request["policy_path"])
    observed = request.get("production_observation", {})
    binding = observed.get("application_contract", {}).get("binding", {})
    if (not isinstance(request.get("postcheck_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", request["postcheck_sha256"]) or binding.get("principal") != requester
            or request.get("target") != plan["production"]):
        raise ValueError("opening requires the protected postchecked production release request")
    release_runner.verify_identity(plan, "production")
    if (request.get("ci") != release_runner.ci_context(root)
            or request.get("policy_sha256") != release_runner.digest(plan)
            or request.get("inputs") != release_runner.inputs(root, plan, request["policy_path"])):
        raise ValueError("release authorization changed before admission")
    started = release_runner._gate()._parse_rfc3339_datetime(request.get("started_at"))
    if started is None:
        raise ValueError("current release window required")
    release_runner._gate()._release_stamp(request.get("completed_at"), datetime.now(timezone.utc),
                                         started, plan["max_age_seconds"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "stop", "open"))
    parser.add_argument("--requester", required=True, help="Observed registered agent principal, never the operator")
    for name in ("gateway-url", "scope", "tenant", "subscription", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--credential", choices=("azure-cli", "managed-identity"), required=True)
    parser.add_argument("--client-id")
    parser.add_argument("--valid-until")
    args = parser.parse_args(argv)
    try:
        from govern_control_plane.operator import main as native
        root = Path.cwd()
        output = release_runner.local_path(root, args.output)
        if output.exists():
            raise ValueError("operator output must be new")
        directory = root / ".threadlight-release-private" / ("gateway-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o700, parents=True)
        sequence = 0

        def invoke(request):
            nonlocal sequence
            if request.get("state") == "open":
                authorize_open(root, args.requester)
            sequence += 1
            source, result = directory / f"{sequence}-request.json", directory / f"{sequence}-response.json"
            source.write_text(json.dumps(request), encoding="utf-8")
            command = ["--request", str(source), "--output", str(result)]
            for name in ("gateway_url", "scope", "tenant", "subscription", "credential", "client_id"):
                value = getattr(args, name)
                if value is not None:
                    command += ["--" + name.replace("_", "-"), value]
            with (directory / f"{sequence}.log").open("x") as log, redirect_stdout(log):
                if native(command):
                    raise ValueError("native operator did not acknowledge; preserve evidence and inspect")
            return release_runner.load(result)

        result = operate(args.operation, requester=args.requester, invoke=invoke, valid_until=args.valid_until)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x") as stream:
            json.dump(result, stream, allow_nan=False)
            stream.write("\n")
        print(json.dumps(result, allow_nan=False))
        return 0
    except (ImportError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"Gateway admission unconfirmed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
