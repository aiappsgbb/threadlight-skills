# Continuous evals — manifest report

> Verdict: **COMPREHENSIVE** · freshness `3650d` · captured 2026-06-22T16:11:48+00:00

| Capability | Pillar ID | Status | Evidence / hint |
|---|---|---|---|
| `eval_scenarios_present` | `EVAL-001` | ✅ pass | 1 scenario marker(s); first: specs/spec.md |
| `eval_datasets_present` | `EVAL-002` | ✅ pass | evals/dataset.jsonl |
| `dataset_shape_ok` | `EVAL-003` | ✅ pass | evals/dataset.jsonl |
| `thresholds_declared` | `EVAL-004` | ✅ pass | evals/continuous-eval.yaml |
| `schedule_present` | `EVAL-005` | ✅ pass | Plan A: evals/continuous-eval.yaml |
| `run_history_present` | `EVAL-006` | ✅ pass | evals/runs/2026-01-01.json |
| `online_eval_wired` | `EVAL-101` | ✅ pass | src/continuous_eval.py |
| `latest_eval_run_fresh` | `EVAL-102/EVAL-103` | ✅ pass | evals/runs/2026-01-01.json (172.7d old <= 3650d) |
| `alert_wired` | `EVAL-104` | ✅ pass | infra/eval-alert.bicep |
| `latest_pass_rate_ok` | `EVAL-105` | ✅ pass | pass_rate=0.91 >= threshold=0.85 |
| `ab_comparison_present` | `F3` | ✅ pass | evals/ab/ |

Consumed by `threadlight-production-ready` pillar 6 (`continuous-evals`).
