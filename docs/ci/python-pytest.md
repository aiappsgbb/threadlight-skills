# `python-pytest.yml` — cheap pre-merge gate

> Operator guide for the [`python-pytest`](../../.github/workflows/python-pytest.yml)
> GitHub Actions workflow. Complements the expensive
> [`threadlight-e2e-foundry.yml`](./threadlight-e2e.md) by giving every
> PR a fast feedback loop.

## What this workflow does

Runs the python unit test suites under `skills/*/tests/` on every PR
against `main` and every push to `main`. No Azure, no model tokens, no
external network — pure pytest against the in-tree code.

Coverage as of v1.3.0:

| Skill | Tests | Notes |
|---|---|---|
| `threadlight-consumption-iq` | 125 | Includes a deterministic golden-file e2e against `references/fixtures/sample-pilot-consumption/expected/`. Uses a mock pricing client (no network) and pins `generated_at` so output is reproducible. |
| `threadlight-production-ready` | ~150 | Includes new `tests/test_cost_006.py` covering COST-005 freshness check + COST-006 recommendation walker. 2 pre-existing `test_end_to_end.py` failures (stale safe-check fixture > 24h) are tracked separately. |
| `threadlight-cicd` | 35 | Renders GitHub Actions + Azure DevOps pipelines and env-setup runbooks (UAMI/federated creds, RBAC, private-VNet runners) from flags/framing. Deterministic template rendering — no Azure calls. Asserts OIDC/WIF only (no client secret ever emitted) and resolves the onboarding-path gate (standalone / spoke / hub-handoff). |
| `threadlight-auto` | ~5 | Orchestrator state machine; includes a new assertion that `cost_projection` sits between `safe_check` and `invoke` in `STAGES`. |

## When it fires

- Every pull request to `main` (auto-cancel on new push to the PR branch via Actions' default behaviour).
- Every push to `main` (post-merge sanity check).
- Manual via `gh workflow run python-pytest.yml`.

## Failure triage

| Failure | Cause | Action |
|---|---|---|
| `tests/test_e2e.py::test_e2e_full_pipeline_matches_golden_*` | Pipeline output drifted from the golden fixture. | If the drift is intentional (e.g. you changed a projector formula), regenerate locally: `CONSUMPTION_IQ_REGENERATE_GOLDEN=1 python3 -m pytest skills/threadlight-consumption-iq/tests/test_e2e.py -v`. Then `git diff` to review the new golden and commit it with your change. |
| `tests/test_projector_*.py` failures | A projector regression. | Read the failing assertion; the formulas live in `skills/threadlight-consumption-iq/references/consumption-formulas.md`. |
| `tests/test_cost_006.py` failures | COST-005/006 contract drift in `production_ready.py`. | The cost manifest schema lives in `skills/threadlight-consumption-iq/references/cost-manifest-schema.md` — production-ready must consume it consistently. |
| `tests/test_threadlight_auto_orchestrator.py` failures | Stage list drift. | Confirm `cost_projection` still appears between `safe_check` and `invoke` in `STAGES`. |
| `test_end_to_end.py::test_e2e_handoff_path_*` failures | Pre-existing — stale safe-check fixture older than 24h. | Out of scope for this gate; tracked separately. Refresh the fixture if blocking. |

## Why not extend `threadlight-e2e-foundry.yml`?

The Foundry e2e is `workflow_dispatch` only and costs ~$1/run in Azure + tokens. Mixing cheap unit-test feedback with expensive e2e would either delay every PR by ~50 minutes (if we made it gating) or hide unit failures behind the dispatch button (if we kept the gating model). Two workflows = right tool for each job.
