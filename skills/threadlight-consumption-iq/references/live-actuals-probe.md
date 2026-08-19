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
* Set `AZURE_CONFIG_DIR` / `AZD_CONFIG_DIR` from the per-tenant index (see
  the `azure-tenant-isolation` skill) **before** anything below runs. This
  runbook never authenticates and never switches context on the
  operator's behalf: it does not call `az login` and it does not call
  `az account set`. It only *reads* whatever context those two isolated
  config directories already point at and refuses to continue if that
  context is not the one the operator confirmed.
* Confirmation is **mechanical, not prose**: `EXPECTED_TENANT_ID` and
  `AZURE_SUBSCRIPTION_ID` are required inputs, and the script in Step 2
  compares them, byte-for-byte, against `az account show --query tenantId
  -o tsv` and `az account show --query id -o tsv` respectively — *before*
  the first billing/monitoring/trace query is issued. A mismatch on
  either one exits `1` with nothing queried. There is no "eyeball the
  printed JSON and proceed" step left in this runbook.

## Required RBAC (read-only; nothing else is granted or used)

| Role | Scope | Why |
|---|---|---|
| **Cost Management Reader** | subscription or resource group | `Microsoft.CostManagement/query` (the `Usage` Query API) |
| **Monitoring Reader** | the AI/Cognitive account (`MONITOR_RESOURCE_ID`) | token metrics (`InputTokens`/`OutputTokens`) behind `usage.models[]`/`model_attribution_status` |
| **Log Analytics Reader** | the workspace (`WORKSPACE_RESOURCE_ID`) | the interaction/trace (`AppTraces`) query behind `usage.total_interactions`/`interaction_status` |

No write, deploy, or Bicep-mutation permission is granted or exercised at
any point. The probe is `actuals` only — it never calls `reconcile`
against a live source (`reconcile` never contacts Azure to begin with) and
never runs `run --all --with-actuals` against a customer subscription.

Both `MONITOR_RESOURCE_ID` and `WORKSPACE_RESOURCE_ID` are **required** in
this runbook (not merely accepted) because `references/fixtures/sample-cost-actuals/live-shape.json`
pins the outcome of collecting *both* optional evidence streams, not only
the mandatory Cost Management one:

* `MONITOR_RESOURCE_ID` — the AI/Cognitive account's ARM resource ID — is
  what makes `usage.models[]` non-empty and `model_attribution_status:
  "pass"` possible at all. Without it no token-metrics query is issued and
  the fixture's one model/token row (`cached_input_tokens: null`,
  populated `input_tokens`/`output_tokens`) would never have been
  observed.
* `WORKSPACE_RESOURCE_ID` — the Log Analytics workspace's ARM resource ID
  — is what makes the fixture's `interaction_status: "pass"` with
  `total_interactions: 0` a genuine **observed zero**, backed by a real
  `AppTraces` query that ran and returned an empty result, rather than a
  skipped query that would instead degrade to `not-verified`. Omitting it
  cannot reproduce that shape.

## Step 1 — required inputs and the isolation contract they enforce

Nothing below has a default and nothing below is inferred. Every input is
read, never guessed:

| Variable | Holds |
|---|---|
| `AZURE_CONFIG_DIR` / `AZD_CONFIG_DIR` | the per-tenant isolated CLI/azd config dirs (already authenticated — this runbook never logs in) |
| `EXPECTED_TENANT_ID` | the tenant GUID the operator has confirmed for this alias |
| `AZURE_SUBSCRIPTION_ID` | the subscription GUID the operator has confirmed for this alias |
| `COST_WINDOW_START` / `COST_WINDOW_END` | the closed cost window, `YYYY-MM-DD` |
| `PILOT_RESOURCE_GROUP` | the dedicated AI workload resource group |
| `MONITOR_RESOURCE_ID` | the AI/Cognitive account ARM resource ID (token metrics) |
| `WORKSPACE_RESOURCE_ID` | the Log Analytics workspace ARM resource ID (interaction traces) |
| `PILOT_ROOT` | the pilot workspace path (holds `specs/SPEC.md`) |
| `THREADLIGHT_SKILLS_ROOT` | this checked-out `threadlight-skills` repository root |
| `RAW_EVIDENCE_DIR` | a private, out-of-repo directory for raw billing evidence — never `/tmp` |

`PILOT_ROOT` and `THREADLIGHT_SKILLS_ROOT` are ordinarily two different
checkouts (the customer/pilot project vs. this skills repository); Step 2
guards `RAW_EVIDENCE_DIR` against *both*, independently, because either one
being a git working tree is enough for an accidental `git add -A` to pick
up raw billing evidence.

## Step 2 — mechanical checks, then collect into an out-of-repo raw evidence directory

The whole step is one `set -euo pipefail` subshell, run with `python3`, so
a guard failure exits only the subshell — never the operator's parent
interactive shell:

```bash
(
set -euo pipefail

# ---- required inputs; none has a default ----
: "${AZURE_CONFIG_DIR:?set AZURE_CONFIG_DIR to the isolated per-tenant Azure CLI config dir}"
: "${AZD_CONFIG_DIR:?set AZD_CONFIG_DIR to the isolated per-tenant azd config dir}"
: "${EXPECTED_TENANT_ID:?set EXPECTED_TENANT_ID to the confirmed tenant id for this alias}"
: "${AZURE_SUBSCRIPTION_ID:?set AZURE_SUBSCRIPTION_ID to the confirmed, verified subscription id}"
: "${COST_WINDOW_START:?set COST_WINDOW_START to YYYY-MM-DD}"
: "${COST_WINDOW_END:?set COST_WINDOW_END to YYYY-MM-DD}"
: "${PILOT_RESOURCE_GROUP:?set the dedicated AI workload resource group}"
: "${MONITOR_RESOURCE_ID:?set the AI/Cognitive account ARM resource id for token metrics}"
: "${WORKSPACE_RESOURCE_ID:?set the Log Analytics workspace ARM resource id for interaction traces}"
: "${PILOT_ROOT:?set the pilot workspace path (holds specs/SPEC.md)}"
: "${THREADLIGHT_SKILLS_ROOT:?set the checked-out threadlight-skills repository root}"
: "${RAW_EVIDENCE_DIR:?set a private, out-of-repo directory for raw billing evidence; there is no default}"

# ---- mechanical tenant/subscription enforcement, BEFORE any query ----
# Reads the context AZURE_CONFIG_DIR/AZD_CONFIG_DIR already point at; never
# authenticates, never switches subscription.
actual_tenant_id="$(az account show --query tenantId -o tsv)"
actual_subscription_id="$(az account show --query id -o tsv)"
if [ "$actual_tenant_id" != "$EXPECTED_TENANT_ID" ]; then
  echo "tenant mismatch: az account show reports $actual_tenant_id, expected $EXPECTED_TENANT_ID; refusing to run any command" >&2
  exit 1
fi
if [ "$actual_subscription_id" != "$AZURE_SUBSCRIPTION_ID" ]; then
  echo "subscription mismatch: az account show reports $actual_subscription_id, expected $AZURE_SUBSCRIPTION_ID; refusing to run any command" >&2
  exit 1
fi

# ---- generic guard: RAW_EVIDENCE_DIR must resolve outside EVERY required
#      repository root, not only one of them ----
require_outside_repo_root() {
  # $1: human-readable label   $2: root to guard   $3: resolved candidate
  label="$1"; root_input="$2"; candidate="$3"
  test -n "$root_input" || {
    echo "$label root is empty; refusing to proceed" >&2; exit 1; }
  test -d "$root_input" || {
    echo "$label root ($root_input) does not exist; refusing to proceed" >&2; exit 1; }
  root_real="$(cd "$root_input" && pwd -P)"
  case "$candidate" in
    "$root_real"|"$root_real"/*)
      echo "RAW_EVIDENCE_DIR ($candidate) resolves inside the $label ($root_real); refusing to write raw billing evidence there." >&2
      exit 1
      ;;
  esac
}

mkdir -p "$RAW_EVIDENCE_DIR"
raw_evidence_dir_real="$(cd "$RAW_EVIDENCE_DIR" && pwd -P)"
require_outside_repo_root "pilot workspace (PILOT_ROOT)" "$PILOT_ROOT" "$raw_evidence_dir_real"
require_outside_repo_root "threadlight-skills repository (THREADLIGHT_SKILLS_ROOT)" "$THREADLIGHT_SKILLS_ROOT" "$raw_evidence_dir_real"

python3 skills/threadlight-consumption-iq/scripts/consumption_iq.py actuals \
  --start "$COST_WINDOW_START" \
  --end "$COST_WINDOW_END" \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$PILOT_RESOURCE_GROUP" \
  --monitor-resource-id "$MONITOR_RESOURCE_ID" \
  --workspace-resource-id "$WORKSPACE_RESOURCE_ID" \
  --spec "$PILOT_ROOT/specs/SPEC.md" \
  --actuals-manifest "$raw_evidence_dir_real/threadlight-cost-actuals.json"
)
```

`RAW_EVIDENCE_DIR` must never be `/tmp` or any other shared/ephemeral
location — it holds real, unsanitized billing evidence (live resource IDs,
subscription ID, actual prices) for the short time between collection and
sanitization, and is discarded once Step 3 is complete. Nothing under
`RAW_EVIDENCE_DIR` is ever committed, read by this repository's tests, or
retained after the sanitized fixture is written. `require_outside_repo_root`
is generic — it takes a label, a root and a resolved candidate, and knows
nothing about `PILOT_ROOT` or `THREADLIGHT_SKILLS_ROOT` specifically — so
it is called once per required root instead of special-casing either one.

Expected: read-only calls only; the tenant/subscription check above matched
*before* the first query; no Azure mutation of any kind.

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
- [x] `provenance` mirrors every key `consumption_iq._actuals_provenance`
      actually emits — `sources`, `query_api_version`, `subscription_id`,
      `resource_group`, `monitor_resource_id`, `workspace_resource_id`,
      `token_source_resource_id`, `token_query_issued`,
      `interaction_query_issued`, `window`
      (`{start, end}`), `collected_at` — with synthetic/zeroed values,
      *plus* an explicit sanitization notice (`sanitized_fixture: true`,
      `shape_observed_at`, `values: "synthetic"`) so no consumer can
      mistake the fixture for real evidence. `collected_at` equals the
      document's own top-level `generated_at`, exactly as
      `_actuals_provenance` derives it from the same instant — it is the
      synthetic moment this fixture's evidence was "collected" for window
      validation purposes. `shape_observed_at` is a different, repo-only
      fact with no schema counterpart: the calendar date this sanitized
      *shape* was captured from the real probe and committed here. The two
      are allowed to differ (and do, in this fixture) without being
      inconsistent — one is evidence-internal, the other is provenance
      about the sanitization itself.
- [x] Scanned generically for anything that could still identify a person,
      account or environment: every GUID-shaped substring is the all-zero
      GUID (`00000000-0000-0000-0000-000000000000`, checked recursively,
      not just in named fields), every resource/account/model/deployment
      name carries only a `-sanitized`/`sanitized-` synthetic marker, and
      no email address, URL, or credential-shaped (`key: value`/`key=value`
      secret) fragment appears anywhere — the same secret-literal denylist
      `tests/test_no_customer_references.py` enforces repo-wide, run again
      here without adding anything operator- or person-specific to any
      denylist. No raw token is asserted-absent by name in the tests below;
      the checks are structural.
- [x] No claim anywhere in this document or the fixture that these numbers
      are, or resemble, an invoice.

## Step 4 — parser proof

```bash
python3 -m pytest \
  skills/threadlight-consumption-iq/tests/test_cost_actuals.py -k live_shape -q
python3 -m pytest skills/threadlight-consumption-iq/tests -q
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
