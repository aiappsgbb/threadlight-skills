---
name: threadlight-loadtest
description: >
  Manual, live/cost-bearing skill that runs a budget-capped load-test
  profile through k6 or locust (or an injected test adapter) and emits
  `specs/load-manifest.json` under the shared threadlight.load/v1 envelope,
  so pilots get real latency/throughput/error-rate evidence instead of
  static claims. USE FOR: load testing, latency benchmarking, throughput
  testing, k6, locust, LOAD-001/LOAD-002/LOAD-003 findings, budget-capped
  load runs, production load-test confirmation, load-manifest evidence,
  p50/p95 latency evidence, error-rate/tokens-per-request evidence. DO NOT
  USE FOR: deploying infrastructure or agents (threadlight-deploy /
  azd-patterns); autonomous/agentic-loop execution — this skill never runs
  unattended and never installs k6/locust itself; release or rollout
  gating (threadlight-production-ready); quality/groundedness evals
  (threadlight-evals); adversarial/safety scans (threadlight-redteam).
metadata:
  version: "0.1.0"
---

# Threadlight Load Test — guarded, budget-capped load evidence

A **manual, live, cost-bearing** skill. Running it can spend real money
(token cost against a live endpoint) and can hit a production endpoint if you
let it. Every run is gated **before** any load-generation command is ever
invoked:

1. **Budget ceiling.** You must supply `budget_ceiling_usd`. The run's
   projected token cost is compared **directly** to the ceiling: preferring the
   profile's declared `projected_token_cost_usd`, otherwise a deterministic
   derivation from explicit request/rate + token-rate inputs. A **known**
   projection over the ceiling **aborts** — no adapter is ever called. A
   projection that cannot be determined (no declared value and no explicit
   inputs to derive one) yields a `partial` / `LOAD-002: not-verified` manifest
   rather than a fabricated request count that could undercount real spend.
2. **Production confirmation.** If `endpoint_class == "production"`, the run
   **aborts** unless you pass `allow_production=True` explicitly.
3. **No surprise installs.** If neither `k6` nor `locust` is already on
   `PATH`, the skill does **not** install anything. It emits a `partial`
   manifest with `LOAD-002: not-verified` and tells you what's missing.

This skill never deploys anything, never loops autonomously, and never
gates a release by itself — it produces evidence for a human (or a paired
skill like `threadlight-production-ready`) to read.

## What this skill does (and does not)

- **Runs:** one guarded execution of a load profile against an adapter (`k6`,
  `locust`, or an injected adapter for tests), then summarizes the resulting
  samples (p50/p95/p99 latency, error rate, tokens/request, observed
  throughput, and cold-start / time-to-scale when samples carry them).
- **Emits:** `specs/load-manifest.json` (`threadlight.load/v1`), atomically
  written and schema-validated. A failed write never clobbers the previous
  valid manifest.
- **Never installs dependencies.** `k6`/`locust` must already be on `PATH`.
  If neither is found, the manifest records `LOAD-002: not-verified` — it
  does not attempt `pip install`, `npm install`, `brew install`, etc.
- **Never runs unattended / in an agentic loop.** There is no scheduler, no
  retry loop, no autonomous re-run. A human (or an explicit CI step) invokes
  it once per evidence run.
- **Never deploys or releases anything.** No infra provisioning, no rollout
  gating. Use `threadlight-deploy` / `azd-patterns` for deployment and
  `threadlight-production-ready` for release-readiness scoring.
- **Never writes SPEC.md automatically.** Only a `complete` run may propose an
  advisory patch snippet (`spec_update_plan`, a safe quoted-YAML block under
  `load_profile/performance`) for a human to paste into `SPEC.md` by hand — it
  is never applied automatically, and it is **omitted entirely** from partial or
  aborted manifests.
- **Never persists secrets or payloads.** No access tokens, prompts, model
  responses, request/response bodies, endpoint URLs, credential references,
  or raw command stdout/stderr ever reach the manifest (see Privacy below).

## The profile you provide

The **approved minimal shape** carries a direct, declared budget projection
plus the volume it describes:

```json
{
  "peak_requests_per_second": 2,
  "hold_seconds": 10,
  "projected_token_cost_usd": 5.00
}
```

A richer profile can also describe a live run (endpoint, script, SLO) and/or
let the projection be **derived** deterministically from explicit inputs:

```json
{
  "name": "checkout-agent-smoke",
  "endpoint": {"url": "https://staging.example.test/api", "credential_ref": "kv:load-test-key"},
  "peak_requests_per_second": 10,
  "hold_seconds": 30,
  "request_count": 300,
  "tokens_per_request_estimate": 500,
  "price_per_1k_tokens_usd": 0.002,
  "slo": {"max_p95_latency_ms": 800, "max_error_rate": 0.02},
  "script_path": "loadtest/checkout.js",
  "adapter_args": ["--summary-trend-stats", "p(50),p(95),p(99)"]
}
```

| Field | Required | Meaning |
|---|---|---|
| `projected_token_cost_usd` | no¹ | **Declared** budget projection in USD, compared directly to the ceiling. Nonnegative + finite; `0` is honored **only** because it is explicit. |
| `peak_requests_per_second` / `hold_seconds` | no | Volume of the approved shape; `peak_requests_per_second × hold_seconds` also feeds a **derived** projection. Both strictly positive. |
| `request_count` | no | Explicit total request count for a **derived** projection (positive integer). Never defaulted from `virtual_users` (that would undercount). |
| `tokens_per_request_estimate` / `price_per_1k_tokens_usd` | no¹ | Token-rate inputs for a **derived** projection (nonnegative). |
| `name` | no | Human-readable profile name; defaults to `load-profile` in the manifest if absent. |
| `endpoint.url` / `endpoint.credential_ref` | no (both, to run live) | Target + credential **reference name**, never the secret itself. Absent ⇒ `partial`, `LOAD-002: not-verified`. A present-but-malformed endpoint is a controlled validation error. |
| `duration_s` | no | Legacy run duration in seconds (positive). Used for throughput/derivation only when `hold_seconds` is absent. |
| `virtual_users` | no | Concurrent virtual users (positive integer). Descriptive only — **never** used to project cost. |
| `spawn_rate_per_s` | no | Ramp-up rate passed to the adapter |
| `slo` | no | `max_p95_latency_ms` / `max_error_rate` thresholds scored into `LOAD-003` |
| `script_path` | no | Load-test script path passed to `k6 run` / `locust -f` |
| `adapter_args` | no | Extra string argv appended safely (list of strings only) |

¹ *Nothing is structurally required.* To get a budget verdict you need **either**
a declared `projected_token_cost_usd` **or** enough explicit inputs to derive
one (`request_count` — or `peak_requests_per_second × hold_seconds` — together
with `tokens_per_request_estimate` **and** `price_per_1k_tokens_usd`). Otherwise
the projection is `unavailable` and the run is `partial` / not-verified.

Unknown profile keys are rejected — this is a strict allowlist, not a
free-form bag.

## The contract — `specs/load-manifest.json`

| Field | Meaning |
|---|---|
| `schema` | Always `threadlight.load/v1` |
| `tool_version` | Version of `scripts/loadtest.py` (currently `0.1.0`) |
| `generated_at` / `freshness` | Shared envelope timestamp fields; `source_oldest_at` is the earliest `observed_at` found across samples, or `null` |
| `status` | `complete` \| `partial` \| `aborted` — **never** reports `complete` for an aborted or partial run |
| `profile_name` | Echo of the profile's `name` |
| `endpoint_class` | `non-production` \| `production` |
| `endpoint_configured` | `true` only if the profile declared both a URL and a credential reference name (never the values themselves) |
| `allow_production` | Echo of the caller's explicit production confirmation |
| `adapter_name` | Selected engine name (`k6`, `locust`, injected name), or `null` if none was selected/available |
| `budget.ceiling_usd` | The mandatory positive ceiling |
| `budget.projected_usd` | Projected cost in USD, or `null` when `projection_source == "unavailable"` |
| `budget.within_ceiling` | Whether the projection stayed under the ceiling, or `null` when unavailable |
| `budget.projection_source` | `declared` (profile's `projected_token_cost_usd`) \| `derived` (computed from explicit inputs) \| `unavailable` (could not determine without inventing inputs) |
| `diagnostics` | `sample_count`, `p50_latency_ms`, `p95_latency_ms`, `p99_latency_ms`, `error_rate`, `tokens_per_request`, `throughput_rps`, `cold_start_latency_ms`, `time_to_scale_s` (each `null` when not observed), `adapter_error` (scrubbed, ≤220 chars, or `null`) |
| `spec_update_plan` | **Omitted entirely** unless `status == "complete"` (never a `null` placeholder); when present, an advisory `{action: "advisory", target: "SPEC.md", section: "load_profile/performance", snippet}` object |
| `findings` | Exactly one each of `LOAD-001`, `LOAD-002`, `LOAD-003` — no duplicates, missing, or unknown ids — each `pass \| must-fix \| should-fix \| not-verified` |

## Findings

| ID | Dimension | `must-fix` when | `not-verified` when |
|---|---|---|---|
| `LOAD-001` | Production safety | `endpoint_class == "production"` and `allow_production` is not `true` | — |
| `LOAD-002` | Budget / execution | A **known** projection exceeded the ceiling (run aborted) | The budget projection was `unavailable`; no adapter was selected/available; the endpoint/credential was not configured; or the adapter itself returned `partial` (including a `complete` claim with zero samples, which is treated as untrustworthy) |
| `LOAD-003` | SLO / quality | Declared `slo` thresholds were violated by the observed samples | No samples were collected or no `slo` was declared |

Aborted and partial runs are **never** represented as successful: `status`
stays `aborted`/`partial`, `spec_update_plan` is **omitted**, and `LOAD-002`
reflects exactly why.

## Gate order (what happens before any command runs)

1. Structural validation of the profile, budget ceiling, `endpoint_class`,
   and `allow_production` — a caller mistake raises `LoadTestValidationError`
   immediately (this is a programming-usage error, not a manifest state). A
   *missing* endpoint or projection is **not** a caller mistake — it flows to a
   partial gate below.
2. **Budget gate (known projection):** a declared/derived projection strictly
   over the ceiling ⇒ `status: aborted`, `LOAD-002: must-fix`. Adapter is never
   called.
3. **Production gate:** `endpoint_class == "production"` without
   `allow_production=True` ⇒ `status: aborted`, `LOAD-001: must-fix`.
   Adapter is never called.
4. **Projection gate:** the projection is `unavailable` (neither declared nor
   deterministically derivable) ⇒ `status: partial`, `LOAD-002: not-verified`.
   We never invent a request count to fabricate a projection.
5. **Adapter gate:** no adapter selected/injected ⇒ `status: partial`,
   `LOAD-002: not-verified`. Nothing is installed.
6. **Endpoint gate:** profile missing `endpoint.url` or `endpoint.credential_ref`
   ⇒ `status: partial`, `LOAD-002: not-verified`.

Only after all gates pass is `adapter.run(profile)` invoked — exactly once.
`LOAD-001` is always reported accurately even when a different gate aborts the
run (a budget abort on a production endpoint still shows `LOAD-001: must-fix`).

## Adapters

`LoadAdapter` is a `Protocol`: any object with a `name: str` attribute and a
`run(profile) -> {"status": "complete"|"partial", "samples": [...], "error"?: str}`
method satisfies it. Tests inject fakes; production code uses
`select_adapter(available_commands)` (k6, then locust, then `None` — pure
selection, no side effects) and `CommandLoadAdapter`, which:

- invokes **only** the one selected, already-existing command (`shutil.which`
  probe, no install);
- always passes argv as a **list** (never a shell string) with `shell=False`;
- always sets a `timeout_s`;
- scrubs secrets out of any error text before it can reach the manifest:
  Bearer tokens, `key=value`-shaped secrets, embedded URL credentials,
  API keys, JWTs, Azure SAS `sig=` query tokens, storage/connection-string
  keys (`AccountKey=` / `SharedAccessKey=` / `SharedAccessSignature=`), PEM
  private-key blocks, and pre-masked `******` markers (each replaced
  with `[REDACTED]`);
- never returns raw command stdout/stderr — only parsed NDJSON samples and a
  scrubbed, truncated error summary.

## Privacy

`write_load_manifest` runs the manifest through the schema-mirroring
validator, **then** a recursive forbidden-key + secret-value scan, and only
then performs an atomic write. Either check failing means nothing is written
and the previous valid manifest (if any) is untouched. Forbidden key words
include `token`, `secret`, `password`, `credential(s)`, `authorization`,
`prompt`, `completion(s)`, `payload`, `stdout`, `stderr` (word-matched, so
`tokens_per_request` is unaffected). Forbidden value shapes include
Bearer-style tokens, `sk-...` API keys, JWTs, embedded URL credentials,
Azure SAS `sig=` tokens, connection-string keys (`AccountKey=` /
`SharedAccessKey=`), PEM private-key material, and pre-masked `******`
markers — a strict allowlist of value shapes, so opaque metric IDs
(UUIDs, hex / metric identifiers) are never false-positives.

## Usage

```python
import sys
sys.path.insert(0, "skills/threadlight-loadtest/scripts")
from loadtest import run_loadtest, write_load_manifest
from adapters import detect_available_commands, select_adapter, CommandLoadAdapter

profile = {
    "name": "checkout-agent-smoke",
    "endpoint": {"url": None, "credential_ref": None},  # dry-run: no live endpoint
    "peak_requests_per_second": 10,
    "hold_seconds": 30,
    "projected_token_cost_usd": 3.00,  # declared → compared directly to the ceiling
}

available = detect_available_commands()          # pure shutil.which probe
name = select_adapter(available)                  # "k6" | "locust" | None
adapter = CommandLoadAdapter(name=name, command_path=available[name]) if name else None

manifest = run_loadtest(
    profile=profile,
    budget_ceiling_usd=5.00,       # MANDATORY — no default
    endpoint_class="non-production",
    allow_production=False,        # MUST be True to touch a production endpoint
    adapter=adapter,
    generated_at="2026-08-17T10:00:00Z",
)
write_load_manifest("specs/load-manifest.json", manifest)
```

Or from the CLI:

```bash
python3 scripts/loadtest.py \
  --profile profile.json \
  --budget-ceiling-usd 5.00 \
  --endpoint-class non-production \
  --out specs/load-manifest.json

# Production run — requires explicit confirmation flag:
python3 scripts/loadtest.py \
  --profile profile.json \
  --budget-ceiling-usd 5.00 \
  --endpoint-class production \
  --allow-production \
  --out specs/load-manifest.json
```

## Files

```
SKILL.md
scripts/adapters.py                    # LoadAdapter Protocol, select_adapter, CommandLoadAdapter
scripts/loadtest.py                    # validation, summarize_samples, run_loadtest, CLI, manifest I/O
references/load-manifest.schema.json   # threadlight.load/v1 manifest contract (draft-07)
tests/test_loadtest.py                 # pytest suite (FakeAdapter, gates, percentiles, privacy, schema parity)
```

## Tests

```bash
python3 -m pytest skills/threadlight-loadtest/tests/ skills/_shared/tests/test_manifest.py -v
```

## Common mistakes

- **Forgetting `budget_ceiling_usd`.** It is a required keyword argument —
  there is no default ceiling, and a missing/zero/negative/non-finite value
  raises `LoadTestValidationError` before anything runs.
- **Expecting a production run without `allow_production=True`.** The run
  aborts by design; this is not a bug.
- **Expecting the skill to install k6/locust.** It only probes `PATH`. Install
  the engine yourself, then re-run.
- **Treating a `partial` manifest as evidence of success.** `LOAD-002:
  not-verified` means exactly that — the run did not produce trustworthy
  complete evidence, whether because the budget projection was `unavailable`,
  no adapter ran, no endpoint was configured, or the adapter itself only
  completed part of the profile.
- **Expecting a budget verdict without a projection.** The skill will **not**
  invent a request count (e.g. from `virtual_users`) to fabricate a cost — that
  could undercount real spend. Supply a declared `projected_token_cost_usd`, or
  enough explicit inputs to derive one; otherwise you get `partial` /
  not-verified, not a green light.
- **Assuming a missing endpoint is a usage error.** An absent endpoint/credential
  yields a `partial` manifest (`LOAD-002: not-verified`), not a raised
  exception. Only a *malformed* endpoint (wrong types) is a controlled
  validation error.
- **Expecting `spec_update_plan` to modify `SPEC.md`.** It is advisory
  quoted-YAML only, present **only** on a `complete` run and omitted entirely
  otherwise, returned inside the manifest for a human to paste in by hand.
