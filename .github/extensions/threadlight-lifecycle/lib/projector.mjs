import { createArtifactReader } from "./artifact-reader.mjs";
import { LIFECYCLE_PHASES, SKILL_REGISTRY } from "./lifecycle-registry.mjs";

const ACTIONABLE_PHASE_STATUSES = new Set([
  "ready",
  "blocked",
  "failed",
  "stale",
  "running",
]);

const ACTIONABLE_SKILL_STATUSES = new Set([
  "ready",
  "blocked",
  "failed",
  "stale",
]);

function hasItems(value) {
  return Array.isArray(value) && value.length > 0;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isYes(value) {
  if (value === true) {
    return true;
  }
  if (typeof value !== "string") {
    return false;
  }
  return ["1", "true", "yes", "enabled"].includes(value.trim().toLowerCase());
}

function manifestSelector(manifest, key) {
  return (
    manifest?.selectors?.[key] ??
    manifest?.selectors?.[key.replaceAll("-", "_")] ??
    manifest?.[key] ??
    manifest?.[key.replaceAll("-", "_")]
  );
}

function hasTrait(manifest, trait) {
  return (
    hasItems(manifest?.traits) &&
    manifest.traits.some((candidate) => candidate === trait)
  );
}

function isSkillApplicable(definition, manifest, manifestInvalid) {
  if (definition.applicability === "always") {
    return true;
  }
  if (manifestInvalid) {
    return true;
  }

  switch (definition.applicability) {
    case "mock-systems":
      return hasItems(manifest?.mock_systems);
    case "human-approval":
      return (
        hasTrait(manifest, "human-approval") ||
        isYes(manifestSelector(manifest, "aca-bot"))
      );
    case "workspace-ui":
      return isYes(manifestSelector(manifest, "workspace-ui"));
    case "event-trigger":
      return (
        isYes(manifestSelector(manifest, "aca-job")) ||
        isYes(manifestSelector(manifest, "event-grid")) ||
        isYes(manifestSelector(manifest, "service-bus")) ||
        hasItems(manifest?.scheduled_jobs)
      );
    default:
      return true;
  }
}

function parseTimestamp(value) {
  if (typeof value !== "string" || value.trim() === "") {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function evidenceTimestamp(json, metadata) {
  if (isPlainObject(json)) {
    for (const key of ["captured_at", "generated_at", "checked_at"]) {
      const parsed = parseTimestamp(json[key]);
      if (parsed) {
        return parsed;
      }
    }
  }
  return parseTimestamp(metadata?.modifiedAt);
}

function hasHardGate(value) {
  return (
    value?.hard_gate === true ||
    value?.hardGate === true ||
    value?.["hard gate"] === true
  );
}

function evidenceStatus(value) {
  if (!isPlainObject(value)) {
    return null;
  }
  if (
    hasItems(value.must_fix) ||
    hasHardGate(value) ||
    value.go_live === "not_ready"
  ) {
    return "failed";
  }
  if (value.verdict === "partial" || hasItems(value.not_verified)) {
    return "running";
  }
  return null;
}

function firstJsonPath(requiredArtifactGroups) {
  return requiredArtifactGroups
    .flat()
    .find((artifactPath) => artifactPath.endsWith(".json"));
}

function isArtifactParseError(error) {
  return (
    error?.name === "ArtifactParseError" &&
    typeof error.relativePath === "string"
  );
}

function parseError(error) {
  return {
    code: "artifact-parse-failed",
    path: error.relativePath,
    message: error.message,
  };
}

function statusWithFreshness(status, definition, timestamp, now) {
  if (
    definition.freshnessHours === null ||
    (status !== "complete" && status !== "running")
  ) {
    return status;
  }
  if (!timestamp) {
    return "stale";
  }
  const ageHours = (now.getTime() - timestamp.getTime()) / 3_600_000;
  return ageHours > definition.freshnessHours ? "stale" : status;
}

async function collectEvidence(reader, definition) {
  const evidence = [];

  for (const group of definition.requiredArtifactGroups) {
    let match = null;
    for (const artifactPath of group) {
      const metadata = await reader.metadata(artifactPath);
      if (metadata) {
        match = {
          path: metadata.relativePath,
          kind: metadata.kind,
          modifiedAt: metadata.modifiedAt,
          size: metadata.size,
        };
        break;
      }
    }
    if (match) {
      evidence.push(match);
    }
  }

  return evidence;
}

function aggregatePhaseStatus(skills) {
  const applicable = skills.filter(
    (skill) =>
      skill.definition.affectsPhaseStatus &&
      skill.status !== "not-applicable",
  );
  if (applicable.length === 0) {
    return "not-applicable";
  }

  const statuses = applicable.map((skill) => skill.status);
  if (statuses.includes("failed")) {
    return "failed";
  }
  if (statuses.includes("blocked")) {
    return "blocked";
  }
  if (statuses.includes("stale")) {
    return "stale";
  }
  if (statuses.every((status) => status === "complete")) {
    return "complete";
  }
  if (statuses.includes("complete")) {
    return "running";
  }
  if (statuses.includes("running")) {
    return "running";
  }
  if (statuses.includes("ready")) {
    return "ready";
  }
  return "not-started";
}

function phaseBlockers(skills) {
  return skills
    .filter((skill) => skill.definition.affectsPhaseStatus)
    .flatMap((skill) => [
      ...skill.blockers,
      ...(skill.status === "ready"
        ? [`Awaiting evidence for ${skill.definition.label}`]
        : []),
    ]);
}

function phaseNextActions(skills) {
  return skills
    .filter((skill) => ACTIONABLE_SKILL_STATUSES.has(skill.status))
    .map((skill) => skill.definition.nextIntent);
}

async function readManifest(reader, errors) {
  try {
    return {
      manifest: await reader.readJson("specs/manifest.json"),
      manifestInvalid: false,
    };
  } catch (error) {
    if (isArtifactParseError(error)) {
      errors.push(parseError(error));
      return { manifest: null, manifestInvalid: true };
    }
    throw error;
  }
}

async function projectSkill({
  definition,
  reader,
  manifest,
  manifestInvalid,
  projectedSkills,
  errors,
  now,
}) {
  if (!isSkillApplicable(definition, manifest, manifestInvalid)) {
    return {
      definition,
      status: "not-applicable",
      evidence: [],
      blockers: [],
    };
  }

  const evidence = await collectEvidence(reader, definition);
  const incompletePrerequisiteId = definition.prerequisiteSkills.find((skillId) => {
    const prerequisite = projectedSkills.get(skillId);
    return (
      prerequisite &&
      prerequisite.status !== "complete" &&
      prerequisite.status !== "not-applicable"
    );
  });
  const incompletePrerequisite = incompletePrerequisiteId
    ? projectedSkills.get(incompletePrerequisiteId)
    : null;

  let status;
  if (definition.completionMode === "manual") {
    status = incompletePrerequisite ? "blocked" : "ready";
  } else if (
    definition.requiredArtifactGroups.length > 0 &&
    evidence.length === definition.requiredArtifactGroups.length
  ) {
    status = "complete";
  } else if (evidence.length > 0) {
    status = "running";
  } else if (incompletePrerequisite) {
    status = "blocked";
  } else {
    status = "ready";
  }

  if (definition.completionMode === "artifact-running" && status === "complete") {
    status = "running";
  }

  if (definition.completionArtifact && status === "complete") {
    status = (await reader.exists(definition.completionArtifact))
      ? "complete"
      : "running";
  }

  let timestamp = evidenceTimestamp(null, evidence[0]);
  const jsonPath = firstJsonPath(definition.requiredArtifactGroups);
  if (evidence.length > 0 && jsonPath) {
    try {
      let json = null;
      if (jsonPath === "specs/manifest.json") {
        json = manifest;
        if (manifestInvalid) {
          status = "failed";
        }
      } else {
        json = await reader.readJson(jsonPath);
      }
      const evidenceOverride = evidenceStatus(json);
      if (evidenceOverride) {
        status = evidenceOverride;
      }
      timestamp = evidenceTimestamp(json, evidence[0]);
    } catch (error) {
      if (!isArtifactParseError(error)) {
        throw error;
      }
      errors.push(parseError(error));
      status = "failed";
    }
  }

  status = statusWithFreshness(status, definition, timestamp, now);

  return {
    definition,
    status,
    evidence,
    blockers: incompletePrerequisite
      ? [
          `${definition.label} is waiting for ${incompletePrerequisite.definition.label}`,
        ]
      : [],
  };
}

function buildPhases(projectedSkills) {
  return LIFECYCLE_PHASES.map((phaseDefinition) => {
    const skills = SKILL_REGISTRY.filter(
      (skill) => skill.phase === phaseDefinition.id,
    ).map((skill) => projectedSkills.get(skill.id));
    return {
      ...phaseDefinition,
      status: aggregatePhaseStatus(skills),
      skills,
      blockers: phaseBlockers(skills),
      evidence: skills.flatMap((skill) => skill.evidence),
      nextActions: phaseNextActions(skills),
    };
  });
}

function choosePrimaryAction(phases) {
  const design = phases.find((phase) => phase.id === "design");
  if (design?.status === "ready") {
    return { type: "start_pilot" };
  }

  for (const phase of phases) {
    if (
      ACTIONABLE_PHASE_STATUSES.has(phase.status) &&
      phase.nextActions.length > 0
    ) {
      return phase.nextActions[0];
    }
  }

  return { type: "prepare_handoff" };
}

function summarize(phases, errors) {
  if (errors.length > 0) {
    return `${errors.length} errors`;
  }
  const completeCount = phases.filter((phase) => phase.status === "complete").length;
  return `${completeCount}/${LIFECYCLE_PHASES.length} phases complete`;
}

export async function projectWorkspace(workspace, options = {}) {
  const now =
    options.now instanceof Date ? options.now : new Date(options.now ?? Date.now());
  const reader = options.reader ?? (await createArtifactReader(workspace));
  const generatedAt = now.toISOString();
  const errors = [];
  const { manifest, manifestInvalid } = await readManifest(reader, errors);
  const projectedSkills = new Map();

  for (const definition of SKILL_REGISTRY) {
    const projected = await projectSkill({
      definition,
      reader,
      manifest,
      manifestInvalid,
      projectedSkills,
      errors,
      now,
    });
    projectedSkills.set(definition.id, projected);
  }

  const phases = buildPhases(projectedSkills);
  return {
    workspace,
    generatedAt,
    summary: summarize(phases, errors),
    phases,
    primaryAction: choosePrimaryAction(phases),
    errors,
  };
}
