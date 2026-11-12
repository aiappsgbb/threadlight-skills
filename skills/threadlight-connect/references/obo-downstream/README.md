# User-scoped OBO downstream (reference)

A small MCP server that proves **who** reached it. It accepts only a Microsoft
Entra **delegated** access token for its own API and reports the user (`oid`,
`tid`, name) and the actor application (`azp`) it saw. Use it to prove that a
hosted agent calls a tool **on behalf of the signed-in user**, and that the
downstream, not the agent, makes the authorization decision.

| Request | Downstream answer |
|---|---|
| No token, bad signature, wrong issuer or audience, expired | HTTP 401 |
| App-only token (no `scp`): the agent acting as itself | HTTP 403 `delegated_token_required` |
| Delegated token without `Records.Read` | HTTP 403 `missing_scope` |
| Delegated token, user without the `Records.Reader` app role | tool result `access_denied` |
| Delegated token, user with the role | records plus the `seen` block |

Logs carry the decision, a 10-character hash of the user object ID and the
actor application ID. They never carry a token or a claim set.

Files: `server.py` (FastMCP 1.29.1, streamable HTTP, stateless), `Dockerfile`,
`requirements.lock` (hashed pins, regenerate from `requirements.in`). Tests:
`skills/threadlight-connect/tests/test_obo_downstream.py` (offline, local RSA keys).

## Live recipe: hosted agent → toolbox → OBO → this server

Proven live on 2026-11-11 with a GHCP hosted agent; the full record, the
measured timings and the boundaries are in
[docs/agent-operations.md, "Identity model"](../../../../docs/agent-operations.md#identity-model-who-acts-with-which-permissions).
Everything below uses the documented Foundry path
([User delegation for tools](https://learn.microsoft.com/azure/foundry/agents/concepts/user-delegation),
[Configure user delegation](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-user-delegation));
`AgentBlueprint` and `AgentOnBehalfOfUser` are **preview**.

1. **API registration** for this server: identifier URI `api://<api-app-id>`,
   `requestedAccessTokenVersion: 2`, a delegated scope `Records.Read`, a user
   app role `Records.Reader`. Assign the role only to users who may read.
2. **Deploy** this image to Azure Container Apps (`docker.remoteBuild: true`,
   `minReplicas: 0`) with `OBO_TENANT_ID` and `OBO_API_CLIENT_ID`.
3. **Agent endpoint**: add the `AgentBlueprint` scheme next to the existing
   `Entra`/`AzureRbac` one (`PATCH {project}/agents/<agent>?api-version=v1`,
   restrict `allowed_tenants`). Clients then request
   `api://<blueprint-client-id>/access_agent` for the user.
4. **Connection** (`category: RemoteTool`, `authType: AgentOnBehalfOfUser`,
   `scopes: ["api://<api-app-id>/Records.Read"]`, `metadata.type: generic_mcp`)
   and a **toolbox** with one `mcp` tool that references it.
5. **Blueprint permissions**: record `requiredResourceAccess` and
   `inheritablePermissions` on the managed blueprint
   (`PATCH {project}/managedAgentIdentityBlueprints/<blueprint>?api-version=2025-05-01`).
6. **Consent**. In the live run, preauthorizing the blueprint on the API and an
   admin grant on the blueprint principal still returned `CONSENT_REQUIRED`
   naming the **agent identity** as client; a tenant-admin
   `oauth2PermissionGrant` (`consentType: AllPrincipals`, scope `Records.Read`)
   on the agent identity's service principal unblocked it within a minute.
   Without admin consent, surface the `oauth_consent_request` link to the user.
7. **Hosted agent**: set `TOOLBOX_NAME` on the GHCP pilot. The template's
   `_ToolboxBridge` forwards the platform's `x-agent-foundry-call-id`, which the
   toolbox needs to resolve the user, and bridges the tools as Copilot SDK tools.

Check: an allowed user gets the records and `seen.actor_app_id` equals the
agent identity; a user without the role gets `access_denied`; the downstream
log shows `decision=allow|deny` with the same actor.

## Two hops: agent to agent (A2A), live 2026-11-12

To keep the user through another Foundry agent, give the caller's toolbox one
GA `a2a` tool (`a2a_version: "1.0"`, **only** `project_connection_id`) whose
`RemoteA2A` connection uses `UserEntraToken` with audience `https://ai.azure.com`
and the target's `.../agents/<target>/endpoint/protocols/a2a` base path. Grant:
users (or groups) **Foundry Agent Consumer** on the target agent; the caller
agent identity an admin `user_impersonation` grant on Azure Machine Learning
Services; the target agent identity the same `Records.Read` grant as above. The
downstream then sees the user with the **target** agent identity as actor.
An `AgenticIdentityToken` A2A connection carries no user, and the target's
user-delegated tool fails. Results and blockers:
[Identity model](../../../../docs/agent-operations.md#identity-model-who-acts-with-which-permissions).
