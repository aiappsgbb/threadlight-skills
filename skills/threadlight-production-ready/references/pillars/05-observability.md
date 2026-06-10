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
