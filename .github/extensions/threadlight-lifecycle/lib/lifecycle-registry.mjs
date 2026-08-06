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
