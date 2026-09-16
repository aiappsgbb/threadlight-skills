# Adaptive Card templates

Seven copyable Adaptive Card 1.5 templates are supplied here. Bind their
`${...}` values from a server-owned review projection, not a model's authority
claims. The card is a user interface, **not an approval grant**.

| Gate | File | Submitted intent |
|------|------|------------------|
| Approve | [approve.json](approve.json) | Approve or decline the current review |
| Edit and approve | [edit-and-approve.json](edit-and-approve.json) | Propose changed fields for a **new intent** |
| Reject | [reject.json](reject.json) | Decline with a reason |
| Escalate | [escalate.json](escalate.json) | Request server-validated routing |
| Signoff | [signoff.json](signoff.json) | Acknowledge review, not business execution |
| Audit view | [audit-view.json](audit-view.json) | None: no action buttons |
| Request information | [request-info.json](request-info.json) | Request a separately authorized message |

Common bindings are `title`, `summary`, `caseId` and, for submissions, `reviewId`.
Additional display/input bindings are explicit in each JSON file. Replace the
editable field with the application's typed entity fields; keep server-side
validation independent of card input validation. Cancel buttons bypass input
validation but confer no permission.

The server must reload the exact review, verify the authenticated human and
current entitlement, and reject expired, changed or already consumed authority.
Changing arguments requires a new intent and fresh decision; copying the old
grant onto edited values is forbidden. Recipient/queue controls belong to the
server. Rendering an audit card does not prove that a human read it.

These files do not provision a bot, exchange a Teams identity for a delegated
control-plane token, or resume a native session. See the
[runtime/channel support matrix](../../../../docs/runtime-support.md) before
choosing a handler. Native Outlook approval and ordinary Teams card submission
are different authority paths.

The [delegated decision bridge](../handlers/control_plane_review.py) implements
approve/reject against the existing control-plane protocol. It requires a
server-owned review loader and a real delegated credential supplied by the bot's
verified identity integration. It records a decision, never the business effect.

Format reference: [official Adaptive Cards input/submit example](https://learn.microsoft.com/adaptive-cards/samples/input-form-with-right-to-left#input-form-with-rtl-sample).
