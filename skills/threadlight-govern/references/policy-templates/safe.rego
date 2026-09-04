package threadlight.safe

import rego.v1

# These refund thresholds and field names are business-specific examples.
# The host, never model arguments, owns snapshot.safe and verifies its records.
default pre_tool_verdict := {"decision": "allow", "reason": "outside-refund-scope"}

valid_amount if {
    amount := input.policy_target.value.refund_amount
    is_number(amount)
    amount >= 0
}

receipt_verified if {
    input.snapshot.safe.evidence.receipt_verified == true
}

refund_approved if {
    input.snapshot.safe.escalations.refund_approved == true
}

pre_tool_verdict := {"decision": "deny", "reason": "refund-evidence-required"} if {
    input.tool.name == "returns_apply_decision"
    not receipt_verified
} else := {"decision": "deny", "reason": "refund-amount-invalid"} if {
    input.tool.name == "returns_apply_decision"
    not valid_amount
} else := {"decision": "escalate", "reason": "refund-approval-required"} if {
    input.tool.name == "returns_apply_decision"
    input.policy_target.value.refund_amount > 500
    not refund_approved
} else := {"decision": "allow", "reason": "refund-invariants-satisfied"} if {
    input.tool.name == "returns_apply_decision"
}

default post_tool_verdict := {"decision": "allow", "reason": "outside-refund-result-scope"}

post_tool_verdict := {
    "decision": "transform",
    "reason": "refund-result-email-removed",
    "transform": {
        "path": "$policy_target",
        "value": object.remove(input.policy_target.value, {"customer_email"}),
    },
} if {
    input.tool.name == "returns_apply_decision"
    is_object(input.policy_target.value)
    "customer_email" in object.keys(input.policy_target.value)
}

default output_verdict := {"decision": "allow", "reason": "no-structured-email-field"}

output_verdict := {
    "decision": "transform",
    "reason": "structured-output-email-removed",
    "transform": {
        "path": "$policy_target",
        "value": object.remove(input.policy_target.value, {"customer_email"}),
    },
} if {
    is_object(input.policy_target.value)
    "customer_email" in object.keys(input.policy_target.value)
}
