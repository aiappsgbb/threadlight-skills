# Phase 6.5: Demo data seed

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when SPEC § 5 marks any system as `mock`.

## Phase 6.5: Demo Data Seed (MANDATORY when SPEC § 5 marks any system as `mock`)

> **Why this is mandatory.** SPEC § 5 declares which systems are `mock`
> (almost always true for pilots). For each `mock` system backed by
> Cosmos, the deployment provisions empty containers — and unless a seed
> step runs, the deployed agent answers "not found" or "no records" on
> every realistic prompt. The `threadlight-demo-data-factory` skill
> generates `specs/sample-data/*.json` AND `scripts/seed_data.py`, but
> Phase 6.5 is what actually invokes the seed at deploy time.
>
> Origin: recent pilot retrospective — `azd up` returned 0, the agent
> deployed cleanly, the user typed `CASE-<id>` (a realistic golden
> case from `specs/sample-data/*.json`), the agent
> correctly reported "not found" because Cosmos was empty of that ID.
> The demo looked broken; it wasn't — it just had no data. 4 hours
> diagnosing what should have been a postdeploy seed step.

### Step 1 — Verify the demo-data-factory artifacts exist

Phase 1 (Analyze) reads SPEC § 5. If any system has `mock: yes`:

- ✅ `specs/sample-data/*.json` — at least one JSON per Cosmos container
  the spec declares (cases, customers, screening, risk-assessments, etc.)
- ✅ `scripts/seed_data.py` — the upsert script following the
  `threadlight-demo-data-factory` `Semaphore(8)` pattern
- ✅ `scripts/reset_data.py` — wipes Cosmos and re-runs `seed_data.py`
  (for re-runs after schema changes)

If any of the above is missing, **invoke `threadlight-demo-data-factory`
NOW** to generate them. Do not proceed to Step 2 until they exist.

### Step 2 — Grant Cosmos data-plane RBAC to the deployer

This is the single most common blocker. Cosmos data-plane writes
require **`Cosmos DB Built-in Data Contributor`** (role definition ID
`00000000-0000-0000-0000-000000000002`) — control-plane Owner is NOT
sufficient. Grant the deployer principal:

```bash
DEPLOYER_OID=$(az ad signed-in-user show --query id -o tsv)
COSMOS_ID=$(az cosmosdb show -g $RG -n $COSMOS_ACCOUNT --query id -o tsv)
az cosmosdb sql role assignment create -g $RG -a $COSMOS_ACCOUNT \
  --role-definition-id "00000000-0000-0000-0000-000000000002" \
  --principal-id "$DEPLOYER_OID" \
  --scope "$COSMOS_ID"
sleep 30   # RBAC propagation
```

For the **agent + bot UAMI** (so the runtime can read/write Cosmos via
the MCP server), the same role is required at the same scope. The Bicep
in `infra/modules/cosmos-db.bicep` should declare this assignment for
the shared UAMI; verify it's there.

### Step 3 — Wire the seed into the postdeploy hook chain

Append to `azure.yaml`:

```yaml
hooks:
  postdeploy:
    shell: pwsh
    run: |
      cd scripts && uv sync --frozen --quiet
      uv run seed_data.py
```

If a `postdeploy` hook already exists from Phase 5/6 (Toolbox setup,
Teams manifest sideload, AppIn connect), **merge** rather than overwrite:

```yaml
hooks:
  postdeploy:
    shell: pwsh
    run: |
      cd infra/scripts && uv sync --frozen --quiet
      uv run postdeploy.py     # dispatcher: toolbox + manifest + appin + seed
```

The dispatcher pattern (`infra/scripts/postdeploy.py`) is documented in
[`references/azd-modules/README.md`](azd-modules/README.md) § "Recommended: Python + `postdeploy` hook (cross-platform)".

### Step 4 — Verify

`scripts/seed_data.py` should print one line per file: `→ <file>
<container> (N docs)` and end with `✅ upserted N   ❌ errors 0`. The
post-deploy completeness gate (`threadlight-safe-check --phase
post-deploy` Step 5.8) then asserts that for each container declared
in SPEC § 5b with `seed_from: sample-data`, document count is non-zero.
If either step fails, the PoC is incomplete; do NOT declare victory.
