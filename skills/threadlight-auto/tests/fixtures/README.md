# `threadlight-auto` orchestrator fixtures

Exercise the 4 canonical decision-tree paths of `references/orchestrator.py`.

| Fixture | Expected next_action |
|---|---|
| `blank/`        | `run` all 5 stages |
| `all-complete/` | `run` 0 stages (all skip) |
| `hard-stop/`    | `hard_stop` at `design` (NEEDS CLARIFICATION) |
| `spec-edited/`  | `run` from `design` onward (cascade after hash mismatch) |

Consumed by `tests/test_threadlight_auto_orchestrator.py`.
