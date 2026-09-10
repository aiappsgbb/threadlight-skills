#!/usr/bin/env python3
"""Read-only default AgentOps evidence assessment; never prints native payloads."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from agentops import AgentOpsValidationError, assess_repository, bounded_command, safe_path
from manifest import atomic_write_json


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, "Invalid AgentOps invocation; use --help for supported options.\n")


class _QuietOutput:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def load_runtime():
    path = Path(__file__).resolve().parents[2] / "threadlight-cicd/scripts/agentops_runtime.py"
    if not path.is_file() or path.is_symlink():
        raise AgentOpsValidationError("approved-runtime-unavailable")
    spec = importlib.util.spec_from_file_location("_threadlight_approved_agentops_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_native(argv, *, cwd, timeout=120, env=None):
    return bounded_command(argv, cwd=cwd, timeout=timeout, max_bytes=1024 * 1024, env=env)


def assess(repo, *, now=None, freshness_hours=24, **kwargs):
    return assess_repository(Path(repo), now=now, freshness_hours=freshness_hours)


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("."))
    parser.add_argument("--emit", nargs="?", const="specs/agentops-manifest.json")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--freshness-hours", type=int, default=24)
    parser.add_argument("--agentops-bin", type=Path)
    parser.add_argument("--refresh-doctor", action="store_true")
    args = parser.parse_args(argv)
    try:
        runtime_code = 0
        if args.refresh_doctor:
            runtime_args = ["--repo", str(args.target), "--refresh-doctor"]
            if args.agentops_bin:
                runtime_args.extend(["--agentops-bin", str(args.agentops_bin)])
            # Runtime owns explicit approval/identity/export prerequisites and
            # bounded private subprocess capture. Keep --json a single document.
            with contextlib.redirect_stdout(_QuietOutput()), contextlib.redirect_stderr(_QuietOutput()):
                try:
                    runtime = load_runtime()
                    runtime_code = runtime.main(runtime_args)
                except SystemExit:
                    raise AgentOpsValidationError("unsupported-runtime-invocation") from None
            if runtime_code not in {0, 2}:
                raise AgentOpsValidationError("approved-runtime-prerequisites-unverified")
        result = assess(args.target, freshness_hours=args.freshness_hours)
        if args.emit:
            if not args.emit.startswith("specs/") or not args.emit.endswith(".json"):
                raise AgentOpsValidationError("unsafe-manifest-destination")
            atomic_write_json(safe_path(args.target, args.emit, exists=False), result)
        if args.json:
            print(json.dumps(result, sort_keys=True, allow_nan=False))
        else:
            print(f"AgentOps: {result['verdict']} ({result['summary']['agents_total']} opted-in agents)")
        return 2 if runtime_code == 2 or args.gate and result["verdict"] == "blocked" else 0
    except (ValueError, OSError, ImportError):
        print("AgentOps assessment could not verify the requested evidence or execution prerequisites.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
