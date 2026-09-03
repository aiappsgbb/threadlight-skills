# Contoso Claims Assistant — SPEC

## 1. Overview

A minimal SPEC fixture used to exercise the governed-actions inventory
builder against a conformant Microsoft Agent Framework (MAF) style layout.
This document intentionally has few sections; only section 8 is read by
the inventory builder.

## 8. Action Governance and Approval

The following actions require explicit approval before execution:

- `payments.refund`

The customer lookup action is read-only and does not require approval; it
is intentionally not named with a dotted action token in this section.

SAFE declarations for this agent:

- [x] authorization
- [x] approval
- [x] idempotency-or-transaction
- [x] output-mediation
- [x] audit

## 9. Appendix

Nothing else to declare. This section exists only to confirm that section
8 extraction stops before consuming trailing content.
