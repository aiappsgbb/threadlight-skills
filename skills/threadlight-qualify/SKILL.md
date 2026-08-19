---
name: threadlight-qualify
description: >-
  Cowork-safe pre-sales qualification and sizing for Threadlight / Azure AI
  agent pilots. Turns a declared interview (no Azure, Bicep, az, azd, Docker, or
  customer credentials) into a deterministic sizing package — sizing.md,
  sizing-manifest.json, discovery.md, and an optional roi.md. Derives monthly
  volumes, builds MVP and production profiles, and projects both through the
  shared cost engine with explicit meter coverage and PTU break-even; Citadel
  hub sizing stays separate from per-application sizing, and every assumption
  carries a provenance. USE FOR: qualify a pilot, size an agent workload,
  pre-sales sizing, sizing manifest, cost per transaction, MVP vs production
  sizing, ROI estimate, Citadel hub sizing, discovery notes, seed SPEC section
  12. DO NOT USE FOR: post-deploy cost projection from a live deployment (use
  threadlight-consumption-iq); production-readiness gating (use
  threadlight-production-ready); Bicep/infra changes or deployment (use
  threadlight-deploy).
metadata:
  version: "0.1.0"
---

# threadlight-qualify

Deterministic, **no-discovery** qualification & sizing for an Azure AI agent
pilot. It runs from a declared interview only — it never touches Azure, Bicep,
`az`, `azd`, Docker, or customer credentials, so it is safe to run in Microsoft
Copilot Cowork.

## When to use

Reach for this at the **pre-sales / qualification** stage, before any repo or
deployment exists, to answer: *what will this cost, at what volume, and is the
ROI there?* The output seeds SPEC § 12 for `threadlight-design`.

## Inputs (the interview)

Collect these required fields into a profile object (see
`references/fixtures/sample-qualification/profile.json`):

- `customer_brief`, `workload_class`
- `annual_transaction_volume`, `transaction_unit`
- `pages_per_transaction`, `document_origin`
- `turns_per_conversation`, `tokens_per_turn_estimate`
- `peak_concurrency`, `business_hours_only`
- `sites_or_entities`, `data_residency`, `pinned_region`

Optional (both required to unlock `roi.md`):
`current_annual_cost_usd`, `current_handling_minutes_per_transaction`.

## How to run

Call the Python entry point (no shell tooling, no infrastructure required):

```
python3 scripts/qualify.py \
  --profile <profile.json> \
  --output-dir <dir> \
  --generated-at 2026-06-12T12:00:00+00:00   # pin for deterministic bytes
```

Or import it:

```python
from qualify import run_qualification
run_qualification(profile, output_dir=Path("out"), generated_at="2026-06-12T12:00:00+00:00")
```

## Outputs

Written under `<output-dir>/qualification/`:

| File | When | Contents |
| --- | --- | --- |
| `sizing.md` | always | Human-readable MVP + production sizing, hub sizing, assumptions |
| `sizing-manifest.json` | always | Normalized load profile, per-stage cost manifests, assumptions ledger |
| `discovery.md` | always | Declared inputs + open questions (no live probe was run) |
| `roi.md` | only when both current-cost inputs are supplied | Labor + solution-cost ROI |

## Contracts

- **Validation first.** A missing/invalid required field raises
  `QualificationError` and writes **nothing**.
- **Incomplete totals never lie.** A not-priceable or unverified line makes the
  cost manifest `partial`, `totals.complete=false`, and suppresses
  cost-per-transaction. No unknown is summed as zero.
- **Provenance everywhere.** Every assumption is `user-supplied`, `derived`,
  `fixture`, or `live`.
- **Hub ≠ app.** Citadel hub sizing (`kind: citadel-hub`, estate-billed) is kept
  separate from application sizing (`kind: threadlight-application`).
- **Deterministic.** A pinned `generated_at` produces byte-identical
  `sizing-manifest.json`.

## Seeding SPEC § 12

`threadlight-design` reads `qualification/sizing-manifest.json` and seeds SPEC
§ 12 `load_profile{}` from its normalized load profile instead of re-running the
interview.
