---
name: threadlight-hitl-patterns
description: >
  Generate Teams Adaptive Card flows + bot UX components for the seven
  canonical action gates (approve, edit-and-approve, reject, escalate,
  signoff, audit-view, request-info) declared in spec § 8 Human Interaction
  Points. Pairs with foundry-teams-bot for delivery.
  USE FOR: human-in-the-loop, approval cards, Teams Adaptive Cards for
  agent decisions, action gate UX, edit-and-approve flow, escalation
  card, signoff flow, threadlight HITL, add gate to Kratos export.
  DO NOT USE FOR: bot infrastructure (use foundry-teams-bot), workspace
  UI (use threadlight-workspace-ui), agent runtime logic (use
  threadlight-deploy).
metadata:
  version: "1.2.0"
---

# Threadlight HITL Patterns

Generate **Teams Adaptive Cards** + bot integration for the seven canonical
action gates declared in `specs/SPEC.md` § 8 Human Interaction Points.

> **Why a separate skill from `foundry-teams-bot`?** `foundry-teams-bot`
> handles the bot **infrastructure** (manifest, ACA, UAMI, MsalConnectionManager,
> messaging extension routing). This skill handles the **gate UX** — the
> Adaptive Card content, the Action.Submit handlers, the audit-trail wiring.
> One bot, many gates; the bot doesn't know what the gates mean.

Before generation, select framework, enforcement path, channel and review window
against the [runtime support matrix](../../docs/runtime-support.md). A Teams UI
does not supply native resume or delegated authority. Do not choose a deferred
GitHub Copilot SDK flow and discover its unsupported resume seam only at deploy.

## When to Use

- Process spec § 8 declares one or more action gates
- Process needs human approval/escalation/signoff in Teams
- Edit-and-approve flow (operator can amend the agent's proposal before approving)

## When NOT to Use

- Process is fully autonomous (no § 8)
- Operator works in a workspace UI only (use `threadlight-workspace-ui`);
  but note that a workspace can still embed action gates locally

---

## Input contract / Output artifacts

**Input contract**:

- `specs/SPEC.md` § 8 — for each interaction:
  - `Action gate`: one of the seven canonical gates (see below)
  - `Linked business rules` (BR-XXX list)
  - `Data Presented`: which fields the human sees
  - `Options`: what actions the human can take
  - `Timeout/SLA`: how long before escalation
- `specs/SPEC.md` § 4 — entity field schemas (for the card data binding)
- `AGENTS.md` — for the agent identity that calls the gate

**Output**:

```
<skills-root>/{skill-using-gate}/cards/
├── {gate-name}.json           # Adaptive Card template
└── {gate-name}-handler.py     # Action.Submit response handler
src/bot/cards/
├── card_router.py             # Routes incoming Action.Submit to the right handler
├── audit_trail.py             # Writes gate outcomes to Cosmos (or AppInsights)
└── card_registry.json         # Map of card name → handler module
```

> **Skills root + Kratos-export mode.** `<skills-root>` resolves per the
> [`docs/KRATOS-BRIDGE.md`](../../docs/KRATOS-BRIDGE.md) convention:
> `use-cases/<x>/skills/` for a **Kratos-exported project** (`src/hosted-agent/`
> + `use-cases/<x>/`), otherwise `src/agent/skills/` (design mode). Override with
> `--skills-root <path>`. In Kratos-export mode the gate scaffold is written
> **next to the existing use-case skills** so it travels with the same bundle.
> If the export has no `specs/SPEC.md` § 8, take the gate type + linked fields
> from the operator (or infer from `use-cases/<x>/SYSTEM_PROMPT.md`) instead of
> failing on the missing SPEC.

---

## The seven canonical gates

Aligned with the action-gate taxonomy in `threadlight-design` SPEC § 8.

### 1. `approve` — yes/no

**When**: agent proposes a low-risk action; human confirms.

**Card shape**: Title + summary card + two buttons (`Approve` / `Decline`).

```jsonc
{
  "type": "AdaptiveCard",
  "version": "1.5",
  "body": [
    {"type": "TextBlock", "text": "${title}", "size": "large", "weight": "bolder"},
    {"type": "FactSet", "facts": "${summaryFacts}"},
    {"type": "TextBlock", "text": "Linked rules: ${linkedRules}", "isSubtle": true, "size": "small"}
  ],
  "actions": [
    {"type": "Action.Submit", "title": "Approve", "data": {"gate": "approve", "decision": "approved", "case_id": "${caseId}", "review_id": "${reviewId}"}, "style": "positive"},
    {"type": "Action.Submit", "title": "Decline", "data": {"gate": "approve", "decision": "declined", "case_id": "${caseId}", "review_id": "${reviewId}"}, "style": "destructive"}
  ]
}
```

**Audit fields written**: `gate=approve`, `decision`, `case_id`, `actor`, `timestamp`,
`linked_rules`, `agent_proposal_summary`.

### 2. `edit-and-approve` — amend before commit

**When**: agent's proposal is mostly right but the human may want to tweak
fields before committing.

**Card shape**: editable Input fields prefilled with the proposal +
`Review amended proposal` and `Cancel`. Changed arguments require a new intent
and a fresh decision; the original grant must never authorize the edit.

```jsonc
{
  "type": "AdaptiveCard",
  "version": "1.5",
  "body": [
    {"type": "TextBlock", "text": "${title}", "size": "large", "weight": "bolder"},
    {"type": "Input.Text", "id": "field1", "label": "Field 1", "value": "${proposed.field1}"},
    {"type": "Input.ChoiceSet", "id": "field2", "label": "Field 2", "value": "${proposed.field2}", "choices": "${field2Choices}"},
    {"type": "Input.Text", "id": "rationale", "label": "Why edit?", "isMultiline": true}
  ],
  "actions": [
    {"type": "Action.Submit", "title": "Review amended proposal", "data": {"gate": "edit-and-approve", "decision": "review_edits", "case_id": "${caseId}", "review_id": "${reviewId}"}, "style": "positive"},
    {"type": "Action.Submit", "title": "Cancel", "associatedInputs": "none", "data": {"gate": "edit-and-approve", "decision": "cancelled", "case_id": "${caseId}", "review_id": "${reviewId}"}}
  ]
}
```

**Audit fields**: includes `proposed_diff` (the delta between agent proposal
and human-edited values) for traceability.

### 3. `reject` — refuse with reason

**When**: human declines and must record why (regulatory or quality reasons).

**Card shape**: reason picker (ChoiceSet from the linked rules) + free-text +
single `Reject` button.

### 4. `escalate` — route to higher authority

**When**: case exceeds the human's authority; routes to a queue or named role.

**Card shape**: role/queue picker + reason + `Escalate` button. After submit,
post a NEW card to the escalation target.

### 5. `signoff` — attest review (no veto)

**When**: regulator requires human attestation but human has no veto power
(read-and-acknowledge).

**Card shape**: full case detail + `I have reviewed and acknowledge` single
button. Records signature trail (actor + timestamp + content hash).

### 6. `audit-view` — read-only inspection

**When**: human (auditor, compliance) inspects a case without taking action.

**Card shape**: full case detail with NO action buttons. Generates an audit
event ("viewed by X at T") for compliance.

### 7. `request-info` — ask for more data

**When**: agent can't proceed without more input from the customer or an
external party.

**Card shape**: templated message composer (subject + body, optionally with
attachment slots). Submit posts the templated message via the configured
channel (Teams chat, email via Logic App, etc.).

---

## Generation procedure

### Step 1: Walk spec § 8

For each interaction:

```python
gate = interaction["action_gate"]
linked_rules = interaction["linked_business_rules"]
fields = interaction["data_presented"]
sla = interaction["timeout_sla"]
```

### Step 2: Generate the card template

- Pick the canonical card shape from this skill's `references/cards/{gate}.json`
- Substitute `${title}`, `${summary}`, `${linkedRules}`, etc. with server-owned review data
- Bind `reviewId` to the exact pending intent nonce and `caseId` to its authoritative case
- For `edit-and-approve`: generate Input fields from the entity schema in spec § 4
- For `escalate`: derive the role list from the AGENTS.md skill actor table

### Step 3: Bind the handler to actual authority

For governed approve/reject actions, copy the executable
[`control_plane_review.py`](references/handlers/control_plane_review.py) bridge
and use the packaged `govern_control_plane.review` client. The bridge reloads a
server-owned pending review, checks case/nonce/freshness, and calls the actual
delegated decision protocol. It **never writes the business case** or starts a
new agent session. A successful result says `execution: not-started`.

The bot integration supplies `load_review(review_id) -> (case_id, pending)` from
its protected review store and a real delegated `AsyncTokenCredential`. The
configured control-plane URL, scope and approving role are server-owned inputs.
Do not derive authority from `activity.from`, Easy Auth headers, card fields or
the bot's app-only credential. Verified Teams SSO/OBO, bot delivery and consent
remain application/platform integration requirements; missing integration is a
blocker, not a reason to invent a token or grant.

Generate the bot's `handle(turn_context, activity, value)` wrapper to call
`decide_submission(value, ...)` with those trusted dependencies, then render the
recorded decision. Resume only the original operation through the supported
native host adapter; the authority is consumed there, once, before its effect.
Surface a rejected/expired/replayed decision or unavailable service explicitly.

Other gates need different handlers. Edits create a **new intent** and fresh
review; escalation validates the destination; signoff records acknowledgement;
audit-view requires audit access; request-info needs a separately authorized
message action. The bridge rejects these gates rather than mapping them all to
`approved=true`. The common bot routing signature does not make their business
semantics interchangeable.

### Step 4: Wire into the bot

Update `src/bot/cards/card_router.py` to map gate names to handlers:

```python
import importlib
import logging

log = logging.getLogger(__name__)

HANDLERS = {
    "approve":          "skills.kyc_decision.cards.approve_handler",
    "edit-and-approve": "skills.kyc_decision.cards.edit_and_approve_handler",
    "reject":           "skills.kyc_decision.cards.reject_handler",
    "escalate":         "skills.kyc_decision.cards.escalate_handler",
    "signoff":          "skills.kyc_decision.cards.signoff_handler",
    "audit-view":       "skills.kyc_decision.cards.audit_view_handler",
    "request-info":     "skills.kyc_decision.cards.request_info_handler",
}

async def route(turn_context, activity):
    value = activity.value or {}
    gate = value.get("gate")
    if not gate or gate not in HANDLERS:
        return error_card("unknown-gate", details=f"Got {gate!r}")
    try:
        handler = importlib.import_module(HANDLERS[gate]).handle
    except (ImportError, AttributeError) as e:
        log.exception("Handler import failed for gate=%s", gate)
        return error_card("handler-unavailable", details=str(e))
    return await handler(turn_context, activity, value)
```

`error_card(code, details=None)` is a small helper that builds an
Adaptive Card with a short red-banner error message and a "Try again"
button — define it once in `src/bot/cards/_error.py` and import.

### Step 5: Generate the audit-trail writer

`src/bot/cards/audit_trail.py` records UI interactions under the application's
data-retention policy. This is not the immutable governance receipt or the
business decision audit, and must not be used as proof that an effect occurred.
Record the authority's decision reference and authenticated subject rather than
treating display names as identity. The UI record has this application-specific
shape:

```json
{
  "id": "audit-{case_id}-{gate}-{activity_id}",
  "case_id": "...",
  "gate": "approve | edit-and-approve | reject | escalate | signoff | audit-view | request-info",
  "decision": "approved | declined | cancelled | escalated_to | acknowledged | viewed | requested",
  "actor": {"upn": "...", "displayName": "..."},
  "timestamp": "ISO 8601",
  "linked_rules": ["BR-001", "BR-007"],
  "agent_proposal": {...},
  "human_edits": {...},     // only for edit-and-approve
  "rationale": "..."        // only for reject / edit-and-approve
}
```

> **Deterministic id, not `uuid4`.** The `id` is composed from the case
> id, the gate name, and the source `activity.id` from Bot Framework.
> This makes the write **idempotent** under Bot Framework retries (Teams
> is at-least-once for outgoing card submissions when the bot times out)
> — a duplicate Action.Submit collapses into a single audit row instead
> of double-booking the case. Pair with `upsert_item` not `create_item`.

**Keyless Cosmos pattern (mandatory for threadlight pilots):**

```python
# src/bot/cards/audit_trail.py
import os
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential

# Module-level singletons — created once at bot startup, reused for the
# bot's lifetime. Re-creating CosmosClient per request is a known cause
# of socket exhaustion under load (Cosmos SDK opens up to 100 sockets
# per client; reuse keeps it bounded).
_credential = DefaultAzureCredential()
_client = CosmosClient(url=os.environ["COSMOS_ENDPOINT"], credential=_credential)
_container = (
    _client.get_database_client(os.environ["COSMOS_DB"])
           .get_container_client("case_audit")
)

async def write_audit(record: dict) -> None:
    # Deterministic id: see audit-schema.md for the rationale.
    # upsert is idempotent under Bot Framework retries.
    await _container.upsert_item(body=record)

async def aclose() -> None:
    """Call from bot shutdown hook to release sockets cleanly."""
    await _client.close()
    await _credential.close()
```

> **Keyless RBAC pin**: assign `Cosmos DB Built-in Data Contributor`
> (`00000000-0000-0000-0000-000000000002`) to the bot's UAMI on the Cosmos
> account scope. **NOT** `DocumentDB Account Contributor` — that's
> control-plane only. See `azd-patterns` § Shared UAMI for the Bicep wiring.
> Verify with `az cosmosdb sql role assignment list --account-name <acct>
> --resource-group <rg>` after deploy.

This audit trail powers:
- The workspace UI's audit viewer (see `threadlight-workspace-ui`)
- The continuous-eval KPIs (see `foundry-evals` continuous loop)
- Regulator-facing reports (compliance team queries Cosmos directly)

### Step 6: SLA timeouts

For gates with a `Timeout/SLA` in spec § 8:

- Generate a scheduled ACA job (via `threadlight-event-triggers`) that scans
  for un-acted-on cases past their SLA
- The job posts an `escalate` card to the escalation target
- Records a "SLA breach" audit event

---

## Card content rules

- **One card = one decision.** Don't bundle approve+edit+reject in one card.
- **Always show linked BR-XXX.** Auditor needs to see which rules drive
  this decision point.
- **Never include free-form chat in an action card.** That's a different
  surface (chat inside Teams).
- **Always include case-id.** The handler needs to round-trip it.
- **Use Adaptive Cards 1.5+.** v1.5 adds `carouselPage`, table layout,
  `Action.Execute` with refresh, and `targetWidth` responsive overrides
  that we depend on for the workspace UI's adaptive width. Teams supports
  v1.5 in current desktop / mobile / web clients.

---

## SLA + escalation pattern

Gates with SLAs need a follow-up watcher:

```
spec § 8 says: "Approve within 4h, otherwise escalate to manager"
       ↓
ACA Job (cron `*/15 * * * *` — every 15 minutes): scan Cosmos for cases where:
  status='awaiting_approval' AND created_at < now() - 4h
       ↓
For each match:
  1. Write audit event "SLA breach"
  2. Post `escalate` card to manager (using the same gate plumbing)
  3. Mark case as 'escalated'
```

The watcher is generated by `threadlight-event-triggers` based on the SLA
declarations harvested by this skill.

---

## Reference files

| File | Purpose | Status |
|------|---------|--------|
| `references/cards/README.md` | Per-gate card schemas (one section per gate, embedded) | shipped |
| `references/audit-schema.md` | Full audit-trail JSON schema + deterministic-id rationale | shipped |

> Card templates and SLA-watcher wiring details live inside the two files
> above (one consolidated reference per concern keeps the templates in
> sync with the gate-vocabulary changes documented in
> `threadlight-design/references/speckit-template.md` § 8). SLA-watcher
> Bicep + cron wiring is generated by `threadlight-event-triggers` and
> consumes only the SLA fields written by this skill — there is no
> separate `sla-watchers.md` reference.

---

## Anti-patterns

- ❌ **Don't put business logic in the card.** Cards are display + intent
  capture. Logic lives in the handler module.
- ❌ **Don't skip audit trail** — even for `audit-view` (the act of viewing
  is itself auditable).
- ❌ **Don't reuse one card for multiple gates** — each gate has different
  field requirements and audit semantics.
- ❌ **Don't ship without SLA watcher** when spec § 8 declares an SLA. A
  card without a watcher is a card that silently misses its deadline.
- ❌ **Don't hardcode actor lists for `escalate`** — read them from
  AGENTS.md or a config file so they evolve with the org.

---

## See Also

| Skill | Use When |
|-------|----------|
| [`threadlight-design`](../threadlight-design/) | Produces the spec § 8 + § 8b that drive gate selection |
| [`foundry-teams-bot`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-teams-bot/) | Hosts the bot infrastructure that delivers cards |
| [`threadlight-workspace-ui`](../threadlight-workspace-ui/) | Renders the same gates inside the operator workspace |
| [`threadlight-event-triggers`](../threadlight-event-triggers/) | Generates the SLA watcher ACA job |
| [`foundry-evals`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-evals/) | Reads the audit trail to compute continuous-loop KPIs |
| [`threadlight-safe-check`](../threadlight-safe-check/) | Verifies the bot + audit trail this skill generates are reachable from SPEC § 8 channels (post-deploy gate) |
