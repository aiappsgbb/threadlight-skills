function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) {
      deepFreeze(nested);
    }
    Object.freeze(value);
  }
  return value;
}

export function artifactGroup(...paths) {
  return Object.freeze([...paths]);
}

export function skill(
  id,
  phase,
  label,
  requiredArtifactGroups,
  options = {},
) {
  const definition = {
    id,
    phase,
    label,
    requiredArtifactGroups: Object.freeze(
      requiredArtifactGroups.map((group) => Object.freeze([...group])),
    ),
    optionalArtifacts: Object.freeze([...((options.optionalArtifacts ?? []))]),
    prerequisiteSkills: Object.freeze([
      ...((options.prerequisiteSkills ?? [])),
    ]),
    role: options.role ?? "stage",
    applicability: options.applicability ?? "always",
    completionMode: options.completionMode ?? "artifact-complete",
    completionArtifact: options.completionArtifact ?? null,
    freshnessHours: options.freshnessHours ?? null,
    // threadlight-auto planner stage this skill owns (null when the skill is
    // not a planner gate). The plan, not this registry, decides gate order.
    stage: options.stage ?? null,
    affectsPhaseStatus: options.affectsPhaseStatus ?? true,
    nextIntent: Object.freeze({
      ...(options.nextIntent ?? { type: "resume_phase", phase }),
    }),
  };

  return deepFreeze(definition);
}

// Live-leg shared-envelope contracts (skills/_shared/manifest.py). Keyed by the
// leg's required artifact PATH so the Canvas projector recognizes a new-leg
// manifest by path and can strictly validate the safe common envelope before it
// trusts any status. Mirrors the consumer-side trust boundary in
// threadlight-production-ready (_GAP_LEG_EXPECTED_SCHEMA / _GAP_LEG_REQUIRED_IDS)
// so both surfaces reject the same forged evidence. `schema` is the per-file
// identity (connect is hyphen-namespaced; ground/load/upgrade are dotted);
// `findingIds` is the exact finding-id set the producer emits — one each.
// `allowedTopLevelKeys` mirrors each producer schema (and production-ready's
// consumer allowlist) without duplicating the producer-specific nested schema.
export const LEG_ENVELOPE_CONTRACTS = deepFreeze({
  "specs/connect-manifest.json": {
    schema: "threadlight-connect-manifest/v1",
    findingIds: ["INT-001", "INT-002", "INT-003", "INT-004"],
    allowedTopLevelKeys: [
      "schema",
      "tool_version",
      "generated_at",
      "freshness",
      "status",
      "findings",
      "tool_name",
      "integration_state",
      "target_state",
      "contract",
      "conformance",
      "evidence_summary",
      "apply_plan",
      "changed_paths",
      "apply",
    ],
  },
  "specs/ground-manifest.json": {
    schema: "threadlight.ground/v1",
    findingIds: ["GRD-001", "GRD-002", "GRD-003", "GRD-004"],
    allowedTopLevelKeys: [
      "schema",
      "tool_version",
      "generated_at",
      "freshness",
      "status",
      "findings",
      "sources",
      "acl_evidence",
      "citation_evidence",
      "refusal_evidence",
      "telemetry",
      "retrieval_quality_baseline",
    ],
  },
  "specs/load-manifest.json": {
    schema: "threadlight.load/v1",
    findingIds: ["LOAD-001", "LOAD-002", "LOAD-003"],
    allowedTopLevelKeys: [
      "schema",
      "tool_version",
      "generated_at",
      "freshness",
      "status",
      "findings",
      "profile_name",
      "endpoint_class",
      "endpoint_configured",
      "allow_production",
      "adapter_name",
      "budget",
      "diagnostics",
      "spec_update_plan",
    ],
  },
  "specs/upgrade-manifest.json": {
    schema: "threadlight.upgrade/v1",
    findingIds: ["UPG-001", "UPG-002", "UPG-003"],
    allowedTopLevelKeys: [
      "schema",
      "tool_version",
      "generated_at",
      "freshness",
      "status",
      "findings",
      "plan",
    ],
  },
});

// Process-language milestones, in the order a pilot is proven. Each groups
// one or more threadlight-auto planner stages (see process-model.mjs).
export const LIFECYCLE_PHASES = Object.freeze([
  Object.freeze({ id: "design", label: "Design", title: "Design the process" }),
  Object.freeze({ id: "controls", label: "Controls", title: "Select runtime controls" }),
  Object.freeze({ id: "publish", label: "Publish", title: "Publish on Microsoft Foundry" }),
  Object.freeze({ id: "verify", label: "Verify", title: "Verify it live" }),
  Object.freeze({ id: "evaluate", label: "Evaluate", title: "Measure quality and safety" }),
  Object.freeze({ id: "go-live", label: "Go-live", title: "Decide on go-live" }),
]);

function advisory(id, phase, label, requiredArtifactGroups, options = {}) {
  return skill(id, phase, label, requiredArtifactGroups, {
    role: "advisory",
    affectsPhaseStatus: false,
    ...options,
    nextIntent: { type: "invoke_skill", skillId: id, phase },
  });
}

export const SKILL_REGISTRY = Object.freeze([
  skill(
    "threadlight-qualify",
    "design",
    "Size the engagement",
    [artifactGroup("qualification/sizing-manifest.json")],
    {
      role: "entry",
      // No-repo entry point: qualification/sizing precedes the repo, so it is
      // advisory and never gates the Design milestone.
      affectsPhaseStatus: false,
      nextIntent: { type: "invoke_skill", skillId: "threadlight-qualify", phase: "design" },
    },
  ),
  skill(
    "threadlight-design",
    "design",
    "Define the pilot",
    [artifactGroup("specs/SPEC.md"), artifactGroup("specs/manifest.json")],
    { stage: "design" },
  ),
  skill(
    "threadlight-demo-data-factory",
    "design",
    "Create credible demo data",
    [artifactGroup("specs/sample-data")],
    {
      applicability: "mock-systems",
      prerequisiteSkills: ["threadlight-design"],
    },
  ),
  skill(
    "threadlight-event-triggers",
    "design",
    "Define event entry points",
    [artifactGroup("src/triggers")],
    {
      applicability: "event-trigger",
      prerequisiteSkills: ["threadlight-design"],
    },
  ),
  advisory(
    "threadlight-hitl-patterns",
    "design",
    "Design human approval cards",
    [artifactGroup("src/bot/cards/card_registry.json")],
    {
      applicability: "human-approval",
      prerequisiteSkills: ["threadlight-design"],
    },
  ),
  skill(
    "threadlight-workspace-ui",
    "design",
    "Shape the operator workspace",
    [artifactGroup("src/workspace")],
    {
      applicability: "workspace-ui",
      prerequisiteSkills: ["threadlight-design"],
    },
  ),
  skill(
    "threadlight-auto",
    "design",
    "Plan and drive the pilot lifecycle",
    [artifactGroup(".threadlight/auto-next.json")],
    {
      role: "orchestrator",
      completionMode: "artifact-running",
      affectsPhaseStatus: false,
      optionalArtifacts: [".threadlight/auto-state.json"],
      prerequisiteSkills: ["threadlight-design"],
    },
  ),
  skill(
    "threadlight-govern",
    "controls",
    "Select runtime controls",
    [artifactGroup("specs/governance-manifest.json")],
    {
      stage: "govern",
      optionalArtifacts: ["specs/governance-contract.json"],
      prerequisiteSkills: ["threadlight-design"],
    },
  ),
  skill(
    "threadlight-governed-actions",
    "controls",
    "Validate governed actions",
    [artifactGroup("tests/governed-actions-manifest.json")],
    {
      stage: "governed_actions_gate",
      applicability: "governance-contract",
      prerequisiteSkills: ["threadlight-govern"],
    },
  ),
  skill(
    "threadlight-local-test",
    "publish",
    "Run the local inner loop",
    [],
    {
      completionMode: "manual",
      affectsPhaseStatus: false,
      prerequisiteSkills: ["threadlight-design"],
    },
  ),
  skill(
    "threadlight-deploy",
    "publish",
    "Publish to the sandbox",
    [artifactGroup("azure.yaml"), artifactGroup("infra/main.bicep")],
    {
      stage: "deploy",
      prerequisiteSkills: ["threadlight-design", "threadlight-governed-actions"],
    },
  ),
  advisory(
    "threadlight-citadel-hub",
    "publish",
    "Stand up the AI governance hub",
    [],
    { completionMode: "manual", applicability: "governance-hub" },
  ),
  advisory(
    "threadlight-citadel-spoke",
    "publish",
    "Connect to the AI governance hub",
    [artifactGroup("infra/citadel-access-contracts")],
    { applicability: "governance-hub", prerequisiteSkills: ["threadlight-design"] },
  ),
  advisory(
    "threadlight-mcp-aca",
    "publish",
    "Host mock systems as MCP servers",
    [artifactGroup("src/mcp")],
    { applicability: "aca-mcp", prerequisiteSkills: ["threadlight-design"] },
  ),
  advisory(
    "threadlight-tenant-isolation",
    "publish",
    "Isolate Azure tenants per shell",
    [],
    { completionMode: "manual" },
  ),
  skill(
    "threadlight-safe-check",
    "verify",
    "Verify the deployment",
    [artifactGroup("tests/postdeploy-manifest.json")],
    {
      stage: "safe_check",
      freshnessHours: 24,
      optionalArtifacts: ["docs/safe-check-post.md"],
      prerequisiteSkills: ["threadlight-deploy"],
    },
  ),
  skill(
    "threadlight-consumption-iq",
    "verify",
    "Project consumption",
    [artifactGroup("specs/cost-manifest.json")],
    {
      stage: "cost_projection",
      optionalArtifacts: ["docs/cost-projection.md"],
      prerequisiteSkills: ["threadlight-safe-check"],
    },
  ),
  skill(
    "threadlight-agentops",
    "evaluate",
    "Inspect per-agent operations",
    [artifactGroup("specs/agentops-manifest.json")],
    {
      stage: "agentops",
      applicability: "agentops-opt-in",
      role: "advisory",
      affectsPhaseStatus: false,
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-safe-check"],
      nextIntent: { type: "invoke_skill", skillId: "threadlight-agentops", phase: "evaluate" },
    },
  ),
  skill(
    "threadlight-evals",
    "evaluate",
    "Measure quality",
    [artifactGroup("specs/evals-manifest.json")],
    {
      stage: "evals",
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-safe-check"],
    },
  ),
  skill(
    "threadlight-redteam",
    "evaluate",
    "Probe adversarial safety",
    [artifactGroup("specs/redteam-manifest.json")],
    {
      stage: "redteam",
      optionalArtifacts: ["docs/redteam-report.md"],
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-safe-check"],
    },
  ),
  advisory(
    "threadlight-connect",
    "evaluate",
    "Bind real integrations",
    [artifactGroup("specs/connect-manifest.json")],
    { freshnessHours: 24, prerequisiteSkills: ["threadlight-safe-check"] },
  ),
  advisory(
    "threadlight-ground",
    "evaluate",
    "Prove grounding & ACLs",
    [artifactGroup("specs/ground-manifest.json")],
    { freshnessHours: 24, prerequisiteSkills: ["threadlight-safe-check"] },
  ),
  advisory(
    "threadlight-loadtest",
    "evaluate",
    "Load-test the pilot",
    [artifactGroup("specs/load-manifest.json")],
    { freshnessHours: 24, prerequisiteSkills: ["threadlight-safe-check"] },
  ),
  skill(
    "threadlight-router-bench",
    "evaluate",
    "Learn from completed runs",
    [artifactGroup("router-bench-out")],
    {
      prerequisiteSkills: ["threadlight-evals"],
    },
  ),
  skill(
    "threadlight-production-ready",
    "go-live",
    "Assess production readiness",
    [artifactGroup("tests/production-readiness-manifest.json")],
    {
      optionalArtifacts: ["docs/production-readiness-report.md"],
      prerequisiteSkills: ["threadlight-safe-check"],
    },
  ),
  skill(
    "threadlight-cicd",
    "go-live",
    "Prepare the production pipeline",
    [
      artifactGroup(
        ".github/workflows/azd-deploy-prod.yml",
        "azure-pipelines.yml",
      ),
    ],
    {
      prerequisiteSkills: ["threadlight-production-ready"],
    },
  ),
  skill(
    "threadlight-customize",
    "go-live",
    "Prepare customer onboarding",
    [artifactGroup("docs/threadlight-customize/customer-profile.md")],
    {
      prerequisiteSkills: ["threadlight-production-ready"],
    },
  ),
  advisory(
    "threadlight-upgrade",
    "go-live",
    "Track upgrade drift",
    [artifactGroup("specs/upgrade-manifest.json")],
    { freshnessHours: 24, prerequisiteSkills: ["threadlight-safe-check"] },
  ),
]);
