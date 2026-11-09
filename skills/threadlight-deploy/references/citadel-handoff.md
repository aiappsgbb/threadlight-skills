# Phase 7: Citadel handoff

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when SPEC § 11b requires a governance hub or the operator opts in.

## Phase 7: Citadel Handoff (opt-in — interactive when SPEC is silent)

**Trigger (two paths):**
1. **Explicit**: SPEC § 11b sets `governance_hub.required: yes` → proceed automatically.
2. **Interactive**: SPEC § 11b is absent or sets `governance_hub.required: no` → **ask the operator**.

If the customer wants the deployed agent to land as a **spoke under their
AI Governance Hub** (centralized model gateway, key vault inheritance,
APIM policies, JWT auth), Phase 7 invokes the `threadlight-citadel-spoke`
skill AFTER the base deployment is provisioned.

### Why this is opt-in (default: no)

- Citadel adds APIM connection wiring + product policy + JWT auth steps that
  are unnecessary for cx who just want a PoC running in their tenant
- Customers with no Citadel hub in place would face an extra dependency
- Adding Citadel later is a clean, additive change (no rewrite)
- **But**: customers who DO have a hub benefit from day-1 governance — hence
  the interactive prompt. Asking costs nothing; missing the window costs a
  follow-up engagement.

### Path 1: SPEC § 11b sets `governance_hub.required: yes`

Phase 7 reads SPEC § 11b for the rest of the AI Governance Hub spoke
posture:

```yaml
# specs/SPEC.md § 11b (when governance_hub.required: yes)
governance:
  governance_hub:
    required: yes
    hub_endpoint: https://hub-prod.<customer-apim>.azure-api.net
    access_contracts:
      - hub-llm-gateway
      - hub-knowledge-search
    secrets_via_keyvault: true
    jwt_auth: true
```

Then it hands off to `threadlight-citadel-spoke` (the **AI Citadel
Governance Hub** is the reference implementation; the SPEC field is
named generically because some customers run other hub products):

```
1. Run base azd up (Phase 5 + 6 complete) — agent deploys to its own tenant
2. Invoke threadlight-citadel-spoke skill with:
   - hub_endpoint
   - access_contracts list
   - the agent's UAMI principal (so APIM can grant it product subscription)
3. threadlight-citadel-spoke produces:
   - APIM connection in the Foundry project pointing to hub gateway
     (use Option B — Foundry Connection — NOT Option A; Option A breaks
     the keyless-by-mandate posture for threadlight pilots)
   - Key Vault references replacing direct AOAI keys
   - Updated agent.yaml with `MODEL_DEPLOYMENT_NAME = connectionName/deploymentName`
   - JWT validation policy on the agent endpoint
   - Validation notebook to prove end-to-end works
4. Redeploy agent (azd deploy <service>) to pick up the new MODEL_DEPLOYMENT_NAME
```

### Path 2: SPEC § 11b absent or `governance_hub.required: no` — interactive prompt

When the SPEC does not pre-declare a governance hub, Phase 7 **asks the
operator** before skipping:

> **Citadel governance hub.** Does this customer have an AI Governance Hub
> (Azure APIM AI Gateway) deployed on their tenant?
> If yes, we can onboard this agent as a spoke now — governed from day 1.
> Onboard to Citadel? [yes / **no** (default)]

> **Bias from Phase 1.5 posture.** If `specs/deployment-posture.md`
> declares `deployment_target: production-bound`, change the default in
> the prompt above from **no** → **yes** (production posture treats a
> governance hub as the norm). Do NOT auto-onboard — still ask — but
> surface the recommendation: _"posture is production-bound; consider
> Citadel onboarding now to avoid a follow-up engagement"_. Other
> targets (`demo-sandbox`, `customer-pilot`) keep the existing **no**
> default.

**If the operator answers no** (or accepts the default): Phase 7 is a
**no-op** — log "Governance hub onboarding skipped (operator declined)" and
end. The customer can re-enable later by setting
`governance_hub.required: yes` in SPEC § 11b and running
`threadlight-deploy` Phase 7 again (it is incrementally re-runnable).

**If the operator answers yes**, collect the hub details interactively:

```
1. Hub endpoint URL?  → e.g. https://hub-prod.<customer-apim>.azure-api.net
2. Access contracts?  → comma-separated, e.g. hub-llm-gateway, hub-knowledge-search
3. Secrets via Key Vault? [yes/no, default: yes]
4. JWT auth? [yes/no, default: yes]
```

Then proceed with the same `threadlight-citadel-spoke` handoff as Path 1.
The operator-provided values are equivalent to SPEC § 11b — the skill
treats them identically.

> **Why ask at deploy time, not design time?** The customer's hub may not
> exist (or be known to the GBB) when the SPEC is authored. By the time
> `threadlight-deploy` runs, the operator is in the customer's Azure
> subscription and can check. The prompt surfaces the option without
> forcing SPEC rewrites.

> **See `threadlight-citadel-spoke` skill** for the full step-by-step
> onboarding procedure, APIM access contract details, and validation
> notebook.
