# sample-qualification fixture

Declared interview profiles for `threadlight-qualify`. No live discovery is
performed — these are the *inputs* a seller/SE collects in a qualification call.

- `profile.json` — the minimum required interview fields (no ROI inputs).
- `profile-with-roi.json` — adds the two optional current-cost inputs
  (`current_annual_cost_usd`, `current_handling_minutes_per_transaction`) that
  unlock `roi.md`.

Run:

```
python3 skills/threadlight-qualify/scripts/qualify.py \
  --profile skills/threadlight-qualify/references/fixtures/sample-qualification/profile.json \
  --output-dir ./out \
  --generated-at 2026-06-12T12:00:00+00:00
```
