# Production CI/CD for Threadlight pilots — best practices

Reference notes behind the artifacts that `generate_pipeline.py` emits. This
is the "why", grounded in Microsoft Learn. The generator stays deterministic
and offline; everything here is guidance for the human operator and the
customer's platform team.

> **Golden rule.** The pilot pipeline never holds a long-lived secret and
> never touches the central platform. Authenticate with **OIDC / Workload
> Identity Federation**; scope RBAC to the **target / spoke resource group**;
> leave the hub to `citadel-hub-deploy`.

---

## 1. No stored cloud secrets — federate instead

The single most important hardening step is to **stop storing
`AZURE_CREDENTIALS` / a client secret** in the CI system. Both platforms
support OpenID-Connect-based federation to a user-assigned managed identity
(UAMI) or app registration, so the runner exchanges a short-lived,
workload-scoped token at job time.

- **GitHub Actions → Azure via OIDC.** Configure a federated identity
  credential whose subject matches the workflow's `repo:OWNER/REPO:...`
  claim (branch, tag, or — recommended — `environment:NAME`). The pipeline
  then logs in with no secret.
  - Connect from GitHub with OIDC:
    <https://learn.microsoft.com/azure/developer/github/connect-from-azure-openid-connect#prerequisites>
  - IaC with GitHub Actions (prereqs / identity):
    <https://learn.microsoft.com/devops/deliver/iac-github-actions#prerequisites>
  - Federated identity credential trust (subject/issuer):
    <https://learn.microsoft.com/entra/workload-id/workload-identity-federation-create-trust#configure-a-federated-identity-credential-on-an-app>
- **azd in CI.** `azd pipeline config` wires the federated credential and
  pipeline variables for you; the emitted workflow uses
  `azd auth login --federated-credential-provider "github"` so GitHub's
  injected OIDC env vars are consumed automatically (no `${{ }}` secret
  plumbing in the deploy step).
  - GitHub Actions pipeline with azd:
    <https://learn.microsoft.com/azure/developer/azure-developer-cli/pipeline-github-actions#create-a-pipeline-using-github-actions>
  - Azure DevOps pipeline with azd:
    <https://learn.microsoft.com/azure/developer/azure-developer-cli/configure-devops-pipeline>
- **Azure DevOps → Azure via Workload Identity Federation.** Use an ARM
  **service connection configured with workload identity federation** and
  reference it by name from `AzureCLI@2`. This removes the service-principal
  secret entirely. Avoid `System.AccessToken` in deploy logic.
  - Configure WIF service connection:
    <https://learn.microsoft.com/azure/devops/pipelines/release/configure-workload-identity?view=azure-devops#set-a-workload-identity-service-connection>
  - Secret-free AzureRM deployments (background):
    <https://learn.microsoft.com/azure/devops/release-notes/roadmap/2022/secret-free-azurerm-deployments>

**What the generator does.** Client-id / tenant-id / subscription-id are
public identifiers and are inlined as literals; the UAMI client-id is left as
`REPLACE_WITH_UAMI_CLIENT_ID` until the platform team runs runbook `01`. No
secret value is ever written to an emitted file (enforced by
`test_no_secrets_in_templates.py`).

---

## 2. Least-privilege RBAC, scoped to the spoke / target RG

Grant the deploy identity only what it needs, at the **narrowest scope** that
still lets `azd provision`/`deploy` succeed — the **target resource group**
(standalone) or the **spoke resource group** (citadel-spoke). Never grant at
subscription scope "to be safe", and never at hub scope.

- Use a dedicated UAMI per pilot/environment; separate identities for
  `provision` (resource creation) vs `deploy` (push to existing resources)
  when the customer's separation-of-duties policy requires it.
- Prefer built-in roles over a custom role unless the customer mandates one;
  document every assignment in runbook `02` so it is auditable and revocable.
- Deployment-identity pattern (AVM contribution flow, transferable):
  <https://azure.github.io/Azure-Verified-Modules/contributing/bicep/bicep-contribution-flow/#2-configure-a-deployment-identity-in-azure>

**Boundary invariant.** For `citadel-spoke` posture the pilot consumes the
hub through an **Access Contract** (`citadel-spoke-onboarding`), so the
pilot's UAMI needs **no** role on the hub, shared APIM, shared networking, or
platform Key Vault. Runbook `02` asserts spoke-RG scope; the generator marks
spoke paths `rbac_scope=spoke-rg` unconditionally.

---

## 3. Environment gates and approvals

Production deploys should pass through a reviewed gate, not run on every push.

- **GitHub Actions:** target a protected `environment` (e.g. `production`)
  with required reviewers and, optionally, a wait timer / branch restriction.
  Binding the federated credential subject to `environment:production` also
  tightens the trust (token only mints for that environment).
- **Azure DevOps:** use an Environment with approvals & checks; reference it
  from the deploy stage. Keep `provision` and `deploy` as separate stages so
  an approver can inspect the plan before resources change.

---

## 4. Private-VNet deployments → private runners

If the target landing zone uses **private endpoints** (no public network
exposure), Microsoft-hosted / GitHub-hosted runners cannot reach the data
plane. The deploy must run from a runner with a NIC in (or peered to) the
target VNet. The skill wires the pipeline (`runs-on` label for GitHub, `pool`
ref for ADO) and ships runbook `03` covering both options:

- **Azure DevOps — Managed DevOps Pools** (Microsoft-managed agents injected
  into your VNet; least operational overhead):
  - Overview / benefits:
    <https://learn.microsoft.com/azure/devops/managed-devops-pools/overview?view=azure-devops#benefits>
  - Networking (isolated vs injected into existing VNet):
    <https://learn.microsoft.com/azure/devops/managed-devops-pools/configure-networking?view=azure-devops#agents-injected-into-existing-virtual-network>
  - Designate the pool in your pipeline:
    <https://learn.microsoft.com/azure/devops/pipelines/agents/pools-queues?view=azure-devops#designate-a-pool-in-your-pipeline>
- **Self-hosted runners / agents** (customer owns the VM/VMSS lifecycle and
  patching) when policy forbids managed pools or for GitHub Actions:
  the runbook covers a VMSS/agent in the spoke VNet with outbound to the
  pipeline control plane. The pipeline references it by `runs-on:
  [self-hosted, <label>]` (GitHub) or `pool: { name: <pool> }` (ADO).

Choose **managed pools first** (less to own); fall back to self-hosted only
when required. Either way the runner's identity is still the federated UAMI —
private networking changes *where* the job runs, not *how* it authenticates.

---

## 5. Parallel-track boundary (must-tell)

This pipeline is a **separate repo/pipeline** from central-platform
deployment. It must **never** deploy or modify:

- the Citadel hub,
- shared APIM / AI gateway,
- shared networking (hub VNet, firewall, DNS zones),
- the platform Key Vault.

Those are owned by the central-platform team via **`citadel-hub-deploy`**
(awesome-gbb), with the pilot wired in as a spoke via
**`citadel-spoke-onboarding`**. The onboarding-path gate resolves which of
the three paths applies and writes `onboarding-path.json`; the generated
`central-platform-boundary.md` restates the rule for the customer. See
`onboarding-path-decision.md` for the decision tree.

---

## Source freshness

Links above are Microsoft Learn / Azure docs captured during authoring. Azure
DevOps and Managed DevOps Pools docs are versioned with `?view=azure-devops`
and evolve; re-check before a customer engagement. The generator does not
depend on any of these at runtime.
