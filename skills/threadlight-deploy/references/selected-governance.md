# Selected governance wiring rules

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it before wiring any selected governance binding (MAF local hooks, MAF gateway-only, GHCP).

- Keep the operator's runtime choice. MAF local hooks use the **whole portable
  provider**, native Task6 validator, signed Task8 envelope, real Key Vault
  verification, HTTP approval client and authenticated Task8 audit delivery. The
  `create_governed_agent` result is the agent actually passed to
  `ResponsesHostServer`; application middleware stays inside its native hooks.
  Required output mediation buffers before delivery and forces `store=False`.
  Required audit uses remote receipt ACK **before effects**, with bounded worker
  replay and shutdown; the local retry spool is not persistent hosted storage.
  CP outage or local spool failure is unhealthy and blocks required effects.
- MAF can also use the explicit **gateway-only** action path through
  `maf-gateway-container.py`: real native MAF functions call the authenticated
  MCP gateway, which runs ACS and the existing approval/audit protocol before
  the downstream action. This path has no mixed local/gateway bindings or
  lifecycle bindings; application-local tools must be exactly the declared
  unbound reads. Arbitrary request-level tool/middleware/provider overrides
  are rejected. OBO is not provided by this app-only path. Human decisions
  remain separate delegated approval-service calls, not SDK auto-approval.
  Generation and local native integration do not establish Azure deployment
  or business-binding live proof. An explicitly signed deferred approval mode
  returns `pending_approval` without keeping HTTP open. Resume the same action
  with its `governance_operation_id`; policy, arguments, trusted facts, identity,
  expiry and one-use approval are rechecked before the effect. This does not
  add OBO or automatically deploy a review frontend.
- GHCP uses `CopilotClient` + Invocations, **not** invented local hooks. Only
  bound MCP tools go through the authenticated gateway; mixed servers retain
  their explicit unbound tool filters, original URL and auth. The pinned pre-MCP
  hook supports metadata, not HTTP headers: the generated loopback adapter
  supplies fresh gateway-audience auth and Task9 idempotency headers.
- **Selected governance pins override all legacy/default pins below and stale
  installed companions.** Use `../_shared/governance-upstream-pin.json` exactly
  (AGT 5, ACS 0.3.1b0, Hooks 0.1.0a5, MAF 1.14/Foundry 1.11/hosting b260813).
- Missing/unsigned/expired policy, approval or audit dependencies must not
  produce healthy readiness. A diagnostic fallback is **not** a healthy agent.
  Deadline pressure or an already-built legacy image does not waive wiring.
- Separate control-plane, gateway, downstream and publisher identities.
  Binding must cross-check packaged service scopes/URLs against the observed
  deployment audiences/endpoints before writing artifacts; no auth drift.
  Never reuse the agent identity or a shared ancillary admin identity; the
  agent receives no approval-store write or direct downstream permissions.
  Private-required means supplied, verified Foundry VNet injection and reachable
  private dependencies, not external ingress plus a TODO.
- Follow **foundation → package/build → bind** in the runbook. Images and
  deployment identity must be resolved before service revisions; no
  hello-world placeholder images. Packaging, assessments and health are not
  live runtime/effect-closure proof. GHCP is only action-governed after the
  Task9/11 effect-closure probes pass; never claim full output gating.

For a prepared exact-pin Linux environment, the bounded inner loop is
`python scripts/ci/run-governance-pin-tests.py --mcp-prepared`. It verifies
published runtime bytes and runs only the MAF/MCP client, host and generation cases with a
120-second ceiling. Missing/skipped cases fail. It never installs packages,
deploys, runs the whole pipeline or replaces the full native/CTK acceptance gate.
