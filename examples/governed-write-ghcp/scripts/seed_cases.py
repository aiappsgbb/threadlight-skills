#!/usr/bin/env python3
"""Seed the synthetic governed-write cases CREATE-ONLY and print their observed revisions.

Runs as the operator (Azure CLI login) holding only the example's create/read
Cosmos role. Never replaces or deletes: an existing case is reported, not reset.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1]


def main():
    from azure.cosmos import CosmosClient
    from azure.cosmos.exceptions import CosmosResourceExistsError
    from azure.identity import AzureCliCredential

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cosmos-url", required=True)
    parser.add_argument("--database", default="returns")
    parser.add_argument("--container", default="returns-cases")
    parser.add_argument("--outside-snapshot", metavar="CASE_ID",
                        help="also create ONE ordinary eligible case that is NOT in the signed policy "
                             "snapshot (deny demo: the policy refuses to decide it)")
    args = parser.parse_args()
    container = CosmosClient(args.cosmos_url, credential=AzureCliCredential()).get_database_client(
        args.database).get_container_client(args.container)
    cases = json.loads((EXAMPLE / "governance/cases.json").read_text())["cases"]
    if args.outside_snapshot:
        if any(case["case_id"] == args.outside_snapshot for case in cases):
            parser.error("--outside-snapshot must not be a case in governance/cases.json")
        cases = cases + [{"case_id": args.outside_snapshot, "customer_id": "CUST-" + args.outside_snapshot,
                          "amount": 60, "currency": "EUR", "eligible": True, "high_risk": False,
                          "reason_code": "wrong_size"}]
    observed = []
    for case in cases:
        document = {"id": case["case_id"], "kind": "case", "status": "in_triage", **case}
        try:
            container.create_item(document)
            state = "created"
        except CosmosResourceExistsError:
            state = "existing-not-reset"
        record = container.read_item(case["case_id"], partition_key=case["case_id"])
        observed.append({"case_id": case["case_id"], "state": state, "status": record["status"],
                         "_etag": record["_etag"]})
    print(json.dumps(observed, indent=2))


if __name__ == "__main__":
    main()
