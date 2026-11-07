"""returns-mcp: mock MCP server written by threadlight-design (synthetic fixture).

Safeguards: customer field allow-list (masked email only), decision validation
and a required citation. FastMCP is imported lazily so the tool functions stay
importable without it.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
DECISIONS = {"approve_refund", "deny_refund", "escalate_to_supervisor"}
CUSTOMER_FIELDS = ("id", "name", "email_masked", "loyalty_tier", "account_status")


def _data_dir() -> Path:
    env = os.environ.get("SAMPLE_DATA_DIR")
    return Path(env) if env else HERE.parents[1] / "specs" / "sample-data"


def _load(name: str) -> dict[str, dict]:
    payload = json.loads((_data_dir() / f"{name}.json").read_text(encoding="utf-8"))
    return {r["id"]: r for r in payload["records"]}


RETURNS = _load("returns")
CUSTOMERS = _load("customers")


def returns_get_case(rma_id: str) -> dict:
    """Fetch one return case by RMA id."""
    case = RETURNS.get(rma_id)
    return copy.deepcopy(case) if case else {"error": "not_found", "message": f"RMA {rma_id} not found"}


def returns_list_open(offset: int = 0, limit: int = 20) -> dict:
    """List open cases (paged)."""
    open_cases = [r for r in RETURNS.values() if r["status"] == "in_triage"]
    page = open_cases[max(0, offset):max(0, offset) + max(1, min(limit, 100))]
    return {"total": len(open_cases), "items": [{"rma_id": r["id"]} for r in page]}


def customer_get_profile(customer_id: str) -> dict:
    """Fetch the customer profile (email masked)."""
    cust = CUSTOMERS.get(customer_id)
    if cust is None:
        return {"error": "not_found", "message": f"customer {customer_id} not found"}
    return {k: cust[k] for k in CUSTOMER_FIELDS}


def returns_record_recommendation(rma_id: str, decision: str, citations: list[str], rationale: str) -> dict:
    """Record the recommendation on the case. Never settles a refund."""
    if rma_id not in RETURNS:
        return {"error": "not_found", "message": f"RMA {rma_id} not found"}
    if decision not in DECISIONS:
        return {"error": "invalid_decision", "message": f"decision must be one of {sorted(DECISIONS)}"}
    if not citations:
        return {"error": "missing_citation", "message": "at least one policy clause id is required"}
    RETURNS[rma_id]["recommendation"] = {"decision": decision, "citations": sorted(set(citations)),
                                         "rationale": rationale[:600]}
    return {"ok": True, "record_id": f"REC-{rma_id}"}


TOOLS = [returns_get_case, returns_list_open, customer_get_profile, returns_record_recommendation]


def build_server():
    from fastmcp import FastMCP

    mcp = FastMCP("returns-mcp")
    for fn in TOOLS:
        mcp.tool(fn)
    return mcp


if __name__ == "__main__":
    build_server().run(transport="streamable-http", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
