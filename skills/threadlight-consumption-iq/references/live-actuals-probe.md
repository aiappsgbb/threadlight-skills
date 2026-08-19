# Live actuals probe runbook (Task 12)

> Read-only proof that `consumption_iq.py actuals`' Cost Management Query
> API (`2025-03-01`) parsing survives contact with a real subscription
> before any customer ever runs it. This runbook is the exact procedure the
> probe followed and the checklist that turned its raw output into
> `references/fixtures/sample-cost-actuals/live-shape.json`.
>
> **This is not an invoice reconciliation.** The probe reads
> `Usage`/pre-tax cost, Azure Monitor token metrics and Log Analytics
> interaction counts — the same evidence `actuals` always collects — never
> a billing statement, a reservation/amortization view, or a claim about
> what will be invoiced.

## Isolation contract

* Run against a **dedicated, isolated, personal demo Azure subscription**
  — never a shared, customer, or production tenant.
* Set `AZURE_CONFIG_DIR` / `AZD_CONFIG_DIR` from the per-tenant index
  (see the `azure-tenant-isolation` skill) and assert the resolved
  subscription is the intended one **before** any query:

  ```bash
  test -n "$AZURE_CONFIG_DIR"
  test -n "$AZD_CONFIG_DIR"
  az account show --query '{id:id,tenantId:tenantId,name:name}' -o json
  ```

  Confirm the printed subscription is in the tenant alias's
  `allowed_subscriptions` before proceeding. If it is not, stop — do not
  proceed on an unconfirmed subscription.

## Required RBAC (read-only; nothing else is granted or used)

| Role | Scope | Why |
|---|---|---|
| **Cost Management Reader** | subscription or resource group | `Microsoft.CostManagement/query` (the `Usage` Query API) |
| **Monitoring Reader** | the AI/Cognitive account | token metrics (`InputTokens`/`OutputTokens`) |
| **Log Analytics Reader** | the workspace | the interaction/trace query |

No write, deploy, or Bicep-mutation permission is granted or exercised at
any point. The probe is `actuals` only — it never calls `reconcile`
against a live source (`reconcile` never contacts Azure to begin with) and
never runs `run --all --with-actuals` against a customer subscription.

## Step 1 — establish the isolated context

```bash
test -n "$AZURE_CONFIG_DIR"
test -n "$AZD_CONFIG_DIR"
az account show --query '{id:id,tenantId:tenantId,name:name}' -o json
```

## Step 2 — collect into an out-of-repo raw evidence directory

`RAW_EVIDENCE_DIR` is **required**, operator-provided, and must resolve
outside the git working tree — there is no default, and the guard below
fails closed if it resolves inside the repository:

```bash
: "${COST_WINDOW_START:?set COST_WINDOW_START to YYYY-MM-DD}"
: "${COST_WINDOW_END:?set COST_WINDOW_END to YYYY-MM-DD}"
: "${AZURE_SUBSCRIPTION_ID:?set the verified, confirmed subscription id}"
: "${PILOT_RESOURCE_GROUP:?set the dedicated AI workload resource group}"
: "${LOG_ANALYTICS_RESOURCE_ID:?set the workspace ARM resource id}"
: "${PILOT_ROOT:?set the pilot workspace path (holds specs/SPEC.md)}"
: "${RAW_EVIDENCE_DIR:?set a private, out-of-repo directory; there is no default}"

mkdir -p "$RAW_EVIDENCE_DIR"
RAW_EVIDENCE_DIR="$(cd "$RAW_EVIDENCE_DIR" && pwd)"
REPO_ROOT="$(cd "$PILOT_ROOT" && git rev-parse --show-toplevel)"
case "$RAW_EVIDENCE_DIR" in
  "$REPO_ROOT"|"$REPO_ROOT"/*)
    echo "RAW_EVIDENCE_DIR ($RAW_EVIDENCE_DIR) resolves inside the git repository ($REPO_ROOT); refusing to write raw billing evidence there." >&2
    exit 1
    ;;
esac

python skills/threadlight-consumption-iq/scripts/consumption_iq.py actuals \
  --start "$COST_WINDOW_START" \
  --end "$COST_WINDOW_END" \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$PILOT_RESOURCE_GROUP" \
  --workspace-resource-id "$LOG_ANALYTICS_RESOURCE_ID" \
  --spec "$PILOT_ROOT/specs/SPEC.md" \
  --actuals-manifest "$RAW_EVIDENCE_DIR/threadlight-cost-actuals.json"
```

`RAW_EVIDENCE_DIR` must never be `/tmp` or any other shared/ephemeral
location — it holds real, unsanitized billing evidence (live resource IDs,
subscription ID, actual prices) for the short time between collection and
sanitization, and is discarded once Step 3 is complete. Nothing under
`RAW_EVIDENCE_DIR` is ever committed, read by this repository's tests, or
retained after the sanitized fixture is written.

Expected: read-only calls only; `az account show` confirmed the tenant and
subscription before the first query; no Azure mutation of any kind.

## Step 3 — sanitization checklist

Every item below was applied to the raw manifest before anything derived
from it touched this repository. **No item is optional.**

- [x] Subscription ID replaced with the all-zero GUID
      (`00000000-0000-0000-0000-000000000000`) everywhere it appears
      (`scope.subscription_id` and every `resource_id`/
      `account_resource_id`).
- [x] Resource group name replaced with a synthetic, generic placeholder
      (`rg-sanitized-ai-pilot`) — the real resource group name is recorded
      nowhere in this repository.
- [x] Every resource name (storage account, container app, Cognitive
      Services/AOAI account) replaced with a synthetic name. No resource
      name from the probed subscription survives.
- [x] The AOAI model/deployment names replaced with synthetic placeholders
      (`model-sanitized` / `chat-sanitized`).
- [x] Every dollar amount replaced with deterministic synthetic values
      (`100.00` / `20.00` / `3.45`, total `123.45`) that preserve the
      **exact accounting identity**
      (`period_total_usd == sum(resources[].period_cost_usd) +
      unattributed_usd`) rather than the real prices.
- [x] Column/field **names and shape** preserved exactly as observed:
      `PreTaxCost` as the selected cost column, `ServiceName` producing
      both `service_names` (canonical, multi-valued) and `service_name`
      (the single-name convenience scalar, `null` when multiple names were
      observed).
- [x] Daily granularity preserved: one row per resource per day across the
      full 7-day window, so the end-exclusive window validation is
      exercised the same way it was against the real data.
- [x] The multi-`ServiceName`-per-`ResourceId` shape preserved
      (`Storage` + `Microsoft Defender for Cloud` on the same storage
      account) — this is real, observed multiplicity, not a synthesized
      edge case (see [Findings](#findings) below).
- [x] Token metrics preserved as observed: `cached_input_tokens: null`
      (never a manufactured `0`), `input_tokens`/`output_tokens`
      populated — the direct consequence of the mandatory-metrics-only fix
      described below.
- [x] Zero interactions preserved as an **observed pass**, not
      `not-verified` — `total_interactions: 0` /
      `successful_interactions: 0` is a genuine zero-interaction window,
      never confused with a skipped/failed query.
- [x] `warnings: []` preserved — no missing usage days, no degraded
      evidence stream in this window.
- [x] No raw Cost Management page, Azure Monitor response body, or Log
      Analytics response body is retained anywhere in this repository —
      only the already-parsed, already-sanitized `threadlight-cost-actuals/v1`
      manifest shape is committed.
- [x] No error text, stack trace, or verbose CLI output from the live run
      is retained.
- [x] `provenance` carries an explicit sanitization notice
      (`sanitized_fixture: true`, `shape_observed_at`,
      `values: "synthetic"`) so no consumer can mistake the fixture for
      real evidence.
- [x] Scanned for secret-shaped literals and customer/tenant-identifying
      terms (the same denylist `tests/test_no_customer_references.py`
      enforces repo-wide, plus the operator alias) — none found.
- [x] No claim anywhere in this document or the fixture that these numbers
      are, or resemble, an invoice.

## Step 4 — parser proof

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_cost_actuals.py -k live_shape -q
python -m pytest skills/threadlight-consumption-iq/tests -q
```

Expected: the sanitized fixture pins the observed shape (schema/status/
window/money-identity/multi-service/model-row/warnings), is accepted
byte-for-byte by both `consumption_iq._require_reusable_actuals` (the gate
`reconcile` / `run --all --with-actuals` use before trusting an actuals
document) and `reconciliation_emitter._validate_actuals` (the stricter,
publish-time validator), and the full skill test suite stays green.

## Findings

Two real behaviors were confirmed against genuine Cost Management /
Monitor responses from a dedicated AI workload resource group (7 complete,
fully-observed days; 16 distinct resources; USD `PreTaxCost`; 3 resources
carrying more than one `ServiceName` dimension):

1. **The same `ResourceId` can legitimately carry more than one
   `ServiceName`.** A storage account in the probed resource group billed
   under both its own workload service (`Storage`) and a separate
   protection service (`Microsoft Defender for Cloud`) on the same day.
   This was already the documented, tested design of
   `cost.resources[].service_names` /
   `service_name`(see `references/cost-actuals-manifest-schema.md`'s
   "Multiple names for one resource are normal, not an error" and
   `test_multi_service_fixture_keeps_one_resource_with_both_service_names`
   / `test_multiple_service_names_for_same_resource_are_accepted_and_summed`)
   — the live probe is the first confirmation that this shape occurs on a
   real, unmodified subscription rather than only in a hand-built test
   fixture, and that `ResourceId` (never a per-service split) is the
   correct sole aggregation identity for reconciliation.
2. **`CachedInputTokens` is not a universally-supported metric name.** An
   earlier probe against the same class of `Microsoft.CognitiveServices/accounts`
   resource showed `az monitor metrics list` reject the entire request
   with a single `400 BadRequest` when `CachedInputTokens` was requested
   alongside the mandatory `InputTokens`/`OutputTokens` — the account's
   metric-definition list offered only `InputTokens`/`OutputTokens`/
   `TotalTokens`. Because all three metric names shared one API call, the
   optional cache metric being unsupported took down the mandatory ones
   with it, degrading `usage.model_attribution_status` to `not-verified`
   even though real input/output token evidence was available the whole
   time. The mandatory token metrics query is now **Input/Output only**;
   `cached_input_tokens` is always reported `None` until a future,
   capability-probed cache-metric collection path is added (see
   `CHANGELOG.md` and `scripts/actuals_sources.py`'s `fetch_token_metrics`).
   This probe's fixture reflects that fixed, mandatory-metrics-only
   behavior (`cached_input_tokens: null`, populated `input_tokens`/
   `output_tokens`) rather than the failure it replaced.

No other evidence-shape gap was found: `status: pass`, `0` missing usage
days, `0` warnings, `interaction_status: pass` with a genuine
zero-interaction window, and `model_attribution_status: pass` with one
observed model/deployment row.

## What was retained vs. discarded

| Kept in this repository | Discarded, never committed |
|---|---|
| The manifest **shape**: schema, field names, types, window semantics, multi-service aggregation, cache-`None` behavior | The real subscription ID, tenant ID, resource group name |
| Synthetic resource/account/model/deployment names | The real resource, account, model and deployment names |
| Synthetic dollar amounts preserving the exact accounting identity | The real per-resource and period-total costs |
| A sanitization provenance notice (`sanitized_fixture`, `shape_observed_at`, `values: "synthetic"`) | Any raw Cost Management / Monitor / Log Analytics response body, CLI output, or error text |

See `references/cost-actuals-manifest-schema.md` for the full manifest
schema this fixture conforms to, and
`references/fixtures/sample-cost-actuals/live-shape.json` for the sanitized
result of this probe.
