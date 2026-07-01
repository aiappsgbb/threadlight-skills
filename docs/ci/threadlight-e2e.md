# `threadlight-e2e-foundry.yml` — operator runbook

> Operator guide for the [`threadlight-e2e-foundry`](../../.github/workflows/threadlight-e2e-foundry.yml)
> GitHub Actions workflow. **One-time setup** lives in
> [`threadlight-e2e-setup.md`](./threadlight-e2e-setup.md) — do that first.
>
> The retry-on-429 wrapper + git-suppress prompt are the load-bearing
> reliability pieces here — they're what keeps a ~51-minute, 16-step run
> green through transient 429s and clean teardown.

## What this workflow does

Drives the full threadlight pipeline — `threadlight-design` → `threadlight-deploy` (incl. `azd up`) → `threadlight-safe-check phase=post-deploy` → live agent invoke → **control-plane legs** (`threadlight-govern` + `threadlight-evals` + `threadlight-redteam` against the deployed pilot, then `threadlight-production-ready` rendering the outcome-KPI scorecard that joins them) — in a clean Actions runner, with model calls routed to a Foundry account via BYOK. The leg phase is workflow-owned + deterministic and report-only for verdicts (the workshop scenario legitimately surfaces governance/eval/safety gaps); its hard gate is that each leg executable runs end-to-end and emits its `specs/*-manifest.json`. The offline counterpart that asserts a *fully green + joined* control plane is [`test_e2e_control_plane.py`](../../skills/threadlight-auto/tests/test_e2e_control_plane.py). Deployed Azure resources land in a per-run RG (`rg-threadlight-e2e-<run_id>`) and are torn down via `azd down --force --purge` in an `if: always()` step.

## When to fire it

| Reason | Recommended? |
|---|---|
| Validate a `threadlight-deploy` SKILL change before merging | ✅ Always |
| Validate any threadlight-* SKILL change that affects design/deploy/safe-check | ✅ Always |
| Validate an upstream Foundry SDK / azd extension bump | ✅ Yes |
| Routine "is everything still working" check | ⚠️ Only if budget allows — $0.50-1/run + ~$0.50 model tokens |

## How to fire it

```bash
gh workflow run threadlight-e2e-foundry.yml \
  --repo aiappsgbb/threadlight-skills \
  --ref main \
  -f scenario=auto-claim-triage \
  -f region=westus3 \
  -f teardown=true
```

…or via the GitHub UI:

1. Open <https://github.com/aiappsgbb/threadlight-skills/actions/workflows/threadlight-e2e-foundry.yml>
2. Click **Run workflow**
3. Pick inputs:
   - **scenario** — `auto-claim-triage` (default) | `credit-memo` | `prior-auth-healthcare`
   - **region** — `westus3` (default). If quota is tight, try `eastus2` or `northcentralus`
   - **teardown** — `true` (default). Set to `false` ONLY when you want to debug post-run — you MUST then manually `az group delete --name rg-threadlight-e2e-<run_id> --yes`

## Expected wallclock

Baseline from full end-to-end runs (~51 min wallclock, ~47 min agent work):

| Stage | Expected | Worst-case |
|---|---|---|
| `actions/checkout` + Node 22 + Copilot CLI install | < 1 min | 2 min |
| Azure login OIDC + bearer token | ~10 s | 30 s |
| azd + Bicep + azure.ai.agents extension install | ~1 min | 2 min |
| Workspace prep + tenant shim | < 5 s | — |
| `threadlight-design` (SPEC.md + agents + demo scenarios) | ~5-10 min | 15 min |
| `threadlight-deploy` (azd up, all phases) | ~15-25 min | 30 min |
| `threadlight-safe-check phase=post-deploy` | ~1-2 min | 5 min |
| Live agent invoke (2+ demo scenarios) | ~1 min | 3 min |
| Teardown (`azd down --force --purge`) | ~10-15 min | 20 min |
| **TOTAL** | **~35-55 min** | **~60 min** (`timeout-minutes: 60`) |

## Cost per run

Based on measured runs (re-estimated lower than the planning $5-10):

| Component | Active during run |
|---|---|
| ACR Basic + LAW + AppIn + ACA Consumption + Foundry account | ~$0.20 |
| `gpt-5.4-mini` tokens (~10-15M cached, ~70k generated) | ~$0.30-0.60 |
| **TOTAL per run** | **~$0.50-1** Azure-side + token cost |

## Reading the artifact

After the run, an artifact `threadlight-e2e-<run_id>` is uploaded with:

```
specs/                      # SPEC.md + manifest.json from threadlight-design
docs/                       # any docs threadlight produces (seller prep, plan, etc.)
/tmp/threadlight-e2e.log    # full Copilot CLI session transcript
/tmp/copilot-logs/          # Copilot CLI internal logs
/tmp/resource-snapshot.txt  # az resource list snapshot before teardown
/tmp/azd-down.log           # azd down output (if teardown ran)
```

Most useful when triaging:

- **Driver session crashed** — `/tmp/threadlight-e2e.log` (Copilot transcript)
- **azd provision failed** — Same log, search for "azd provision" + " ERROR"
- **Agent never reached active** — `/tmp/resource-snapshot.txt` + Foundry portal blade
- **Teardown failed** — `/tmp/azd-down.log` + manual fallback `az group delete --name rg-threadlight-e2e-<run_id> --yes`

## Common failures + fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `AADSTS70016` / `AADSTS500011` / `AADSTS50020` at `azure/login` step | Federated credential missing for this repo/branch | Re-run [`threadlight-e2e-setup.md`](./threadlight-e2e-setup.md) federated-credential commands |
| `401` from Foundry on first model call | `AZURE_AI_ENDPOINT` secret wrong or UAMI lacks `Cognitive Services OpenAI User` on the Foundry account | `az role assignment list --assignee <UAMI-principal-id> --scope <foundry-account-id>`; re-grant if missing |
| `Authorization failed for ... Microsoft.Authorization/roleAssignments` during Bicep | UAMI missing `User Access Administrator` at sub scope | Grant at sub scope (Bicep RBAC blocks need it) |
| `InsufficientQuota for "gpt-5.4-mini"` across all probed regions | Real exhaustion across westus3 + eastus2 + northcentralus | Request quota increase, or wait for other CI runs to drain |
| Copilot CLI session ends quickly with `CAPIError: Too Many Requests` | Foundry deployment is 429-throttled (transient) | The retry wrapper handles up to 3 attempts automatically; if all 3 fail, refire with `-f region=eastus2` or wait 30 min |
| `azd down` fails with "resources still referenced" | Foundry capability host / private endpoint not fully detached | Fallback `az group delete --no-wait` step kicks in; check the next morning that the RG actually deleted |
| Run took > 60 min and timed out | LAW + Foundry purge can be slow | Per-run RG will still get the fallback async delete; safe to ignore the timeout unless it recurs |

## When NOT to fire it

- `workflow_dispatch` only — will NEVER auto-fire on push / PR. Intentional.
- Don't fire while another threadlight-e2e run is in flight (no collision risk thanks to per-run RG name, but you'll pay double)
- Don't fire if the shared Foundry account is being modified by someone else (capacity / model deployment changes)

## Cross-refs

- [`self-improving-loop.md`](./self-improving-loop.md) — the **primary** `learn` cold-path that mines any single run of this workflow (green or red, no baseline) into ranked fixes
- [`router-validation.md`](./router-validation.md) — the **optional** model-router vs `gpt-5.4-mini` validation matrix (quality + cost) driven by this workflow
- `skills/threadlight-deploy/SKILL.md` § Deploy-time failure-mode index — F-01..F-22 lookup table
- aiappsgbb/awesome-gbb's `copilot-cli-foundry-auth-smoke.yml` — the auth-smoke that proved BYOK Foundry routing
