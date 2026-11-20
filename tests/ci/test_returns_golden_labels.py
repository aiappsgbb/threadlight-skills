"""Golden labels of the governed returns example must follow its own rule precedence.

The instructions, fraud-escalation skill and Rego policy all say BR-003 (value
ceiling / serial-returner / flagged account) overrides BR-001 and BR-004. A golden
label that contradicts that order scores correct agent answers as wrong (#233).
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/returns-triage-governed"
DATA = EXAMPLE / "specs/sample-data"


def _records(name):
    return {r["id"]: r for r in json.loads((DATA / name).read_text())["records"]}


def _dataset():
    lines = (EXAMPLE / "tests/eval_dataset.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _br003_fires(case, customer):
    ceiling = json.loads((DATA / "returns.json").read_text())["_meta"]["auto_approve_ceiling_usd"]
    return (case["refund_amount"] > ceiling
            or customer["lifetime_return_rate"] >= 0.40
            or customer["account_status"] == "review_flagged")


def test_golden_labels_respect_value_ceiling_precedence():
    returns, customers = _records("returns.json"), _records("customers.json")
    checked = 0
    for row in _dataset():
        rma = re.search(r"RMA-\d{4}-\d{6}", row["input"])
        if not rma or rma.group(0) not in returns:
            continue
        case = returns[rma.group(0)]
        if _br003_fires(case, customers[case["customer_id"]]):
            assert row["expected"]["decision"] == "escalate_to_supervisor", (row["id"], case["id"])
        checked += 1
    assert checked >= 5


def test_every_decision_branch_keeps_a_golden_case():
    decisions = {row["expected"]["decision"] for row in _dataset()}
    assert {"approve_refund", "deny_refund", "escalate_to_supervisor", "request_more_info"} <= decisions


def test_needinfo_case_order_matches_refund():
    returns, orders = _records("returns.json"), _records("orders.json")
    case = returns["RMA-2026-004440"]
    order = orders[case["order_id"]]
    assert case["photos_provided"] is False and case["reason_code"] == "arrived_damaged"
    assert order["order_total"] == case["refund_amount"]
    assert sum(line["unit_price"] * line["qty"] for line in order["items"]) == case["refund_amount"]
