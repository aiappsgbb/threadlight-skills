# `threadlight-e2e-foundry.yml` — one-time setup runbook

> Do this **once** before the first run of
> [`threadlight-e2e-foundry.yml`](../../.github/workflows/threadlight-e2e-foundry.yml).
> Operator guide for repeat usage: [`threadlight-e2e.md`](./threadlight-e2e.md).

> **What this workflow tests:** the 1-hour workshop scenario from
> [`docs/WORKSHOP-1H-QUICKSTART.md`](../WORKSHOP-1H-QUICKSTART.md),
> end-to-end:
>
> - **§4.2** `threadlight-design` Fast-PoC for retail returns-triage
> - **§4.3** `threadlight-local-test` Pattern 0 (`--info` + `--check`) —
>   workflow-owned, not in the workshop's critical path; a cheap gate
>   before `azd up` spending
> - **§6.2+§6.3** `threadlight-deploy` → `azd up`
> - **§6.4** Invoke 2 killer prompts against the deployed agent
>
> Each phase runs as a separate step with its own logs and a workflow-
> owned assert; failures are scoped to a single phase, not buried in a
> mega-prompt.

## Option A — reuse the existing `uami-shared-gbb-ci` UAMI (recommended if available)

The agentic-loop + awesome-gbb CI all share a single UAMI (`uami-shared-gbb-ci` in `rg-shared-gbb-ci`, sub `ME-MngEnvMCAP979166-fruocco-2` as of 2026-06-09). If your tenant has access to that sub, the lowest-cost option is to add federated credentials to it for `aiappsgbb/threadlight-skills`.

> [!CAUTION]
> **The shared RG `rg-shared-gbb-ci` MUST carry a `CanNotDelete` management lock from the moment it's created.** A cross-repo `azd down` (e.g. from awesome-gbb CI) authenticated as the shared UAMI deleted the predecessor RG (`rg-awesome-gbb-ci`) twice in the same week in 2026-06; we rebuilt clean on `rg-shared-gbb-ci` on 2026-06-09 with the lock applied at creation. Without the lock, the UAMI has Contributor+UAA at sub scope, so any caller holding its OIDC trust can delete the RG that holds it. Verify the lock exists before assuming this UAMI is safe to reuse:
>
> ```bash
> az lock list --resource-group rg-shared-gbb-ci \
>   --query "[?level=='CanNotDelete'].{name:name, level:level}" -o table
> ```
>
> If the lock is missing, add it FIRST:
>
> ```bash
> az lock create \
>   --name "no-delete-shared-ci" \
>   --resource-group rg-shared-gbb-ci \
>   --lock-type CanNotDelete \
>   --notes "Shared CI RG. Holds the UAMI used by aiappsgbb/threadlight-skills + aiappsgbb/awesome-gbb CI. Removing this RG nukes both repos' CI."
> ```
>
> Lock-removal requires `Microsoft.Authorization/locks/delete` — explicitly NOT in the UAMI's Contributor/UAA grants. Owners can remove it; the shared UAMI cannot.

### Recommended: environment-scoped FIC (one credential covers every branch)

Preferred over per-branch FICs — works for `main`, every feature branch, and every fork-less PR via the same single subject claim. Pair with a `e2e-ci` GitHub Environment whose required-reviewer rule doubles as the per-run cost gate.

```bash
# One-time: create the GitHub Environment (idempotent)
gh api -X PUT repos/aiappsgbb/threadlight-skills/environments/e2e-ci --input - <<'EOF'
{}
EOF

# One-time: create the FIC
az identity federated-credential create \
  --name "fc-threadlight-skills-env-e2e-ci" \
  --identity-name uami-shared-gbb-ci \
  --resource-group rg-shared-gbb-ci \
  --issuer "https://token.actions.githubusercontent.com" \
  --subject "repo:aiappsgbb/threadlight-skills:environment:e2e-ci" \
  --audiences "api://AzureADTokenExchange"
```

The workflow already wires `environment: e2e-ci` on the `e2e` job.

### Per-branch FICs (legacy / fallback)

Only needed if you cannot use the environment-scoped path above. One FIC per branch you `workflow_dispatch` from — Microsoft Entra ID does not support wildcards in the GitHub Actions OIDC `ref` subject.

```bash
az identity federated-credential create \
  --name "fc-threadlight-skills-main" \
  --identity-name uami-shared-gbb-ci \
  --resource-group rg-shared-gbb-ci \
  --issuer "https://token.actions.githubusercontent.com" \
  --subject "repo:aiappsgbb/threadlight-skills:ref:refs/heads/main" \
  --audiences "api://AzureADTokenExchange"
```

Slashes are not allowed in the federated-credential `--name`, so replace them with `-` when targeting feature branches:

```bash
BRANCH=my/feature-branch
az identity federated-credential create \
  --name "fc-threadlight-skills-$(echo $BRANCH | tr / -)" \
  --identity-name uami-shared-gbb-ci \
  --resource-group rg-shared-gbb-ci \
  --issuer "https://token.actions.githubusercontent.com" \
  --subject "repo:aiappsgbb/threadlight-skills:ref:refs/heads/${BRANCH}" \
  --audiences "api://AzureADTokenExchange"
```

The UAMI already has these roles (granted during agentic-loop CI setup) — no extra grants needed:

| Scope | Role |
|---|---|
| `/subscriptions/<sub>` | Contributor + User Access Administrator + Reader |
| Foundry account `aif-shared-gbb-ci` | Cognitive Services OpenAI User + Foundry User |
| ACR `acrawesomegbbci` | AcrPush |
| AppIn `appi-awesome-gbb-ci` | Monitoring Metrics Publisher |

Skip to **Step 3 — GH secrets** below.

## Option B — provision a dedicated UAMI in your own subscription

Use when Option A's sub isn't accessible. Replace `<sub>`, `<rg>`, `<region>`, `<foundry-account>` with your values.

### Prerequisites

- `az` CLI authenticated to the target sub: `az account show` reports the right `tenantId`
- `gh` CLI authenticated to `aiappsgbb/threadlight-skills`: `gh auth status`
- Existing Foundry account with at least one `gpt-5.4-mini` GlobalStandard deployment

### Step 1 — Create RG + UAMI + lock + federated credentials

```bash
SUB=<your-sub-id>
RG=rg-threadlight-ci
LOC=swedencentral
UAMI=uami-threadlight-ci

az group create --name "$RG" --location "$LOC"

# CRITICAL — apply a CanNotDelete lock IMMEDIATELY after creating the RG.
# The UAMI you're about to provision will have Contributor + UAA at sub
# scope, which means anyone holding its OIDC trust (this repo's CI, plus
# any other repo that ever shares this UAMI) can `azd down` this RG and
# nuke its own auth identity. Lock-removal requires
# Microsoft.Authorization/locks/delete which is NOT in Contributor/UAA,
# so this lock holds against the UAMI itself. Owners can lift it; the
# UAMI cannot.
az lock create \
  --name "no-delete-shared-ci" \
  --resource-group "$RG" \
  --lock-type CanNotDelete \
  --notes "Holds UAMI used by aiappsgbb/threadlight-skills CI. DO NOT remove."

az identity create \
  --name "$UAMI" \
  --resource-group "$RG" \
  --location "$LOC"

UAMI_PRINCIPAL=$(az identity show --name "$UAMI" --resource-group "$RG" --query principalId -o tsv)
UAMI_CLIENT_ID=$(az identity show --name "$UAMI" --resource-group "$RG" --query clientId -o tsv)

# Recommended: one environment-scoped FIC instead of per-branch (see Option A
# for rationale). Falls back to per-branch FICs if you skip the Environment.
gh api -X PUT repos/aiappsgbb/threadlight-skills/environments/e2e-ci --input - <<'EOF'
{}
EOF

az identity federated-credential create \
  --name "fc-threadlight-skills-env-e2e-ci" \
  --identity-name "$UAMI" \
  --resource-group "$RG" \
  --issuer "https://token.actions.githubusercontent.com" \
  --subject "repo:aiappsgbb/threadlight-skills:environment:e2e-ci" \
  --audiences "api://AzureADTokenExchange"
```

### Step 2 — Grant required roles

```bash
# Contributor + User Access Administrator at sub scope
# (UAA is mandatory — Bicep RBAC blocks need it; without it,
# threadlight-deploy will fail at the first `azd up`)
for role in Contributor "User Access Administrator"; do
  az role assignment create \
    --assignee "$UAMI_PRINCIPAL" \
    --role "$role" \
    --scope "/subscriptions/${SUB}"
done

# Cognitive Services OpenAI User on the Foundry account
FOUNDRY_ACCT=<your-foundry-account-name>
FOUNDRY_RG=<rg-containing-the-foundry-account>
ACCT_ID=$(az cognitiveservices account show \
  --name "$FOUNDRY_ACCT" \
  --resource-group "$FOUNDRY_RG" \
  --query id -o tsv)

az role assignment create \
  --assignee "$UAMI_PRINCIPAL" \
  --role "Cognitive Services OpenAI User" \
  --scope "$ACCT_ID"

# Foundry User (data-plane access — required since the May 2026 rename)
az role assignment create \
  --assignee "$UAMI_PRINCIPAL" \
  --role "53ca6127-db72-4b80-b1b0-d745d6d5456d" \
  --scope "$ACCT_ID"
```

## Step 3 — Configure GitHub secrets on `aiappsgbb/threadlight-skills`

Whether you came from Option A or B, set these 4 secrets:

```bash
# Use the right UAMI_CLIENT_ID for your option:
#   Option A: query live, the value rotates whenever the shared RG is
#             rebuilt. Last rebuild: 2026-06-09 (after cross-repo azd
#             down destroyed the RG twice in one week; mitigated with
#             the CanNotDelete lock documented above).
#             Live value:
#               az identity show --name uami-shared-gbb-ci \
#                 --resource-group rg-shared-gbb-ci \
#                 --query clientId -o tsv
#   Option B: the value printed from Step 1 above
UAMI_CLIENT_ID=<from-above>

TENANT_ID=$(az account show --query tenantId -o tsv)
SUB_ID=$(az account show --query id -o tsv)

# Foundry account base URL — bare account, NOT project-scoped
# (See F-16 in threadlight-deploy/SKILL.md § Deploy-time failure-mode index)
AI_ENDPOINT="https://${FOUNDRY_ACCT}.cognitiveservices.azure.com"

gh secret set AZURE_CLIENT_ID       --repo aiappsgbb/threadlight-skills --body "$UAMI_CLIENT_ID"
gh secret set AZURE_TENANT_ID       --repo aiappsgbb/threadlight-skills --body "$TENANT_ID"
gh secret set AZURE_SUBSCRIPTION_ID --repo aiappsgbb/threadlight-skills --body "$SUB_ID"
gh secret set AZURE_AI_ENDPOINT     --repo aiappsgbb/threadlight-skills --body "$AI_ENDPOINT"

# Verify
gh secret list --repo aiappsgbb/threadlight-skills | grep -E "AZURE_"
```

You should see all 4.

## Step 4 — Fire the first run

```bash
gh workflow run threadlight-e2e-foundry.yml \
  --repo aiappsgbb/threadlight-skills \
  --ref main \
  -f region=westus3 \
  -f teardown=true \
  -f mode=full

# Watch
gh run watch \
  --repo aiappsgbb/threadlight-skills \
  $(gh run list --repo aiappsgbb/threadlight-skills \
       --workflow=threadlight-e2e-foundry.yml \
       --limit 1 --json databaseId --jq '.[0].databaseId')
```

> **Note on model deployment name:** the workflow defaults `model_deployment`
> to `gpt-5.4-mini`. Override per run with `-f model_deployment=<name>`
> (e.g. `-f model_deployment=gpt-4o-mini`). The value flows into both the
> agent driver (`COPILOT_PROVIDER_MODEL_ID` for all four phases + the
> discovery smoke gate) AND the Pattern 0 `.env.local`
> (`AZURE_OPENAI_DEPLOYMENT`). The named deployment must already exist on
> the `AZURE_AI_ENDPOINT` Foundry account.

> **Cheap pre-flight:** trigger `-f mode=smoke-only` first to exercise just
> the discovery gate (~3-5 min, no Azure spend). Use `mode=full` only when
> the smoke run is green.

Expected: ~35-55 min wallclock. If it hangs at the first step ("Azure login via OIDC"), the federated credential is mis-scoped — go back to Step 1.

## Step 5 — When the run is green

After a successful first run:

1. Download the artifact:
   ```bash
   gh run download <run-id> --repo aiappsgbb/threadlight-skills \
     --name threadlight-e2e-<run-id> --dir /tmp/threadlight-evidence
   ```
2. Inspect the workspace artifacts (`returns-triage/specs/`, `returns-triage/docs/`, the 4 `phase-*.log` files, the 2 invoke transcripts) to confirm the pipeline produced what you expected
3. Consider adding a validation evidence row to `skills/threadlight-deploy/SKILL.md` § Validation history (TBD; cribbed from agentic-loop's table pattern)

## Teardown if you want to remove the federated credentials later

```bash
# Option A
az identity federated-credential delete \
  --name "github-aiappsgbb-threadlight-skills-main" \
  --identity-name uami-shared-gbb-ci \
  --resource-group rg-shared-gbb-ci

az identity federated-credential delete \
  --name "github-aiappsgbb-threadlight-skills-feat" \
  --identity-name uami-shared-gbb-ci \
  --resource-group rg-shared-gbb-ci

# Option B (also delete the UAMI itself if no other workload uses it)
az identity federated-credential delete --identity-name "$UAMI" --resource-group "$RG" \
  --name "github-aiappsgbb-threadlight-skills-main"
az identity federated-credential delete --identity-name "$UAMI" --resource-group "$RG" \
  --name "github-aiappsgbb-threadlight-skills-feat-port-ci-e2e-from-agentic-loop"
az identity delete --name "$UAMI" --resource-group "$RG"
```

Do NOT remove RBAC role assignments unless you're sure no other workflow uses the UAMI for the same scopes.
