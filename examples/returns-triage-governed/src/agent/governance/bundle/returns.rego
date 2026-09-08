package returns
import rego.v1

default pre_tool_verdict := {"decision": "deny", "reason": "return-context-required"}

# Only the opt-in Task11 producer exposes this reserved tool, after authenticated
# registration. Its positive control never proves the business-write binding.
pre_tool_verdict := {"decision": "allow", "reason": "reserved-noop-only"} if {
    input.tool.name == "governance_probe_noop"
    input.policy_target.value.variant == "allow"
}

t := input.snapshot.trusted
f := t.facts
a := input.policy_target.value

scope if {
    input.tool.name == "returns_apply_decision"
    a.rma_id in f.allowed_cases
    a.rma_id == f.case.id
    f.case.case_id == f.case.id
    f.case.kind == "case"
    is_string(f.case._etag)
    count(f.case._etag) > 0
    f.case.status in {"in_triage", "closed", "escalated"}
    is_number(f.case.refund_amount)
    is_boolean(f.case.photos_provided)
    object.get(f.case, "reason_code", null) in {
        null, "", "fit_too_small", "wrong_size", "changed_mind",
        "arrived_damaged", "not_as_described", "defective",
    }
    valid_rate
    object.get(f.customer, "account_status", null) in {null, "active", "review_flagged"}
    t.tool_call.args == a
    a.decision in {"approve_refund", "deny_refund", "escalate_to_supervisor", "request_more_info"}
}

# Compare actual ordered backend receipts with the freshly re-read authoritative
# snapshot. Model argument copies, "checks_complete", and role claims are unused.
ordered_reads if {
    some i, j, k
    i < j
    j < k
    t.reads[i] == {"name": "returns_get_case", "result": f.case}
    t.reads[j] == {"name": "oms_get_order", "result": f.order}
    t.reads[k] == {"name": "customer_get_profile", "result": f.customer}
}

intake if {
    ordered_reads
    f.case.order_id == f.order.id
    customer_correlated
    f.order.customer_id == f.case.customer_id
    f.order.status == "delivered"
    f.case.currency == f.order.currency
    f.case.currency == "USD"
    f.case.refund_amount >= 0
    f.case.refund_amount <= f.order.order_total
    count(f.order.items) == 1
    f.case.final_sale == f.order.items[0].final_sale
}
intake if {
    ordered_reads
    f.order.not_found == f.case.order_id
    customer_correlated
    a.decision in {"request_more_info", "escalate_to_supervisor"}
}

customer_correlated if { f.customer.id == f.case.customer_id }
customer_correlated if {
    is_string(f.case.customer_id)
    count(f.case.customer_id) > 0
    # The backend read returns the requested ID, not a model-supplied boolean.
    # ordered_reads binds this exact receipt to the current invocation/snapshot.
    f.customer == {"not_found": f.case.customer_id}
    a.decision in {"request_more_info", "escalate_to_supervisor"}
}

valid_rate if { object.get(f.customer, "lifetime_return_rate", null) == null }
valid_rate if {
    is_number(f.customer.lifetime_return_rate)
    f.customer.lifetime_return_rate >= 0
    f.customer.lifetime_return_rate <= 1
}
known_risk_data if {
    is_number(f.customer.lifetime_return_rate)
    f.customer.account_status in {"active", "review_flagged"}
}
incomplete if { not known_risk_data }
incomplete if { f.order.not_found == f.case.order_id }
incomplete if { object.get(f.case, "reason_code", null) in {null, ""} }
incomplete if {
    f.case.reason_code == "arrived_damaged"
    f.case.photos_provided == false
}
override if { f.case.reason_code in {"arrived_damaged", "defective"} }
eligible if {
    not incomplete
    f.days_since_delivery >= 0
    f.days_since_delivery <= 30
    f.case.final_sale == false
    f.case.item_condition in {"unworn_tags_attached", "unopened", "defective"}
}
ineligible if {
    f.case.final_sale
    f.case.reason_code == "changed_mind"
}
ineligible if {
    f.days_since_delivery > 30
    not override
}
risk if { f.case.refund_amount > 250 }
risk if { f.customer.lifetime_return_rate >= 0.40 }
risk if { f.customer.account_status == "review_flagged" }
risk if {
    f.days_since_delivery > 30
    override
}

recommendation := "escalate_to_supervisor" if {
    risk
} else := "escalate_to_supervisor" if {
    incomplete
    object.get(f.case, "info_requests", 0) >= 1
} else := "request_more_info" if { incomplete
} else := "approve_refund" if { eligible
} else := "deny_refund" if { ineligible
} else := "escalate_to_supervisor"

disposition if {
    a.decision == "approve_refund"
    f.case.item_condition != "defective"
    a.disposition == "restock_a"
}
disposition if {
    a.decision == "approve_refund"
    f.case.item_condition == "defective"
    a.disposition == "liquidation"
}
disposition if { a.decision == "deny_refund"; a.disposition == "return_to_customer" }
disposition if {
    a.decision in {"request_more_info", "escalate_to_supervisor"}
    a.disposition == null
}
cited if {
    count(a.citations) > 0
    every citation in a.citations {
        citation in {"policy#return-window", "policy#final-sale", "policy#condition",
                     "policy#evidence", "policy#escalation", "policy#statutory-rights"}
    }
    count(a.rationale) > 0
    required_citation in a.citations
}
required_citation := "policy#evidence" if { a.decision == "request_more_info"
} else := "policy#escalation" if { a.decision == "escalate_to_supervisor"
} else := "policy#final-sale" if { a.decision == "deny_refund"; f.case.final_sale
} else := "policy#return-window"
pre_tool_verdict := {"decision": "allow", "reason": "durable-identical-result-no-new-effect"} if {
    scope
    ordered_reads
    f.audit.id == f.case.audit_id
    f.audit.kind == "decision-audit"
    f.audit.case_id == a.rma_id
    f.case.decision == a.decision
    f.case.disposition == a.disposition
    object.filter(f.audit, {"rma_id", "decision", "disposition", "citations", "rationale"}) == a
} else := {"decision": "escalate", "reason": "authenticated-supervisor-handoff"} if {
    scope
    f.case.status == "in_triage"
    intake
    a.decision == recommendation
    disposition
    cited
    recommendation == "escalate_to_supervisor"
} else := {"decision": "allow", "reason": "scope-intake-eligibility-risk-checked"} if {
    scope
    f.case.status == "in_triage"
    intake
    a.decision == recommendation
    disposition
    cited
    recommendation != "escalate_to_supervisor"
}
