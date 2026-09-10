#!/usr/bin/env python3
"""Install an already signed observed-run record; no keys, signatures or runs are created."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from agentops import AgentOpsValidationError, discover_opted_in_agents, read_bytes, safe_path, verify_receipt


def bundle(repo, *, agent_root, record, signature, now=None, freshness_hours=24):
    repo = Path(repo).resolve()
    roots = {item["root"] for item in discover_opted_in_agents(repo)}
    if agent_root not in roots:
        raise AgentOpsValidationError("unselected-agent-root")
    root = repo if agent_root == "." else safe_path(repo, agent_root, exists=False)
    now = now or datetime.now(timezone.utc)
    verify_receipt(repo, root, now=now, hours=freshness_hours,
                   receipt_path=record, signature_path=signature)
    destinations = [safe_path(root, ".agentops/threadlight/receipt.json", exists=False),
                    safe_path(root, ".agentops/threadlight/receipt.sig", exists=False)]
    if any(path.exists() for path in destinations):
        raise AgentOpsValidationError("archive-existing-receipt-first")
    payloads = [read_bytes(root, record, limit=65536), read_bytes(root, signature, limit=8192)]
    created = []
    try:
        for path, payload in zip(destinations, payloads):
            path.parent.mkdir(parents=True, exist_ok=True)
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                created.append(path)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        verify_receipt(repo, root, now=now, hours=freshness_hours)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("."))
    parser.add_argument("--agent-root", required=True, help="Repository-relative opted-in root")
    parser.add_argument("--record", required=True, help="Agent-root-relative signed observed-run JSON")
    parser.add_argument("--signature", required=True, help="Agent-root-relative detached signature")
    args = parser.parse_args(argv)
    try:
        bundle(args.target, agent_root=args.agent_root, record=args.record, signature=args.signature)
        print("Verified observed-run receipt installed; no native operation executed.")
        return 0
    except (AgentOpsValidationError, OSError):
        print("Receipt not installed: verify the existing owner signature, scope, hashes and archive destination.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
