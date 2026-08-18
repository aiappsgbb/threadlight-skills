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
export const LEG_ENVELOPE_CONTRACTS = deepFreeze({
  "specs/connect-manifest.json": {
    schema: "threadlight-connect-manifest/v1",
    findingIds: ["INT-001", "INT-002", "INT-003", "INT-004"],
  },
  "specs/ground-manifest.json": {
    schema: "threadlight.ground/v1",
    findingIds: ["GRD-001", "GRD-002", "GRD-003", "GRD-004"],
  },
  "specs/load-manifest.json": {
    schema: "threadlight.load/v1",
    findingIds: ["LOAD-001", "LOAD-002", "LOAD-003"],
  },
  "specs/upgrade-manifest.json": {
    schema: "threadlight.upgrade/v1",
    findingIds: ["UPG-001", "UPG-002", "UPG-003"],
  },
});

export const LIFECYCLE_PHASES = Object.freeze([
  Object.freeze({ id: "design", label: "Design", view: "design" }),
  Object.freeze({
    id: "build-deploy",
    label: "Build / Deploy",
    view: "run",
  }),
  Object.freeze({ id: "discover", label: "Discover", view: "assurance" }),
  Object.freeze({
    id: "protect-govern",
    label: "Protect / Govern",
    view: "assurance",
  }),
  Object.freeze({ id: "improve", label: "Improve", view: "generic" }),
  Object.freeze({ id: "handoff", label: "Handoff", view: "handoff" }),
]);

export const SKILL_REGISTRY = Object.freeze([
  skill(
    "threadlight-qualify",
    "design",
    "Size the engagement",
    [artifactGroup("qualification/sizing-manifest.json")],
    {
      role: "entry",
      // No-repo entry point: qualification/sizing precedes the repo, so it is
      // advisory and never gates the Design phase.
      affectsPhaseStatus: false,
      completionMode: "artifact-complete",
      nextIntent: { type: "invoke_skill", skillId: "threadlight-qualify", phase: "design" },
    },
  ),
  skill(
    "threadlight-design",
    "design",
    "Define the pilot",
    [artifactGroup("specs/SPEC.md"), artifactGroup("specs/manifest.json")],
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
  skill(
    "threadlight-hitl-patterns",
    "design",
    "Design human decisions",
    [artifactGroup("src/bot")],
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
    "build-deploy",
    "Drive the pilot lifecycle",
    [artifactGroup(".threadlight/auto-state.json")],
    {
      role: "orchestrator",
      completionMode: "artifact-running",
      affectsPhaseStatus: false,
      prerequisiteSkills: ["threadlight-design"],
    },
  ),
  skill(
    "threadlight-local-test",
    "build-deploy",
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
    "build-deploy",
    "Deploy to the sandbox",
    [artifactGroup("azure.yaml"), artifactGroup("infra/main.bicep")],
    {
      completionArtifact: "docs/safe-check-post.md",
      prerequisiteSkills: ["threadlight-design"],
    },
  ),
  skill(
    "threadlight-safe-check",
    "build-deploy",
    "Verify the deployment",
    [artifactGroup("docs/safe-check-post.md")],
    {
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-deploy"],
    },
  ),
  skill(
    "threadlight-consumption-iq",
    "discover",
    "Project consumption",
    [artifactGroup("specs/cost-manifest.json")],
    {
      optionalArtifacts: ["docs/cost-projection.md"],
      prerequisiteSkills: ["threadlight-safe-check"],
    },
  ),
  skill(
    "threadlight-evals",
    "discover",
    "Measure quality",
    [artifactGroup("specs/evals-manifest.json")],
    {
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-safe-check"],
    },
  ),
  skill(
    "threadlight-redteam",
    "discover",
    "Probe adversarial safety",
    [artifactGroup("specs/redteam-manifest.json")],
    {
      optionalArtifacts: ["docs/redteam-report.md"],
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-safe-check"],
    },
  ),
  skill(
    "threadlight-connect",
    "discover",
    "Bind real integrations",
    [artifactGroup("specs/connect-manifest.json")],
    {
      // Advisory live leg: manual invocation only, never gates the phase.
      role: "advisory",
      affectsPhaseStatus: false,
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-safe-check"],
      nextIntent: { type: "invoke_skill", skillId: "threadlight-connect", phase: "discover" },
    },
  ),
  skill(
    "threadlight-ground",
    "discover",
    "Prove grounding & ACLs",
    [artifactGroup("specs/ground-manifest.json")],
    {
      role: "advisory",
      affectsPhaseStatus: false,
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-safe-check"],
      nextIntent: { type: "invoke_skill", skillId: "threadlight-ground", phase: "discover" },
    },
  ),
  skill(
    "threadlight-loadtest",
    "discover",
    "Load-test the pilot",
    [artifactGroup("specs/load-manifest.json")],
    {
      role: "advisory",
      affectsPhaseStatus: false,
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-safe-check"],
      nextIntent: { type: "invoke_skill", skillId: "threadlight-loadtest", phase: "discover" },
    },
  ),
  skill(
    "threadlight-govern",
    "protect-govern",
    "Verify runtime governance",
    [artifactGroup("specs/govern-manifest.json")],
    {
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-safe-check"],
    },
  ),
  skill(
    "threadlight-router-bench",
    "improve",
    "Learn from completed runs",
    [artifactGroup("router-bench-out")],
    {
      prerequisiteSkills: ["threadlight-evals"],
    },
  ),
  skill(
    "threadlight-upgrade",
    "improve",
    "Track upgrade drift",
    [artifactGroup("specs/upgrade-manifest.json")],
    {
      role: "advisory",
      affectsPhaseStatus: false,
      freshnessHours: 24,
      prerequisiteSkills: ["threadlight-safe-check"],
      nextIntent: { type: "invoke_skill", skillId: "threadlight-upgrade", phase: "improve" },
    },
  ),
  skill(
    "threadlight-production-ready",
    "handoff",
    "Assess production readiness",
    [artifactGroup("tests/production-readiness-manifest.json")],
    {
      optionalArtifacts: ["docs/production-readiness-report.md"],
      prerequisiteSkills: ["threadlight-safe-check"],
    },
  ),
  skill(
    "threadlight-cicd",
    "handoff",
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
    "handoff",
    "Prepare customer onboarding",
    [artifactGroup("docs/threadlight-customize/customer-profile.md")],
    {
      prerequisiteSkills: ["threadlight-production-ready"],
    },
  ),
]);
