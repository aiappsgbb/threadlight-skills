#!/usr/bin/env python3
"""Build the operator's queue page from real gateway ``pending_approval`` records.

Copies the runnable ``case-list`` workspace shape and writes ``data/cases.json``
with one row per pending record (saved by ``invoke.py --pending``), plus a copy
of each exact record beside the page. The page itself records nothing.

The approver never uses a terminal. Choose how the pending card reads:

* ``--approval-email <address>``: "Approval email sent to <address>" (native
  Outlook channel; the approver clicks Approve in the email).
* ``--panel``: real Approve / Reject buttons served by ``approval_panel.py``,
  which signs the approver in and records the one-use decision as them.
* neither: the ``threadlight-review-action`` command, an operator fallback
  (requires the ``--control-plane-url/--scope/--tenant/--client-id`` values).

Keep the output folder outside any repository.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

EXAMPLE = Path(__file__).resolve().parents[1]
SHAPE = EXAMPLE.parents[1] / "skills/threadlight-workspace-ui/references/shapes/case-list"


def case(pending, file_name):
    if pending.get("status") != "pending_approval" or not pending.get("operation_id"):
        raise ValueError("not_a_pending_approval_record")
    args = pending["proposed_arguments"]
    expires = (pending.get("approval_intent") or {}).get("expires_at")
    return {
        "id": args["case_id"], "customer": "Synthetic case", "item": args["decision"],
        "status": "pending_approval", "amount": None, "decision": args["decision"], "risk": "high",
        "owner": "Approver", "opened_at": expires, "sla_due": expires, "etag": args["expected_etag"],
        "pending": pending, "pending_file": file_name,
        "recommendation": None,
        "reasoning": [], "audit": [],
    }


def build(pending_files, out, *, review=None, approval=None):
    out = Path(out)
    if out.exists():
        raise FileExistsError("refusing_to_overwrite_review_queue")
    records = [(Path(p), json.loads(Path(p).read_text())) for p in pending_files]
    cases = [case(record, f"pending-{index}.json") for index, (_, record) in enumerate(records, 1)]
    shutil.copytree(SHAPE, out, ignore=shutil.ignore_patterns("README.md", "data"))
    (out / "data").mkdir()
    for index, (_, record) in enumerate(records, 1):
        (out / f"pending-{index}.json").write_text(json.dumps(record, indent=2) + "\n")
    doc = {
        "schema": "threadlight-workspace-cases/v1",
        "workspace": {"process_name": "Governed returns", "agent_name": "returns-governed",
                      "accent": "#0d7c66", "currency": "EUR"},
        "config": {"writeback_url": None, "actionable_statuses": []},
        "actions": [], "cases": cases,
    }
    if approval is not None:
        if approval.get("mode") not in ("email", "panel"):
            raise ValueError("approval_mode_invalid")
        doc["config"]["approval"] = dict(approval)
    elif review is not None:
        doc["config"]["review_command"] = {**review, "role": review.get("role", "Approver")}
    else:
        raise ValueError("approval_mode_or_review_command_required")
    (out / "data/cases.json").write_text(json.dumps(doc, indent=2) + "\n")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pending", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--approval-email", help="approver mailbox of the native Outlook channel")
    mode.add_argument("--panel", action="store_true", help="serve with approval_panel.py")
    parser.add_argument("--control-plane-url")
    parser.add_argument("--scope", help="api://<control-api-id>/Governance.Approve")
    parser.add_argument("--tenant")
    parser.add_argument("--client-id", help="public client pre-authorized for Governance.Approve")
    args = parser.parse_args()
    if args.approval_email:
        out = build(args.pending, args.out, approval={"mode": "email", "recipient": args.approval_email})
    elif args.panel:
        out = build(args.pending, args.out, approval={"mode": "panel"})
    else:
        if not all((args.control_plane_url, args.scope, args.tenant, args.client_id)):
            parser.error("the operator fallback needs --control-plane-url, --scope, --tenant and --client-id")
        out = build(args.pending, args.out, review={
            "control_plane_url": args.control_plane_url, "scope": args.scope,
            "tenant": args.tenant, "client_id": args.client_id})
    print(f"queue written to {out}")


if __name__ == "__main__":
    main()
