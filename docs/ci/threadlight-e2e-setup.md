# `threadlight-e2e-foundry.yml` — one-time setup runbook

> Do this **once** before the first run of
> [`threadlight-e2e-foundry.yml`](../../.github/workflows/threadlight-e2e-foundry.yml).
> Operator guide for repeat usage: [`threadlight-e2e.md`](./threadlight-e2e.md).

## Option A — reuse the existing `uami-awesome-gbb-ci` UAMI (recommended if available)

The agentic-loop + awesome-gbb CI all share a single UAMI (`uami-awesome-gbb-ci` in `rg-awesome-gbb-ci`, sub `ME-MngEnvMCAP979166-fruocco-2` as of 2026-06-01). If your tenant has access to that sub, the lowest-cost option is to add 2 more federated credentials to it for `aiappsgbb/threadlight-skills`.

```bash
az identity federated-credential create \
  --name "github-aiappsgbb-threadlight-skills-main" \
  --identity-name uami-awesome-gbb-ci \
  --resource-group rg-awesome-gbb-ci \
  --issuer "https://token.actions.githubusercontent.com" \
  --subject "repo:aiappsgbb/threadlight-skills:ref:refs/heads/main" \
  --audiences "api://AzureADTokenExchange"

az identity federated-credential create \
  --name "github-aiappsgbb-threadlight-skills-feat" \
  --identity-name uami-awesome-gbb-ci \
  --resource-group rg-awesome-gbb-ci \
  --issuer "https://token.actions.githubusercontent.com" \
  --subject "repo:aiappsgbb/threadlight-skills:ref:refs/heads/feat/port-ci-e2e-from-agentic-loop" \
  --audiences "api://AzureADTokenExchange"
```

The UAMI already has these roles (granted during agentic-loop CI setup) — no extra grants needed:

| Scope | Role |
|---|---|
| `/subscriptions/<sub>` | Contributor + User Access Administrator + Reader |
| Foundry account `aif-awesome-gbb-ci` | Cognitive Services OpenAI User + Foundry User |
| ACR `acrawesomegbbci` | AcrPush |
| AppIn `appi-awesome-gbb-ci` | Monitoring Metrics Publisher |

Skip to **Step 3 — GH secrets** below.

## Option B — provision a dedicated UAMI in your own subscription

Use when Option A's sub isn't accessible. Replace `<sub>`, `<rg>`, `<region>`, `<foundry-account>` with your values.

### Prerequisites

- `az` CLI authenticated to the target sub: `az account show` reports the right `tenantId`
- `gh` CLI authenticated to `aiappsgbb/threadlight-skills`: `gh auth status`
- Existing Foundry account with at least one `gpt-5.4-mini` GlobalStandard deployment

### Step 1 — Create UAMI + federated credentials

```bash
SUB=<your-sub-id>
RG=rg-threadlight-ci
LOC=swedencentral
UAMI=uami-threadlight-ci

az group create --name "$RG" --location "$LOC"

az identity create \
  --name "$UAMI" \
  --resource-group "$RG" \
  --location "$LOC"

UAMI_PRINCIPAL=$(az identity show --name "$UAMI" --resource-group "$RG" --query principalId -o tsv)
UAMI_CLIENT_ID=$(az identity show --name "$UAMI" --resource-group "$RG" --query clientId -o tsv)

# Federated credentials — main + the feat branch this PR is on
for branch in main feat/port-ci-e2e-from-agentic-loop; do
  az identity federated-credential create \
    --name "github-aiappsgbb-threadlight-skills-$(echo $branch | tr / -)" \
    --identity-name "$UAMI" \
    --resource-group "$RG" \
    --issuer "https://token.actions.githubusercontent.com" \
    --subject "repo:aiappsgbb/threadlight-skills:ref:refs/heads/${branch}" \
    --audiences "api://AzureADTokenExchange"
done
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
#   Option A: ff405901-cfd1-473f-9b55-99b012752a8e (uami-awesome-gbb-ci)
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
  -f scenario=auto-claim-triage \
  -f region=westus3 \
  -f teardown=true

# Watch
gh run watch \
  --repo aiappsgbb/threadlight-skills \
  $(gh run list --repo aiappsgbb/threadlight-skills \
       --workflow=threadlight-e2e-foundry.yml \
       --limit 1 --json databaseId --jq '.[0].databaseId')
```

Expected: ~35-55 min wallclock. If it hangs at the first step ("Azure login via OIDC"), the federated credential is mis-scoped — go back to Step 1.

## Step 5 — When the run is green

After a successful first run:

1. Download the artifact:
   ```bash
   gh run download <run-id> --repo aiappsgbb/threadlight-skills \
     --name threadlight-e2e-<run-id> --dir /tmp/threadlight-evidence
   ```
2. Inspect the workspace artifacts (specs/, docs/) to confirm the pipeline produced what you expected
3. Consider adding a validation evidence row to `skills/threadlight-deploy/SKILL.md` § Validation history (TBD; cribbed from agentic-loop's table pattern)

## Teardown if you want to remove the federated credentials later

```bash
# Option A
az identity federated-credential delete \
  --name "github-aiappsgbb-threadlight-skills-main" \
  --identity-name uami-awesome-gbb-ci \
  --resource-group rg-awesome-gbb-ci

az identity federated-credential delete \
  --name "github-aiappsgbb-threadlight-skills-feat" \
  --identity-name uami-awesome-gbb-ci \
  --resource-group rg-awesome-gbb-ci

# Option B (also delete the UAMI itself if no other workload uses it)
az identity federated-credential delete --identity-name "$UAMI" --resource-group "$RG" \
  --name "github-aiappsgbb-threadlight-skills-main"
az identity federated-credential delete --identity-name "$UAMI" --resource-group "$RG" \
  --name "github-aiappsgbb-threadlight-skills-feat-port-ci-e2e-from-agentic-loop"
az identity delete --name "$UAMI" --resource-group "$RG"
```

Do NOT remove RBAC role assignments unless you're sure no other workflow uses the UAMI for the same scopes.
