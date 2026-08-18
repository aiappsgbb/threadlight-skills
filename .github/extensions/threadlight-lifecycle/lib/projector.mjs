import { createArtifactReader } from "./artifact-reader.mjs";
import {
  LEG_ENVELOPE_CONTRACTS,
  LIFECYCLE_PHASES,
  SKILL_REGISTRY,
} from "./lifecycle-registry.mjs";

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

function moduleSelectors(manifest) {
  return manifest?.deployment_manifest?.module_selectors ?? {};
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
    case "human-approval": {
      const selectors = moduleSelectors(manifest);
      return (
        hasTrait(manifest, "human-approval") ||
        isYes(selectors["aca-bot"])
      );
    }
    case "workspace-ui": {
      const selectors = moduleSelectors(manifest);
      return isYes(selectors["workspace-ui"]);
    }
    case "event-trigger": {
      const selectors = moduleSelectors(manifest);
      return (
        isYes(selectors["aca-job"]) ||
        isYes(selectors["event-grid"]) ||
        isYes(selectors["service-bus"]) ||
        hasItems(manifest?.deployment_manifest?.scheduled_jobs)
      );
    }
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

// ---------------------------------------------------------------------------
// Live-leg shared-envelope trust boundary (Task 7).
//
// The Canvas is a CONSUMER of the four live-leg manifests (connect / ground /
// load / upgrade). It must never trust a hand-forged or malformed manifest: a
// leg artifact is only projected from its *status* once its safe common
// envelope validates. A malformed leg artifact renders a non-complete
// diagnostic state (`failed`) plus a payload-free error — it never falls back
// to a presence-only `complete`. This mirrors the stdlib validation in
// threadlight-production-ready (_validate_gap_leg_envelope) so both surfaces
// reject the same evidence, and stays throw-free for any JSON shape.
// ---------------------------------------------------------------------------

const LEG_STATUS_ENUM = new Set(["complete", "partial", "aborted"]);
const LEG_FINDING_STATUS_ENUM = new Set([
  "pass",
  "must-fix",
  "should-fix",
  "not-verified",
]);
const LEG_ENVELOPE_REQUIRED_KEYS = [
  "schema",
  "tool_version",
  "generated_at",
  "freshness",
  "status",
  "findings",
];
const LEG_FRESHNESS_KEYS = new Set(["valid_for_hours", "source_oldest_at"]);
// Sane ceiling shared with production-ready (1 year of hours). A leg that claims
// a longer validity window is treated as forged.
const LEG_MAX_VALID_FOR_HOURS = 8760;

// Strict RFC 3339 date-time with a MANDATORY timezone offset (Z/z or ±HH:MM) and
// a T/t separator — intentionally stricter than the Date constructor (which
// accepts naive datetimes, a space separator, and bare dates). Mirrors
// skills/_shared/manifest.py and production-ready so the consumer refuses
// exactly what the producer's `format: date-time` contract forbids.
const LEG_RFC3339_RE =
  /^\d{4}-\d{2}-\d{2}[Tt](?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/;

function isLegRfc3339(value) {
  if (typeof value !== "string" || !LEG_RFC3339_RE.test(value)) {
    return false;
  }
  // The regex fixes the overall shape and the clock/offset digit ranges; only an
  // impossible calendar day (month 13, 2026-02-30) still slips through, so
  // validate the date triple with an explicit UTC round-trip.
  const [, y, m, d] = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  const year = Number(y);
  const month = Number(m);
  const day = Number(d);
  const probe = new Date(Date.UTC(year, month - 1, day));
  return (
    probe.getUTCFullYear() === year &&
    probe.getUTCMonth() === month - 1 &&
    probe.getUTCDate() === day
  );
}

function isDraft7Integer(value) {
  // JSON-Schema Draft-07 integer semantics: an integral float such as 24.0 IS an
  // integer (JSON/JS collapse 24.0 to 24), a bool / 1.5 / NaN / Infinity is not.
  // `typeof value === "number"` excludes booleans, and Number.isInteger already
  // rejects NaN/Infinity and any fractional value.
  return typeof value === "number" && Number.isInteger(value);
}

function validateLegEnvelope(value, contract) {
  if (!isPlainObject(value)) {
    return { valid: false, reason: "manifest must be an object" };
  }
  for (const key of LEG_ENVELOPE_REQUIRED_KEYS) {
    if (!Object.hasOwn(value, key)) {
      return { valid: false, reason: `missing required key: ${key}` };
    }
  }
  const allowedTopLevelKeys = new Set(contract.allowedTopLevelKeys);
  if (Object.keys(value).some((key) => !allowedTopLevelKeys.has(key))) {
    return {
      valid: false,
      reason: "manifest contains unsupported top-level key(s)",
    };
  }
  // Per-file identity: the schema string pins which producer emitted the file.
  if (value.schema !== contract.schema) {
    return { valid: false, reason: `schema is not ${contract.schema}` };
  }
  if (typeof value.tool_version !== "string" || value.tool_version === "") {
    return { valid: false, reason: "tool_version must be a non-empty string" };
  }
  if (!isLegRfc3339(value.generated_at)) {
    return {
      valid: false,
      reason: "generated_at must be an RFC3339 timestamp with a timezone",
    };
  }

  const freshness = value.freshness;
  if (!isPlainObject(freshness)) {
    return { valid: false, reason: "freshness must be an object" };
  }
  for (const key of LEG_FRESHNESS_KEYS) {
    if (!Object.hasOwn(freshness, key)) {
      return { valid: false, reason: `freshness missing required key: ${key}` };
    }
  }
  for (const key of Object.keys(freshness)) {
    if (!LEG_FRESHNESS_KEYS.has(key)) {
      return { valid: false, reason: "freshness contains unsupported key(s)" };
    }
  }
  const validForHours = freshness.valid_for_hours;
  if (!isDraft7Integer(validForHours) || validForHours <= 0) {
    return {
      valid: false,
      reason: "freshness.valid_for_hours must be a positive integer",
    };
  }
  if (validForHours > LEG_MAX_VALID_FOR_HOURS) {
    return {
      valid: false,
      reason: `freshness.valid_for_hours must be at most ${LEG_MAX_VALID_FOR_HOURS}`,
    };
  }
  // source_oldest_at may inform display detail but is NOT used for expiry.
  const sourceOldestAt = freshness.source_oldest_at;
  if (sourceOldestAt !== null && !isLegRfc3339(sourceOldestAt)) {
    return {
      valid: false,
      reason: "freshness.source_oldest_at must be null or an RFC3339 timestamp",
    };
  }

  if (typeof value.status !== "string" || !LEG_STATUS_ENUM.has(value.status)) {
    return { valid: false, reason: "status must be one of complete|partial|aborted" };
  }

  const findings = value.findings;
  if (!Array.isArray(findings)) {
    return { valid: false, reason: "findings must be a list" };
  }
  const requiredIds = contract.findingIds;
  if (findings.length !== requiredIds.length) {
    return {
      valid: false,
      reason: "findings must contain exactly the expected ids (one each)",
    };
  }
  const seenIds = new Set();
  for (const finding of findings) {
    if (!isPlainObject(finding) || typeof finding.id !== "string") {
      return { valid: false, reason: "findings contains a malformed entry" };
    }
    if (
      typeof finding.status !== "string" ||
      !LEG_FINDING_STATUS_ENUM.has(finding.status)
    ) {
      return { valid: false, reason: "findings contains an invalid status" };
    }
    seenIds.add(finding.id);
  }
  // Exactly the required ids, one each: no missing, duplicate, or unknown id.
  if (seenIds.size !== requiredIds.length || !requiredIds.every((id) => seenIds.has(id))) {
    return {
      valid: false,
      reason: "findings must contain exactly the expected ids (one each)",
    };
  }
  return { valid: true };
}

function isLegFresh(envelope, now) {
  // Freshness for a valid leg envelope is anchored on generated_at +
  // freshness.valid_for_hours — the envelope's OWN window, never the registry's
  // fixed hours. source_oldest_at is deliberately not used for expiry. Uppercase
  // normalizes a lowercase t/z separator the Date parser may reject (the string
  // is validated RFC3339, so it contains no other letters).
  const generatedAt = parseTimestamp(envelope.generated_at.toUpperCase());
  if (!generatedAt) {
    return false;
  }
  const ageHours = (now.getTime() - generatedAt.getTime()) / 3_600_000;
  return ageHours >= 0 && ageHours <= envelope.freshness.valid_for_hours;
}

function projectLegStatus(json, contract, now) {
  const result = validateLegEnvelope(json, contract);
  if (!result.valid) {
    // Malformed / untrusted evidence: a non-complete diagnostic state plus a
    // payload-free error. Never a presence-only `complete`.
    return { status: "failed", error: result.reason };
  }
  // An aborted run can never render as complete.
  if (json.status === "aborted") {
    return { status: "failed" };
  }
  // Expiry is resolved before complete/partial or finding-status mapping. Once
  // evidence expires it can only request a manual leg rerun; it cannot keep
  // reporting either active progress or a current result.
  if (!isLegFresh(json, now)) {
    return { status: "stale" };
  }
  // Negative evidence dominates: any must-fix finding fails the leg regardless
  // of a fresh complete/partial envelope's own status.
  const hasMustFix = json.findings.some(
    (finding) => isPlainObject(finding) && finding.status === "must-fix",
  );
  if (hasMustFix) {
    return { status: "failed" };
  }
  if (json.status === "partial") {
    return { status: "running" };
  }
  return { status: "complete" };
}

function evidenceStatus(value) {
  if (!isPlainObject(value)) {
    return null;
  }
  if (
    hasItems(value.must_fix) ||
    value.would_fail_hard_gate === true ||
    value.go_live_recommendation === "not_ready"
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
  const legContract = jsonPath ? LEG_ENVELOPE_CONTRACTS[jsonPath] : undefined;
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
      if (legContract) {
        // Live-leg artifact: only project from its status once the safe common
        // envelope validates. A malformed manifest renders `failed` + a
        // payload-free error, never a presence-only `complete`. Freshness is
        // resolved from the envelope's own valid_for_hours here, so the generic
        // registry-hours freshness step below is skipped for legs.
        const projected = projectLegStatus(json, legContract, now);
        status = projected.status;
        if (projected.error) {
          errors.push({
            code: "leg-envelope-invalid",
            path: jsonPath,
            message: projected.error,
          });
        }
      } else {
        const evidenceOverride = evidenceStatus(json);
        if (evidenceOverride) {
          status = evidenceOverride;
        }
        timestamp = evidenceTimestamp(json, evidence[0]);
      }
    } catch (error) {
      if (!isArtifactParseError(error)) {
        throw error;
      }
      errors.push(parseError(error));
      status = "failed";
    }
  }

  if (!legContract) {
    status = statusWithFreshness(status, definition, timestamp, now);
  }

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
