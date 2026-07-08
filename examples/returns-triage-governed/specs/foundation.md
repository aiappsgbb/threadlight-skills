# Foundation — Returns Triage (Contoso Retail)

> Fast-PoC mode: this record was **house-defaulted after skipping the interactive
> Step 0 foundation interview**. Every row is `source: defaulted-after-skip`.
> Override in SPEC § 7b / § 11c / § 11e / § 11f / § 13 when a real review happens.

## Framework & runtime shape

```yaml
framework: microsoft-agent-framework   # MAF — house default
runtime_shape: agent                    # single agent + skills + tools (not a DurableWorkflow)
source: defaulted-after-skip
```

Rationale: returns triage is a per-case decision with tool-gathering + a small
rule set + one human gate — an **agent** with skills fits better than a
deterministic multi-phase DurableWorkflow. Confirm at SPEC § 11e.

## Model & capacity

```yaml
model: gpt-5.4                 # 2026-03-05 — house default for multi-skill pilots
region: swedencentral
fallback_region: westeurope
capacity_type: GlobalStandard
capacity_tpm: 50K
data_boundary: EU
source: defaulted-after-skip
```

Rationale: customer region is EU → EU data boundary, Sweden Central primary with
West Europe fallback. `gpt-5.4` handles the 4-skill / multi-tool chain reliably.

## Hosting shape

```yaml
hosting: aca-hosted-agent      # Foundry hosted agent (§6.2–6.4 of the workshop)
local_runtime: threadlight_quickstart  # Pattern 0 local for §4.3
deployment_target: customer-pilot
source: defaulted-after-skip
```

## Tools & data

```yaml
tool_binding: mcp
mock_first: true               # OMS, returns-db, customer-profile are all mocked
toolbox: [oms_get_order, returns_get_case, returns_list_open, customer_get_profile, returns_apply_decision]
source: defaulted-after-skip
```

## Identity & RBAC

```yaml
identity: user-assigned-managed-identity
auth: DefaultAzureCredential   # keyless end-to-end, no API keys
rbac: least-privilege
source: defaulted-after-skip
```

## Observability baseline

```yaml
telemetry: otel + application-insights
trace_conventions: gen_ai.*
wired_from_day_one: true
source: defaulted-after-skip
```

## Data residency & compliance

```yaml
residency: EU
retention: 90d                 # demo default; regulated-7y is a deferred decision
deferred_decisions:
  - waf-front-door
  - dr-runbook
  - regulated-7y-retention
source: defaulted-after-skip
```

## Decision summary

| Area | Choice | Source |
|------|--------|--------|
| Framework | microsoft-agent-framework (agent shape) | defaulted-after-skip |
| Model | gpt-5.4 @ Sweden Central, GlobalStandard 50K TPM, EU boundary | defaulted-after-skip |
| Hosting | ACA hosted agent (Foundry) + local `threadlight_quickstart` | defaulted-after-skip |
| Tools | MCP, mock-first (3 mocked systems) | defaulted-after-skip |
| Identity | UAMI + DefaultAzureCredential, keyless | defaulted-after-skip |
| Observability | OTel + App Insights, day one | defaulted-after-skip |
| Governance | **Citadel governance-hub spoke (override — see SPEC § 11b)** | provided |
| Residency | EU, 90-day retention (regulated-7y deferred) | defaulted-after-skip |
