"""Local Task13 fixture inputs; policy, hooks, tools and service are not replaced."""
from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RMA = "RMA-2026-004410"
RISK_RMA = "RMA-2026-004425"


class MemoryCosmos:
    """Task13 SDK protocol fixture. The deployed adapter never uses this store."""
    def __init__(self):
        records = json.loads((ROOT / "specs/sample-data/returns.json").read_text())["records"]
        self.docs = {r["id"]: {**r, "case_id": r["id"], "_etag": "1", "kind": "case"} for r in records}
        self.decisions, self.reads = [], []
        self.queries = 0
        self.before_batch = None

    async def read_item(self, item, partition_key):
        assert partition_key == self.docs[item]["case_id"]
        self.reads.append(item)
        return deepcopy(self.docs[item])

    def query_items(self, **kwargs):
        self.queries += 1
        async def rows():
            for doc in self.docs.values():
                if doc.get("kind") == "case" and doc.get("status") == "in_triage":
                    yield deepcopy(doc)
        return rows()

    async def execute_item_batch(self, batch_operations, partition_key):
        if self.before_batch:
            self.before_batch()
        assert len(batch_operations) == 2
        kind, (key, body), options = batch_operations[0]
        assert kind == "replace"
        if self.docs[key]["_etag"] != options["if_match_etag"]:
            raise RuntimeError("cosmos-precondition-failed")
        kind, (audit,), options = batch_operations[1]
        assert kind == "create" and options == {}
        assert audit["case_id"] == body["case_id"] == partition_key
        assert audit["kind"] == "decision-audit"
        assert audit["action_hash"] and audit["policy_hash"] and audit["principal"]
        if audit["id"] in self.docs:
            raise RuntimeError("cosmos-conflict")
        self.docs[key] = {**deepcopy(body), "_etag": str(int(self.docs[key]["_etag"]) + 1)}
        self.docs[audit["id"]] = deepcopy(audit)
        self.decisions.append(deepcopy(audit))
        return [{"statusCode": 200}, {"statusCode": 201}]


def application(store):
    from governance_application import ReturnsApplication
    from returns_backend import ReturnsBackend
    return ReturnsApplication(ReturnsBackend(container=store, samples=ROOT / "specs/sample-data"))


def sequence(rma=RMA, *, fault="valid", decision="approve_refund"):
    from skills._shared.local_model_fixture import tool_responses
    records = json.loads((ROOT / "specs/sample-data/returns.json").read_text())["records"]
    case = next(c for c in records if c["id"] == rma)
    calls = [("returns_get_case", {"rma_id": rma}),
             ("oms_get_order", {"order_id": case["order_id"]}),
             ("customer_get_profile", {"customer_id": case["customer_id"]})]
    if fault == "missing":
        calls = []
    args = {"rma_id": rma, "decision": decision,
            "disposition": "restock_a" if decision == "approve_refund" else None,
            "citations": ["policy#return-window" if decision == "approve_refund" else
                          "policy#evidence" if decision == "request_more_info" else "policy#escalation"],
            "rationale": "Policy-cited recommendation; never a payment."}
    if fault == "schema":
        args["roles"] = ["Approver"]
    result = [tool_responses(name, values)[0] for name, values in calls]
    result.extend(tool_responses("returns_apply_decision", args))
    for i, response in enumerate(result[:-1]):
        response.messages[0].contents[0].call_id = f"call-{i}"
    return result


def main():
    import subprocess
    import sys
    tools = ROOT / ".governance-tools"
    if not tools.is_dir():
        tools = ROOT.parents[1]
        if ROOT != tools / "examples/returns-triage-governed":
            raise ValueError("materialized_local_tooling_required")
    code = subprocess.run([
        sys.executable, str(tools / "skills/threadlight-governed-actions/scripts/governed_actions.py"),
        "--target", str(ROOT), "--phase", "pre-deploy", "--gate",
    ], check=False).returncode
    if code == 0:
        print("LOCAL selected proof; live bindings unverified")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
