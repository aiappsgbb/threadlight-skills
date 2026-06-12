# Expected: cost-projection.md (golden file for sample-pilot-consumption)

> **Placeholder.** Filled in by the emitter golden-file test once
> projector math lands. See plan.md todo `e2e` and `fixtures`.

This file is intentionally short until the projectors and emitter
produce real numbers. Once they do, this file becomes the diff target
for `tests/test_emitter.py`.

The expected shape (once real):

```
# Cost projection

> Generated <ISO timestamp> against deploy sample-pilot-consumption/<id>.

## Totals

- Monthly cost (current): $<X>
- Monthly cost (recommended): $<Y>
- Monthly savings potential: $<X-Y>

## Per-resource breakdown

### Microsoft.CognitiveServices/accounts/deployments — gpt4o (eastus2)
| Variant | Monthly cost | Δ vs current | Notes |
| --- | --- | --- | --- |
| PAYG (current) | $<…> | — | |
| PTU @ 25 units | $<…> | <…> | |
…

## Recommendations

| Resource | Current SKU | Recommended SKU | Monthly savings | Priority |
| --- | --- | --- | --- | --- |
…

```mermaid
pie title Monthly cost share
  "AOAI" : <…>
  "ACA"  : <…>
  …
```
```
