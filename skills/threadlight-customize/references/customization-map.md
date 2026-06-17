# Customization map — what to fork vs keep

> **Move 2 of `threadlight-customize`.** For every Threadlight skill, decide
> whether you **keep it as-is** (customer-agnostic) or **override it** for this
> customer. Copy the table below into
> `docs/threadlight-customize/customization-map.md` and fill the **Decision**
> and **Override detail** columns from the customer profile (Move 1). Keep
> overrides in an **overlay**, not by editing forked skills in place — see
> `fork-runbook.md`.

## How to classify

- **Keep** — the skill's behavior is the same for every customer (its inputs
  are bounded by a SPEC section or generated artifact). Pull upstream updates
  freely.
- **Override** — the skill's behavior depends on *this* customer's environment
  (Block B) or mandated code (Block D). Record the exact SPEC §, selector, or
  `azd env` hook you change, and put the change in the overlay.

## Priority #1 — production-onboarding skills

These are where the customer's environment actually bites. Expect to override
all four; they are the reason this leg exists.

| Skill | Default behavior | Typical override (from customer profile) | Decision | Override detail |
|---|---|---|---|---|
| `threadlight-deploy` | `azd up` into SE sandbox; selectors from `specs/manifest.json` | landing-zone targeting (sub/RG), region allow-list, private-endpoint + private-DNS wiring, mandated IaC modules (Block D) | _<keep / override>_ | _<…>_ |
| `threadlight-safe-check` | validates resource selectors pre/post deploy | customer naming standard + selectors the gate asserts | _<keep / override>_ | _<…>_ |
| `threadlight-cicd` | generates OIDC/WIF prod pipeline + env-setup runbooks | feed it the customer identity model, RBAC scope, runner topology (Block B). **This skill generates the pipeline — you supply constraints, you do not re-implement it here.** | _<keep / override>_ | _<…>_ |
| `threadlight-production-ready` | 13-pillar advisory scorecard with default thresholds | `customer_overrides` to match the customer's policy baseline; pillar thresholds | _<keep / override>_ | _<…>_ |

## The rest of the pipeline

| Skill | Default behavior | When you override | Decision | Override detail |
|---|---|---|---|---|
| `threadlight-design` | brief → `specs/SPEC.md` + agent surface | rarely; only if the customer mandates a different SPEC shape | _<keep / override>_ | _<…>_ |
| `threadlight-demo-data-factory` | synthetic seed data | swap to customer-representative (still synthetic) data classes | _<keep / override>_ | _<…>_ |
| `threadlight-local-test` | Pattern 0 local boot | point at the customer-env test loop (Move 3) instead of localhost | _<keep / override>_ | _<…>_ |
| `threadlight-consumption-iq` | cost projection from Bicep + retail prices | customer-negotiated rates / reserved capacity / PTU | _<keep / override>_ | _<…>_ |
| `threadlight-event-triggers` | ACA Jobs / Event Grid / cron | customer event sources, private networking on triggers | _<keep / override>_ | _<…>_ |
| `threadlight-hitl-patterns` | Teams Adaptive Card gates | customer approval channel / identity | _<keep / override>_ | _<…>_ |
| `threadlight-workspace-ui` | operator dashboard behind Easy Auth | customer SSO / branding / private access | _<keep / override>_ | _<…>_ |
| `threadlight-auto` | one-prompt pilot driver | **keep — do not point it at customer prod.** It drives the pilot, not the onboarding | _<keep>_ | n/a |

## Overlay placement

For each **override**, record where the overlay change lives so the next SE can
read the diff:

| Override | Upstream file/selector touched | Overlay location | Survives upstream merge? |
|---|---|---|---|
| _<e.g. deploy → target RG>_ | _<file / azd env key>_ | _<overlay path>_ | _<yes — overlay / no — fork-edit (fix this)>_ |

> Any row that says "no — fork-edit" is technical debt. Move it to the overlay
> before go-live so you can still pull upstream Threadlight fixes.
