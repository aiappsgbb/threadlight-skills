# Foundation — Technical Decision Record (`specs/foundation.md`)

Template for the **Step 0 — Foundation** artifact. Locked **before** the SPEC is
written on the **from-scratch path**, so the spec is authored on decided ground
instead of silent defaults back-filled during generation.

> **Single source of truth for the pilot's technical foundations.**
> `threadlight-design` **Step 3 (Generate SpecKit)** reads this file and
> pre-populates SPEC **§ 7b** (model), **§ 11c** (tech stack), **§ 11e**
> (workflow model, i.e. `runtime_shape`), **§ 11f** (deployment posture), and
> **§ 12** (runtime / observability) from it — it does not re-decide.
> **`framework`, `protocol`, and `policy_route` are never pre-populated into a
> SPEC section** — `specs/foundation.md` stays their sole selector authority;
> SPEC only supplies `workflow_model` / capability signals used to confirm the
> resolved `policy_route` still matches. When this file is **absent**
> (older runs, or an operator who skipped Step 0), Step 3 behaves exactly as
> before: it applies documented defaults inline. **Kratos-export projects skip
> Step 0 entirely** — the bundle is already designed.

> **Runtime-policy authority.**
> [`runtime-policy.json`](runtime-policy.json) (sibling of this template) is
> the canonical selector contract for `framework`, `runtime_shape`,
> `protocol`, and `policy_route`. **Record the resolved `schema` +
> `policy_route` in `specs/foundation.md`** (see § 1 below) — do not emit a
> filesystem link back to this skill's `references/` folder into a customer
> project's `specs/foundation.md`; that path does not exist relative to the
> customer project and would be a broken link. This template records the
> chosen route; it does not invent a competing default table.
> Canonical default tuple: `github-copilot-sdk` + `agent` + `invocations` (`policy_route: default-agent`).

> **Fast-PoC mode.** Do not interview the operator. Fill every row with the
> **house default** below, set `source: defaulted-after-skip`, and have Step 3
> surface a one-line callout in SPEC § 12 (_"Foundation not collected; using
> house defaults — override in specs/foundation.md"_). Silent defaults stay
> auditable.

> **Authority order** (drift mitigation): `specs/foundation.md` → the SPEC
> sections it pre-populates → `azd env` vars. On rerun, a decision changed in
> the SPEC that disagrees with this file is surfaced as a conflict, not
> silently overwritten.

---

## 0. Decision summary

| # | Decision | Choice | `source` | Pre-populates | Refined by |
|---|----------|--------|----------|---------------|-----------|
| 1 | Framework | `github-copilot-sdk` | provided \| defaulted | selector authority — not duplicated in SPEC | `runtime-policy.json` route resolution |
| 2 | Runtime shape | `agent` | inferred \| defaulted | § 11e, § 12 | Step 2 trait matrix → confirm at Step 4 |
| 3 | Protocol | `invocations` | provided \| defaulted | selector authority — not duplicated in SPEC | `runtime-policy.json` route resolution |
| 4 | Policy route | `default-agent` | inferred \| defaulted | selector authority — not duplicated in SPEC | `runtime-policy.json` route resolution |
| 5 | Model + capacity | `gpt-5.4` · region · TPM | provided \| defaulted | § 7b | Step 2 (tier may drop to -mini) |
| 6 | Hosting shape | `aca-hosted-agent` | provided \| defaulted | § 11c, § 11f | — |
| 7 | Tools & data | `mcp` · mock-first | inferred | § 5b, § 6 | Step 2 (tool contracts) |
| 8 | Identity & RBAC | UAMI · least-privilege | defaulted | § 11, § 11c | — |
| 9 | Observability | OTel + App Insights, day one | defaulted | § 12 | — |
| 10 | Data residency | region-pinned · retention | provided \| open-question | § 11f | Step 1.5 posture |

`source` taxonomy (same as SPEC § 13): `provided` (operator stated it) ·
`inferred` (from the brief / trait matrix) · `defaulted` (house default, not
raised) · `defaulted-after-skip` (Fast-PoC) · `open-question` (acknowledged,
unresolved).

---

## 1. Framework & runtime shape

> Resolve the tuple from [`runtime-policy.json`](runtime-policy.json) (the
> sibling contract file in this same `references/` folder) and record the
> **schema id + resolved `policy_route`** below — do not paste a filesystem
> path to this skill's `references/` folder into the customer project; that
> path won't exist there and the link would be broken.

```yaml
schema: threadlight.runtime-policy/v1   # runtime-policy contract this record was resolved against
framework: github-copilot-sdk           # resolved via the design skill's runtime-policy route (schema above)
runtime_shape: agent                    # agent (default) | workflow
protocol: invocations                   # invocations (default) | responses
policy_route: default-agent             # explicit-supported-choice | deterministic-workflow | maf-agent-capabilities | default-agent
  # Canonical default tuple: github-copilot-sdk + agent + invocations (policy_route: default-agent).
  # Explicit operator overrides must match a compatible_combinations tuple AND
  # be free of that route's blocked_when capability signals.
  # MAF exceptions: deterministic-workflow →
  # microsoft-agent-framework + workflow + responses;
  # maf-agent-capabilities → microsoft-agent-framework + agent + responses.
  # Source of truth for the agent-vs-workflow shape is the runtime probe /
  # SPEC § 11e. The Step 2 trait matrix auto-suggests `workflow` for
  # deterministic multi-phase processes with persona gates; the operator
  # confirms or overrides at the Step 4 checkpoint. Do NOT re-litigate here —
  # record the current choice and let discovery refine it.
```

→ **Pre-populates** SPEC § 11e (`workflow_model`, from `runtime_shape`) only.
`framework`, `protocol`, and `policy_route` stay in `specs/foundation.md` —
they are **not** emitted into SPEC § 12 or any other section; SPEC's
`workflow_model` / capability signals are read back only to confirm the
resolved `policy_route` still matches, not to duplicate the selector.

---

## 2. Model & capacity

```yaml
model:
  default: gpt-5.4                       # (2026-03-05) — default for 7+ skill pilots
  version: "2026-03-05"
  region: <primary-region>              # e.g. swedencentral, eastus2
  fallback_region: <secondary-region>   # capacity / data-boundary fallback
  capacity_type: GlobalStandard         # GlobalStandard | ProvisionedThroughput (PTU)
  capacity_tpm: 50K                      # GlobalStandard for gpt-5.4; 120K for -mini
  data_boundary: none                    # none | eu — EU Data Boundary requirement
  reasoning_effort: medium               # minimal | low | medium | high
```

→ **Pre-populates** SPEC § 7b. **`region`, `fallback_region`, `capacity_type`,
and `data_boundary` are net-new** — § 7b captures model + version + TPM but not
the region / boundary / fallback triad, which decides where capacity is
provisioned and whether an EU-resident pilot can even run in the primary region.
Do **not** use the legacy `GPT-4o` family — `gpt-5.4` supersedes it.

> **How to choose these values** — model tier, reasoning effort, capacity type,
> TPM, region/fallback, and data boundary — see
> [`references/model-selection.md`](model-selection.md), the seven-decision
> procedure that fills this block.

---

## 3. Hosting shape

```yaml
hosting: aca-hosted-agent               # aca-hosted-agent | azure-functions | aca-job
deployment_target: demo-sandbox         # demo-sandbox | customer-pilot | production-bound
```

→ **Pre-populates** SPEC § 11c (tech stack) and the § 11f `deployment_target`
lever. `deployment_target` chains into `threadlight-deploy` Phase 1.5.

---

## 4. Tools & data

```yaml
tools:
  binding: mcp                          # mcp | native | mixed
  toolbox: <curated tool set>           # versioned alongside skills (see foundry-toolbox)
  mock_first: true                      # inaccessible systems mocked via FastMCP + sample data
```

→ **Pre-populates** SPEC § 5b (External Systems & Mocks) and § 6 (Tool
Contracts). Every PoC ships at least one callable (mock or real) tool.

---

## 5. Identity & RBAC

```yaml
identity:
  principal: user-assigned-managed-identity   # secretless; DefaultAzureCredential end-to-end
  rbac_posture: least-privilege               # scoped role assignments, no wildcards
  secrets: none                                # no API keys in code or env
```

→ **Pre-populates** SPEC § 11 (access control) and § 11c (the always-created
shared UAMI). Managed identity end-to-end is the default; any deviation is a
flag.

---

## 6. Observability baseline

```yaml
observability:
  otel: on                              # OpenTelemetry wired from day one
  sink: application-insights            # traces + metrics + logs
  trace_convention: gen_ai.*            # semantic-convention spans for agent turns / tool calls
```

→ **Pre-populates** SPEC § 12 and the `foundry-observability` wiring the deploy
leg builds on. Observability is not a post-hoc add — it is a foundation.

---

## 7. Data residency & compliance

```yaml
residency:
  region_pinning: <region>              # pin resources to a compliance region
  retention: 90d                        # 90d | regulated-7y | customer-defined
  deferred_decisions:
    - waf-front-door                    # acknowledged, out of pilot scope
    - dr-runbook
```

→ **Pre-populates** SPEC § 11f overrides (`retention`, networking) and its
`deferred_decisions` list. `threadlight-deploy` surfaces deferred rows as
`<!-- TODO(posture): ... -->` in `main.bicep`.

---

## Rationale (prose, 3–6 lines)

A short paragraph the operator can read in a review: what the pilot is, why the
framework / model / hosting choices fit it, and any decision that deviates from
the house default and why. This is the human-readable audit trail — the YAML
blocks above are the machine contract.
