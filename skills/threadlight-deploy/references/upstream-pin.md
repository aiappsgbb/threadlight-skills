---
schema_version: 2
freshness_tier: A
automation_tier: issue_only

upstream:
  type: github_repo
  repo: Azure-Samples/azd-ai-starter-basic
  ref: main
  pinned_sha: a781bbcc229048f6dd12771722342899d7cb23d7
  pinned_commit_message: |
    feat: add ACR support for existing AI projects when hosted agents require a registry (#59)
  license: MIT
  notes: |
    This skill wraps the `azd ai agent` + Azure Container Apps deployment pattern and
    vendors scaffold concepts from `azd-ai-starter-basic` so generated projects remain
    self-contained. Full validation runs `azd up` into a test subscription, so drift
    issues are human-triaged rather than assigned to GHCP automation.

    CAVEAT (2026-08-14): this repo pin no longer describes what the skill actually
    consumes. `threadlight-deploy` Step 1 generates the project with `azd ai agent
    init` and marks the resulting `infra/` "do not modify" — so the emitted infra
    comes from the azd CLI + `azure.ai.agents` extension, not from a clone of this
    repo at `pinned_sha`. The repo pin is retained because the skill still vendors
    scaffold *concepts* from it, but the binding runtime dependency is `tooling`
    below. Re-pin both together.

# The real dependency surface of the design->deploy path. `azure.ai.agents` is a
# beta extension with 50+ published versions; before 2026-08-14 CI installed it
# unpinned (`azd ext install azure.ai.agents || true`), so the generated infra
# could change between two runs of the same commit. Pinned in
# .github/workflows/threadlight-e2e-foundry.yml — keep these values in sync with
# that workflow; they are the same numbers in two places by design.
tooling:
  azd_version: "1.31.1"
  extensions:
    - id: azure.ai.agents
      version: "1.0.0-beta.10"
      pinned: true
    # Transitive extension dependencies, resolved by the azd extension installer
    # at install time. Recorded (not pinned) so drift shows up in a diff; the
    # workflow prints the resolved set on every run.
    - id: azure.ai.inspector
      version: "1.0.0-beta.3"
      pinned: false
      resolved_via: azure.ai.agents
    - id: azure.ai.projects
      version: "1.0.0-beta.6"
      pinned: false
      resolved_via: azure.ai.agents

docs_to_revalidate:
  - "https://github.com/Azure-Samples/azd-ai-starter-basic"
  - "https://github.com/Azure-Samples/azd-ai-starter-basic/blob/main/README.md"
  - "https://learn.microsoft.com/azure/developer/azure-developer-cli/"

known_issues:
  - id: unpinned-azd-toolchain
    summary: |
      CI installed the azd CLI and the azure.ai.agents extension unpinned, so the
      `infra/` the skill forbids modifying was produced by a moving upstream.
      Suspected contributor to the 2026-08-14 Phase 3 timeout, in which the agent
      spent the whole step budget hand-patching registry auth / AcrPull.
    status: mitigated
    mitigation: |
      Both pinned in the E2E workflow on 2026-08-14 and recorded under `tooling`.
      Reproducibility restored; this does not by itself prove Phase 3 is fixed.
  - id: deploy-mode-not-specified
    summary: |
      `azd ai agent init` takes --deploy-mode (`code` = ZIP upload, no ACR, the
      non-interactive default for Python; `container` = Docker image, needs ACR +
      AcrPull). SKILL.md never mentions the flag, while its azure.yaml example
      mandates the container path (`language: docker`, `docker.remoteBuild: true`).
      An agent following the skill therefore takes the ACR path and inherits the
      AcrPull propagation race (F-05/F-06) with no sanctioned remedy, because
      `infra/` is marked do-not-modify.
    status: open
    mitigation: |
      Not yet addressed. Deliberately left open: choosing between the code and
      container paths is a behavioural change on the critical design->deploy path
      and needs its own reviewed change, not a drive-by edit.

validation:
  requires:
    - azure_subscription
  runnable: false
  script: |
    #!/usr/bin/env bash
    # HUMAN EXECUTION ONLY — requires Azure subscription + tenant-isolated az/azd auth
    # Run this from a shell with AZURE_CONFIG_DIR set per azure-tenant-isolation skill
    set -euo pipefail

    : "${AZURE_SUBSCRIPTION_ID:?Set target Azure subscription id}"
    : "${AZURE_TENANT_ID:?Set target tenant id}"
    : "${THREADLIGHT_PROJECT_DIR:?Set path to a generated threadlight-deploy azd project}"
    : "${AZURE_LOCATION:=swedencentral}"
    : "${AZURE_ENV_NAME:=upstream-pin-threadlight-deploy}"

    PINNED_SHA="${PINNED_SHA:-a781bbcc229048f6dd12771722342899d7cb23d7}"
    ROOT_DIR="$(pwd)"
    WORKDIR="${WORKDIR:-$ROOT_DIR/.upstream-pin-work/threadlight-deploy}"
    THREADLIGHT_PROJECT_DIR="$(cd "$THREADLIGHT_PROJECT_DIR" && pwd)"

    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR"
    git clone --quiet https://github.com/Azure-Samples/azd-ai-starter-basic "$WORKDIR/azd-ai-starter-basic"
    cd "$WORKDIR/azd-ai-starter-basic"
    git checkout --quiet "$PINNED_SHA"
    test -f README.md

    actual_sub="$(az account show --query id -o tsv)"
    actual_tenant="$(az account show --query tenantId -o tsv)"
    test "$actual_sub" = "$AZURE_SUBSCRIPTION_ID"
    test "$actual_tenant" = "$AZURE_TENANT_ID"
    az account set --subscription "$AZURE_SUBSCRIPTION_ID"

    cd "$THREADLIGHT_PROJECT_DIR"
    azd auth login --tenant-id "$AZURE_TENANT_ID"
    azd env select "$AZURE_ENV_NAME" || azd env new "$AZURE_ENV_NAME" \
      --subscription "$AZURE_SUBSCRIPTION_ID" \
      --location "$AZURE_LOCATION"
    azd up --no-prompt
    azd ai agent validate
    azd ai agent show
    azd ai agent invoke "Reply with the token THREADLIGHT_DEPLOY_SMOKE_OK only."

    echo "THREADLIGHT_DEPLOY_VALIDATION_PASS"
  expected_output:
    - "THREADLIGHT_DEPLOY_VALIDATION_PASS"
  failure_signatures: []

last_validated: 2026-08-14
validated_by: ricchi
known_issues_count: 2
---

# Upstream pin — `threadlight-deploy` skill

This file is the **machine-readable validation contract** for the
`threadlight-deploy` skill. The YAML front-matter above is parsed by
`scripts/check-freshness.py` weekly; the prose below is the human audit trail.
Keep them in sync.

---

## 1. Pin

| Field | Value |
|-------|-------|
| **Upstream** | `Azure-Samples/azd-ai-starter-basic` |
| **Branch / tag** | `main` |
| **Pinned SHA** | `a781bbcc229048f6dd12771722342899d7cb23d7` |
| **Pinned commit subject** | `feat: add ACR support for existing AI projects when hosted agents require a registry (#59)` |
| **License** | `MIT` |
| **First authored against** | `2026-05-15` |
| **Last re-validated** | `2026-08-14` |

### 1b. Toolchain pin (the binding runtime dependency)

Since Step 1 of the skill generates the project with `azd ai agent init` rather
than copying this repo, the versions below — not the SHA above — determine the
`infra/` that reaches a customer. Pinned in the E2E workflow on 2026-08-14.

| Component | Version | Pinned? |
|-----------|---------|---------|
| `azd` CLI | `1.31.1` | ✅ `install-azd.sh --version` |
| `azure.ai.agents` extension | `1.0.0-beta.10` | ✅ `azd extension install --version` |
| `azure.ai.inspector` | `1.0.0-beta.3` | ⚠️ transitive, recorded only |
| `azure.ai.projects` | `1.0.0-beta.6` | ⚠️ transitive, recorded only |

Refresh procedure:
```bash
azd extension show azure.ai.agents   # read "Latest Version" + "Available Versions"
```

Bumping either pinned value requires a `mode=full` E2E run in the same PR — the
generated infra is not covered by any offline test.

Refresh procedure:
```bash
git ls-remote https://github.com/Azure-Samples/azd-ai-starter-basic main
# Compare first column to pinned_sha in front-matter
```

---

## 2. Verification checklist (the executable contract)

> **For coding agents**: `validation.runnable` is `false`. Do not run this in
> GHCP automation; it deploys a generated project with `azd up`.

```bash
#!/usr/bin/env bash
# HUMAN EXECUTION ONLY — requires Azure subscription + tenant-isolated az/azd auth
# Run this from a shell with AZURE_CONFIG_DIR set per azure-tenant-isolation skill
set -euo pipefail

: "${AZURE_SUBSCRIPTION_ID:?Set target Azure subscription id}"
: "${AZURE_TENANT_ID:?Set target tenant id}"
: "${THREADLIGHT_PROJECT_DIR:?Set path to a generated threadlight-deploy azd project}"
: "${AZURE_LOCATION:=swedencentral}"
: "${AZURE_ENV_NAME:=upstream-pin-threadlight-deploy}"

PINNED_SHA="${PINNED_SHA:-a781bbcc229048f6dd12771722342899d7cb23d7}"
ROOT_DIR="$(pwd)"
WORKDIR="${WORKDIR:-$ROOT_DIR/.upstream-pin-work/threadlight-deploy}"
THREADLIGHT_PROJECT_DIR="$(cd "$THREADLIGHT_PROJECT_DIR" && pwd)"

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
git clone --quiet https://github.com/Azure-Samples/azd-ai-starter-basic "$WORKDIR/azd-ai-starter-basic"
cd "$WORKDIR/azd-ai-starter-basic"
git checkout --quiet "$PINNED_SHA"
test -f README.md

actual_sub="$(az account show --query id -o tsv)"
actual_tenant="$(az account show --query tenantId -o tsv)"
test "$actual_sub" = "$AZURE_SUBSCRIPTION_ID"
test "$actual_tenant" = "$AZURE_TENANT_ID"
az account set --subscription "$AZURE_SUBSCRIPTION_ID"

cd "$THREADLIGHT_PROJECT_DIR"
azd auth login --tenant-id "$AZURE_TENANT_ID"
azd env select "$AZURE_ENV_NAME" || azd env new "$AZURE_ENV_NAME" \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --location "$AZURE_LOCATION"
azd up --no-prompt
azd ai agent validate
azd ai agent show
azd ai agent invoke "Reply with the token THREADLIGHT_DEPLOY_SMOKE_OK only."

echo "THREADLIGHT_DEPLOY_VALIDATION_PASS"
```

**Expected output** must contain (substring match):

- `THREADLIGHT_DEPLOY_VALIDATION_PASS`

**Failure signatures**: none recorded.

---

## 3. Live smoke results (last successful run)

| Check | Result | Evidence |
|-------|--------|----------|
| Issue-only validation procedure | ✅ | Human-run script documented for generated-project `azd up` + agent invoke. |

Captured at `last_validated: 2026-05-15` by `ricchi`.

---

## 4. Known issues at this pin

### 4.1 `unpinned-azd-toolchain` — mitigated 2026-08-14

Until 2026-08-14 the E2E workflow ran `azd ext install azure.ai.agents || true`
against an unpinned `azd` CLI. Two consequences:

1. **Non-reproducible infra.** The same commit could generate different `infra/`
   on two different days, because `azure.ai.agents` is a beta extension with 50+
   published versions. The skill tells the agent not to modify that infra, so the
   repo had no way to compensate for a change in it.
2. **Silent install failure.** `|| true` swallowed a failed install, deferring the
   error to `azd ai agent init` in Phase 3 — surfacing ~20 minutes later as an
   opaque agent-side failure instead of immediately at the install step.

Both are fixed in the workflow, and the resolved versions are recorded under
`tooling` in the front-matter. Note this restores **reproducibility**, which is a
prerequisite for diagnosing the 2026-08-14 Phase 3 timeout — it is not itself
evidence that the timeout is resolved.

### 4.2 `deploy-mode-not-specified` — open

`azd ai agent init` exposes `--deploy-mode`:

- `code` — ZIP upload. No container image, **no ACR, no AcrPull**. This is the
  extension's non-interactive default for Python projects.
- `container` — Docker image. Requires ACR plus an `AcrPull` grant to the pulling
  identity, and inherits the RBAC propagation race documented as F-05/F-06.

SKILL.md never names the flag, yet its `azure.yaml` example mandates the container
path (`language: docker`, `docker.remoteBuild: true`, "build container (ACR
remote)"). An agent following the skill therefore takes the ACR path — and when
the propagation race bites, it has no sanctioned remedy, because the only files
that could fix it live under a `do not modify` boundary. That is consistent with
the observed failure mode, in which the CI agent improvised patches to
`scripts/postprovision.py` until the step budget ran out.

**Deliberately left open.** Switching the documented path, or documenting the
choice explicitly, is a behavioural change to the base design→deploy flow and
deserves its own reviewed change with an E2E run behind it.

---

## 5. Re-pin procedure

When upstream advances:

1. **Capture new SHA**:
   ```bash
   git ls-remote https://github.com/Azure-Samples/azd-ai-starter-basic main
   ```
2. **Diff scaffold assumptions** against the generated artifacts described in
   `threadlight-deploy` Phase 5, especially `azure.yaml`, `agent.yaml`, and
   vendored Bicep module shapes.
3. **Update front-matter**: set `upstream.pinned_sha` to the new value and
   `upstream.pinned_commit_message` to the new commit subject.
4. **Human-run validation**: run the script in § 2 in a disposable validation
   environment.
5. **Verify expected output**: each `expected_output[]` substring must appear.
6. **Update audit trail**: set `last_validated`, `validated_by`, and
   `known_issues_count`.
7. **Bump SKILL.md `metadata.version` PATCH** per AGENTS.md § 5.

### 5b. Re-pinning the toolchain (§ 1b)

The toolchain pin moves independently of the repo SHA and, in practice, more
often. To bump it:

1. **Read available versions**: `azd extension show azure.ai.agents`.
2. **Update both places**: the `tooling` block in this front-matter *and* the
   pinned versions in `.github/workflows/threadlight-e2e-foundry.yml`. They are
   intentionally duplicated; a mismatch means the documented pin is fiction.
3. **Refresh the transitive rows** from the workflow's
   `azd extension list --installed` output — those are recorded, not pinned, so
   they drift silently and are only visible in a diff.
4. **Run `mode=full` E2E in the same PR.** No offline test covers the generated
   `infra/`; the workflow is the only sensor.
5. **Update `last_validated` / `known_issues`** with what the run showed.
8. **Open PR**: touch only `references/upstream-pin.md` and the SKILL.md
   frontmatter version line unless the issue explicitly requests a scaffold change.

---

## 6. URLs to re-validate (link-rot detector input)

- <https://github.com/Azure-Samples/azd-ai-starter-basic>
- <https://github.com/Azure-Samples/azd-ai-starter-basic/blob/main/README.md>
- <https://learn.microsoft.com/azure/developer/azure-developer-cli/>

---

## 7. Cross-references worth bookmarking

- `README.md` — upstream starter usage and deployment notes
- `infra/` — upstream infrastructure scaffold patterns for generated projects

---

## 8. Notes for the coding agent

> **If you're GHCP picking up a refresh issue for this skill:**
>
> 1. `automation_tier` is `issue_only` and `validation.runnable` is `false`.
>    Do not run the live validation script without explicit credentials from a human.
> 2. Open or update the drift issue with the new SHA and ask a human maintainer to
>    run § 2.
> 3. If the human posts passing evidence, update this pin file and bump only the
>    SKILL.md `metadata.version` PATCH line.
