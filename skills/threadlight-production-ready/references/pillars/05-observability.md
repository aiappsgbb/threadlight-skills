# Pillar 5 — `observability`

> **v0.3.0:** Wires `OBS-106` as a live probe (Foundry account
> diagnostic settings → Log Analytics) and lights up `OBS-102` as a
> real KQL freshness probe (`traces | where timestamp > ago(24h)`)
> instead of the v0.2.0 no-op stub. A green `OBS-101` + red `OBS-106`
> is the classic "we wired App Insights but forgot the diag settings"
> failure mode this surfaces.

> **What this pillar answers.** Is App Insights connected at the
> **Foundry account level** (not just project level)? Is OTel emit
> verified by real recent traces? Are alert rules wired? Workbook?
> Retention?

This pillar partners with `foundry-observability` — the awesome-gbb
skill that does the wiring. This skill **verifies the wiring took
effect**.

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `OBS-001` | App Insights resource declared in `infra/` | `must-fix` if missing |
| `OBS-002` | Foundry account-level App Insights connection declared (postprovision hook or Bicep config wiring `appInsightsResourceId` to the account, not just the project) | `must-fix` if absent |
| `OBS-003` | ACA workloads call `configure_azure_monitor()` (or equivalent OTel init) in `container.py` / entry point | `should-fix` if missing |
| `OBS-004` | Workbook exists in repo (`docs/workbooks/*.json`) or deployed via Bicep | `should-fix` if absent |
| `OBS-005` | Log Analytics retention declared (`retentionInDays` set) | `should-fix` if default (30 days) for production |

### Outcome-KPI scorecard (static — F7)

CAF's agent observability triad puts *baselines* (latency, cost-per-interaction,
success-rate) and *deviation alerts* under observability, and asks teams to
measure a real outcome — not just wire traces. These join eval pass-rate +
the **measured** cost per successful interaction + live traces into one view
(rendered as report § 8).

| ID | Check | Default status |
|---|---|---|
| `KPI-001` | SPEC/docs declare all three outcome baselines: target latency, cost-per-interaction, success/pass rate | `should-fix` if any missing |
| `KPI-002` | A deviation alert is wired against a KPI baseline (Insights `metricAlerts`/`scheduledQueryRules` referencing latency/cost/success, or a declared baseline alert) | `should-fix` if absent (recipe `KPI-002`) |
| `KPI-003` | Outcome scorecard is joinable: eval pass-rate (`specs/evals-manifest.json`) **+** actual cost/successful interaction (`specs/cost-reconciliation-manifest.json` → `unit_economics`, re-derived from the pinned actuals) **+** traces emitting all present | `should-fix` if partial, `not-verified` if no signals |

### The KPI-003 cost signal is an actual, never a forecast

`specs/cost-manifest.json` is a *projection*: a cost-per-interaction number in
it is what someone planned to spend, so it can never satisfy KPI-003 — a pilot
that has not billed a single interaction would otherwise report a green unit
cost. (COST-005/006/007 keep consuming that forecast; KPI-003 does not read it.)

The value is read through the same strict loader COST-102/COST-103 use, so it
inherits every proof in that bundle (exact schemas, canonical-JSON digests of
the forecast and actuals, the raw `specs/SPEC.md` § 14 anchor,
verdict-after-evidence timestamps, and a staleness re-check against *today's*
clock — never a `*_ref.path` the artifact chose). On top of that, all of the
reconciler's own gates must hold: envelope `status`, `maturity.status` and
`unit_economics.status` are `pass`, `successful_interactions` is a positive
integer, and `cost_per_successful_interaction_usd` is a finite number `>= 0`.

`unit_economics.target_status` must be a *decided* verdict — `pass` **or**
`should-fix`. A unit cost above the declared § 14 target is still a measured
unit cost, and KPI-003 reports whether the outcome can be measured, not whether
it complies: the target gap is carried by the COST findings. `not-verified`
there is rejected, because a not-verified comparison beside a `pass`
measurement is an internally inconsistent artifact.

### The unit cost is re-derived from the digest-pinned actuals

`unit_economics` states both the unit cost **and** the success count it divided
by, in the same block — so those two agreeing with each other proves nothing.
The canonical numbers are `cost.period_total_usd` and
`usage.successful_interactions` in `specs/cost-actuals-manifest.json`, which the
loader has already pinned by canonical-JSON digest: editing that document in
place invalidates the whole bundle, and restating it honestly re-chains the
digest and *moves the measurement*.

So the relayed number is only reported when the pinned actuals support it:

| Requirement | Withheld when |
|---|---|
| `usage.interaction_status` is `pass` | the actuals never verified their own interaction count |
| `usage.successful_interactions` is a positive integer | there is no divisor to re-derive against |
| it equals `unit_economics.successful_interactions` | the reconciliation divided by a count its evidence does not record |
| `cost.period_total_usd` is a finite number `>= 0` | there is no authoritative actual total |
| the relayed unit cost equals `period_total_usd / successful_interactions` | the reported unit cost is not the one the evidence produces |

The recompute mirrors `reconcile._rate`: the period total is normalized to
cents, divided, and quantized to 4 dp with `ROUND_HALF_UP`. A disagreement
within half of that last step (`0.00005`) is a rounding convention, not a
contradiction, and is accepted. Anything larger is withheld with one bounded
`[warn]` line — KPI-003 never relays a contradicted number, and never
substitutes its own recomputed one for the artifact's.

### Live (tier 2 — `Monitoring Reader` + `Log Analytics Reader`)

| ID | Check | Default status |
|---|---|---|
| `OBS-101` | Foundry account `appInsightsResourceId` property points to the deployed AppIn (account-level connection alive) | `must-fix` if absent |
| `OBS-102` | KQL `traces | where timestamp > ago(30m) | take 1` returns ≥ 1 row | `should-fix` if zero (claimed observability but no ingestion) |
| `OBS-103` | KQL `dependencies | where target contains "openai" \| "foundry" | where timestamp > ago(24h) | summarize count()` returns > 0 | `should-fix` if zero |
| `OBS-104` | At least one alert rule exists in the RG (or pointing at the AppIn / Foundry resource) | `must-fix` if zero |
| `OBS-105` | Workbook count > 0 in the RG (deployed `Microsoft.Insights/workbooks`) | `should-fix` if zero |
| `OBS-106` | Log Analytics workspace `retentionInDays >= 90` | `should-fix` if < 90 |

## Trace freshness rule

The most common pattern: AppIn exists, the Bicep wired it, but no
traces. Causes:
- `configure_azure_monitor()` never called (workload missing the OTel init).
- AppIn connection wired to the project, not the account, so
  `azure.ai.agents` traces go elsewhere.
- The agent hasn't been invoked since deploy (cold pilot).

The skill differentiates by checking: zero traces vs. only test-prompt
traces vs. real-user traces.

## Alert rules baseline

For production, at minimum:

| Alert | Why |
|---|---|
| Agent invocation 5xx error rate | Detects backend outage / model retirement |
| Tool-call latency p95 > N seconds | Detects slow MCP / downstream dependency |
| Content-filter trip count spike | Detects abuse / prompt injection wave |
| Container restart count > N / hour | Detects crash loop |
| Token usage spike beyond budget anomaly threshold | Cost guardrail (also covered in pillar 10) |

These map to the `foundry-observability` reference workbook + alert
catalogue.

## Common gaps

- "Observability is wired" but `configure_azure_monitor()` was never
  added to `container.py`. The workload imports `opentelemetry` but
  never starts it.
- AppIn connection on the project, not the account → traces go to a
  different store than the SRE looks at.
- Alert rules: none. The pilot is observable but no one is paged.
- Workbook exists but it's a copy of an empty starter.
- Log retention = 30 days (default). The risk team requires 90+.

## Remediation

| Finding | Skill |
|---|---|
| Wire AppIn at account-level | `foundry-observability` |
| Add OTel init to workload | `foundry-observability` (workload patterns) |
| Add alert rules | `foundry-observability` |
| Deploy reference workbook | `foundry-observability` |
| Extend retention | `azd-patterns` |

## Why this pillar matters

The day after go-live, the agent will hit an issue. The first question
SRE asks is "show me the trace". If there are no traces — or the
traces are partial — the rollback decision is made blind. Observability
is the difference between "find and fix in 20 minutes" and "find and
fix in 3 days".

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.
