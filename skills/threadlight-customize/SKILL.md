---
name: threadlight-customize
description: >-
  Use when an SE or a customer's platform team needs to FORK the Threadlight
  pipeline and adapt it to one customer's environment — landing zones,
  identity, RBAC, deploy pipelines, governance — with PRODUCTION ONBOARDING as
  priority #1. An instructions/runbooks skill (not automation): intake gate,
  customization map (keep vs per-customer-override),
  test-in-customer-environment runbook for private-VNet envs, and a
  fork-runbook (upstream-pin + overlay merge). USE FOR: fork threadlight,
  customize threadlight, customer onboarding, production onboarding, onboard a
  customer, adapt the pipeline, tailor threadlight, customer landing zone,
  customer RBAC, customization map, what to fork vs keep, upstream pin,
  overlay merge, test in customer environment. DO NOT USE FOR: generating the
  prod deploy pipeline (use threadlight-cicd); the readiness scorecard (use
  threadlight-production-ready); the first-run deploy (use
  threadlight-deploy); a central hub (use citadel-spoke-onboarding /
  citadel-hub-deploy).
metadata:
  version: "0.1.0"
---

# Threadlight Customize — fork the pipeline, onboard a real customer

> The final leg. It answers "**how do I take Threadlight and stand it up
> inside *this* customer — their landing zones, their RBAC, their pipelines,
> their governance — and actually test it on *their* environment?**" It does
> this with **instructions and fill-in runbooks, not automation.** Production
> onboarding is too high-variance to encode; this skill gives you the frame,
> the intake, and the customer-env test loop, then gets out of the way.

## Why this skill exists (and why it is not a generator)

Every other Threadlight skill is opinionated and deterministic because its
inputs are bounded (a SPEC section, a set of selectors, an `azd env`). The
**production onboarding of a specific customer is not bounded.** Their
landing-zone topology, identity model, allowed regions, private DNS, egress
rules, mandated IaC modules, change-management gates, and approval chains are
all theirs — and no two are the same. Trying to cover every permutation in
code produces a generator that is wrong for everyone.

So this skill is **prose + templates**. It captures what you must learn from
the customer, tells you which Threadlight skills to fork-and-override vs keep
as-is, and shows you how to run the dev/test loop **inside the customer's
boundary** (the part the field kept getting stuck on). What it deliberately
does **not** do is generate the onboarding for you. See
[`references/non-coverage.md`](references/non-coverage.md) — that honesty is a
feature.

## Where it sits in the pipeline

```
… → threadlight-production-ready (advisory gate) →
    threadlight-cicd (prod-deploy pipeline, locked-down env) →
    threadlight-customize  ◄── you are here (fork + customer onboarding)
```

It is a **manual handoff leg**, like `threadlight-cicd`. **`threadlight-auto`
does not drive it** — `auto` is a pilot driver, not a customer-onboarding
orchestrator. Run it when the pilot has proven out and the conversation turns
to "now make this live inside *our* environment." You can also start the
**intake gate (Move 1) early**, in parallel with the pilot, because the
intake is the long pole.

## Audience

Written for **two operators**, SE-led path first:

- **SE / GBB (primary).** You fork Threadlight, run the intake with the
  customer, customize the production-onboarding leg, and run the test loop
  inside their environment.
- **Customer platform / engineering team (secondary).** Everything here is
  also a self-serve runbook a customer team can follow to adopt Threadlight
  internally. Where the two paths differ, the step says so.

## The four moves

Run them in order. Each move produces one durable artifact under
`docs/threadlight-customize/` so the engagement is auditable and resumable.

### Move 1 — Intake gate (the long pole)

**You cannot customize what you have not been given.** The field's #1 blocker
was getting the customer inputs, not writing code. Open the engagement by
filling in the intake workbook **with** the customer:

- Copy [`references/customer-profile.md.tmpl`](references/customer-profile.md.tmpl)
  to `docs/threadlight-customize/customer-profile.md`.
- Work it top to bottom with the customer's cloud/platform owner. It has four
  blocks: **customer documents**, **environment setup**, **requirements**, and
  **mandated template/starter code**.
- Treat every unfilled field as a **blocker**, not a TODO. A blank
  "private DNS zones" or "allowed regions" line will stop the deploy later;
  surface it now.

The workbook is the single source of truth every later move reads. Do not
proceed to Move 2 until the **environment setup** and **mandated template
code** blocks are complete — those two drive every override decision.

### Move 2 — Customization map (what to fork vs keep)

Decide, per skill, what you change and what you leave alone. Read
[`references/customization-map.md`](references/customization-map.md): it
classifies every Threadlight skill as **customer-agnostic (keep as-is)** or
**needs per-customer override**, and names the exact SPEC section / selector /
`azd env` hook to change.

**Production-onboarding skills are priority #1** — they are where the
customer's environment actually bites:

| Skill | Typical customer override |
|---|---|
| `threadlight-deploy` | landing-zone targeting (sub/RG), resource selectors, region allow-list, private-endpoint + private-DNS wiring |
| `threadlight-safe-check` | customer resource selectors + naming standard the gate validates against |
| `threadlight-cicd` | pipeline shape, federated identity model, scoped RBAC, runner topology (this skill *generates* it; you feed it the customer's constraints) |
| `threadlight-production-ready` | pillar thresholds + `customer_overrides` to match the customer's policy baseline |

Capture the decisions in `docs/threadlight-customize/customization-map.md`
(copy the table from the reference and fill the right-hand column). Keep your
overrides in an **overlay**, not by editing forked skills in place — see the
fork runbook below — so you can still pull upstream Threadlight updates.

### Move 3 — Test it on the customer's environment

The pilot proves the agent works on an SE sandbox. Production onboarding only
proves out when you can run the loop **inside the customer's boundary** —
often a **fully-private (private-VNet) environment** with no public egress.
This is the move the field repeatedly got stuck on; two patterns work:

- **GitHub Codespaces** (private networking) —
  [`references/private-env-test/codespaces.md`](references/private-env-test/codespaces.md).
- **Azure ML compute instance + VS Code (Remote)** —
  [`references/private-env-test/azure-ml-vscode.md`](references/private-env-test/azure-ml-vscode.md).

Before you run the Threadlight `local-test` / `deploy` loop from inside the
boundary, work the pre-flight in
[`references/private-env-test/private-vnet-checklist.md`](references/private-env-test/private-vnet-checklist.md):
private DNS resolution, egress posture, and reachability of the Foundry,
ACR, Cosmos, and Key Vault **private endpoints**. A green pre-flight is the
gate to running deploy — a half-resolved private DNS zone fails the deploy in
a way that looks like an auth error and burns an afternoon.

### Move 4 — Name the boundary (what we do NOT automate)

Close the engagement honestly. Fill in
[`references/non-coverage.md`](references/non-coverage.md): the **seams you
customized** (so the next SE can read the diff) and what Threadlight
**deliberately does not automate** for this customer, plus a short decision
log. This keeps expectations calibrated for the customer's CISO/architecture
review and stops the next engagement from assuming the onboarding is turnkey.

## Fork mechanics (do this once, up front)

Fork Threadlight the way that survives upstream updates. The full runbook is
[`references/fork-runbook.md`](references/fork-runbook.md); the shape:

1. **Fork** the `threadlight-skills` repo (or install the plugin and overlay
   alongside it) into the customer's org / your engagement repo.
2. **Pin upstream.** Record the upstream commit you forked from — a lightweight
   fork-pin analogous in spirit to the `references/upstream-pin.md` freshness
   records that `threadlight-deploy` and `threadlight-local-test` keep for the
   sources they vendor.
3. **Overlay, don't fork-edit.** Keep customer overrides (selectors, env
   hooks, thresholds, mandated IaC) in a separate overlay directory layered
   over the pinned upstream, so a later `git merge` of upstream Threadlight
   does not clobber the customer's customizations.

## Quick reference

| Move | Read | Produces |
|---|---|---|
| 0 — fork | `references/fork-runbook.md` | forked repo + `upstream-pin.md` |
| 1 — intake | `references/customer-profile.md.tmpl` | `docs/threadlight-customize/customer-profile.md` |
| 2 — map | `references/customization-map.md` | `docs/threadlight-customize/customization-map.md` |
| 3 — test | `references/private-env-test/*` | a green private-VNet pre-flight + a test run inside the customer env |
| 4 — boundary | `references/non-coverage.md` | `docs/threadlight-customize/non-coverage.md` |

## Common mistakes

- **Treating intake as a formality.** The intake gate is the engagement's
  critical path. A blank field in the customer profile is a future blocked
  deploy. Fill it before you write a single override.
- **Editing forked skills in place.** You will want upstream fixes later;
  in-place edits make the merge a nightmare. Use the overlay.
- **Testing only on the SE sandbox.** A pilot that passes on your sandbox and
  has never run inside the customer's private VNet is not onboarded. Move 3 is
  not optional.
- **Pretending the onboarding is automated.** It is not, by design. If you
  find yourself writing a generator that branches on the customer's landing
  zone, stop — that is the seam this skill exists to keep as instructions.
- **Letting `threadlight-auto` drive this.** It is a manual leg. `auto` stops
  at the pilot; the customer onboarding is a human-led handoff.

## References

- [`references/customer-profile.md.tmpl`](references/customer-profile.md.tmpl)
  — the Move 1 intake workbook (documents, env setup, requirements, mandated
  template code).
- [`references/customization-map.md`](references/customization-map.md) — the
  Move 2 fork-vs-keep classification, production-onboarding skills flagged
  priority.
- [`references/private-env-test/`](references/private-env-test/) — the Move 3
  runbooks: `codespaces.md`, `azure-ml-vscode.md`, `private-vnet-checklist.md`.
- [`references/non-coverage.md`](references/non-coverage.md) — the Move 4
  boundary + decision-log template.
- [`references/fork-runbook.md`](references/fork-runbook.md) — fork +
  upstream-pin + overlay-merge mechanics.
- [`references/field-notes-telco-pilot.md`](references/field-notes-telco-pilot.md)
  — anonymized learnings from a large telco AI pilot that shaped this leg.
