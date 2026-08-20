import {
  ArtifactAccessError,
  createArtifactReader,
} from "./artifact-reader.mjs";
import {
  LEG_ENVELOPE_CONTRACTS,
  LIFECYCLE_PHASES,
  SKILL_REGISTRY,
} from "./lifecycle-registry.mjs";

const COST_ACTUALS_SCHEMA = "threadlight-cost-actuals/v1";
const COST_RECONCILIATION_SCHEMA = "threadlight-cost-reconciliation/v1";
const READINESS_REQUIRED_BOOLEANS = Object.freeze([
  "latency_declared",
  "cost_per_interaction_declared",
  "success_rate_declared",
  "deviation_alert_present",
  "traces_emit",
]);

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

function parseRfc3339Timestamp(value) {
  if (typeof value !== "string" || value.trim() === "") {
    return null;
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})[Tt]([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(\.\d+)?([Zz]|([+-])([01]\d|2[0-3]):([0-5]\d))$/.exec(
    value.trim(),
  );
  if (!match) {
    return null;
  }
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return null;
  }
  const [
    ,
    year,
    month,
    day,
    hour,
    minute,
    second,
    fractionalSeconds = "",
    zone,
    offsetSign,
    offsetHour,
    offsetMinute,
  ] = match;
  const milliseconds = fractionalSeconds
    ? Number(fractionalSeconds.slice(1).padEnd(3, "0").slice(0, 3))
    : 0;
  const offsetMinutes = /^[Zz]$/.test(zone)
    ? 0
    : (offsetSign === "+" ? 1 : -1) * (Number(offsetHour) * 60 + Number(offsetMinute));
  const adjusted = new Date(parsed.getTime() + offsetMinutes * 60_000);
  return adjusted.getUTCFullYear() === Number(year) &&
      adjusted.getUTCMonth() === Number(month) - 1 &&
      adjusted.getUTCDate() === Number(day) &&
      adjusted.getUTCHours() === Number(hour) &&
      adjusted.getUTCMinutes() === Number(minute) &&
      adjusted.getUTCSeconds() === Number(second) &&
      adjusted.getUTCMilliseconds() === milliseconds
    ? parsed
    : null;
}

function postdeployCheckedAt(postdeploy, now) {
  const checkedAt = parseRfc3339Timestamp(postdeploy?.checked_at);
  if (!checkedAt || checkedAt.getTime() > now.getTime()) {
    return null;
  }
  return checkedAt;
}

function evidenceTimestamp(json, metadata, jsonPath, now) {
  if (isPlainObject(json)) {
    if (jsonPath === "tests/postdeploy-manifest.json") {
      return postdeployCheckedAt(json, now);
    }
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
  // Negative evidence dominates freshness and completeness: a trusted must-fix
  // remains failed even when the envelope is partial or expired.
  const hasMustFix = json.findings.some(
    (finding) => isPlainObject(finding) && finding.status === "must-fix",
  );
  if (hasMustFix) {
    return { status: "failed" };
  }
  // Without negative evidence, expired complete/partial evidence requests a
  // manual rerun rather than reporting active progress or a current result.
  if (!isLegFresh(json, now)) {
    return { status: "stale" };
  }
  if (json.status === "partial") {
    return { status: "running" };
  }
  return { status: "complete" };
}

function evidenceStatus(value, jsonPath, now) {
  if (jsonPath === "tests/postdeploy-manifest.json") {
    return isGreenPostdeployManifest(value) && postdeployCheckedAt(value, now) ? null : "failed";
  }
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

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function nonEmptyText(value) {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

function hasScopeMatch(actualScope, expectedScope) {
  const actualSubscription = nonEmptyText(actualScope?.subscription_id);
  const actualResourceGroup = nonEmptyText(actualScope?.resource_group);
  const expectedSubscription = nonEmptyText(expectedScope?.subscription_id);
  const expectedResourceGroup = nonEmptyText(expectedScope?.resource_group);
  if (
    !actualSubscription ||
    !actualResourceGroup ||
    !expectedSubscription ||
    !expectedResourceGroup
  ) {
    return true;
  }
  return (
    actualSubscription.toLowerCase() === expectedSubscription.toLowerCase() &&
    actualResourceGroup.toLowerCase() === expectedResourceGroup.toLowerCase()
  );
}

function isValidActualsManifest(actuals) {
  return (
    isPlainObject(actuals) &&
    actuals.schema === COST_ACTUALS_SCHEMA &&
    actuals.status === "pass" &&
    isPlainObject(actuals.window) &&
    nonEmptyText(actuals.window.start) &&
    nonEmptyText(actuals.window.end) &&
    isPlainObject(actuals.scope) &&
    nonEmptyText(actuals.scope.subscription_id) &&
    nonEmptyText(actuals.scope.resource_group)
  );
}

function classifyReconciliation(reconciliation) {
  if (
    !isPlainObject(reconciliation) ||
    reconciliation.schema !== COST_RECONCILIATION_SCHEMA ||
    !isPlainObject(reconciliation.maturity) ||
    typeof reconciliation.status !== "string" ||
    typeof reconciliation.maturity.status !== "string"
  ) {
    return "reconciliation-invalid";
  }
  return reconciliation.status === "pass" && reconciliation.maturity.status === "pass"
    ? "reconciled"
    : "reconciliation-not-verified";
}

function isGreenPostdeployManifest(postdeploy) {
  return (
    isPlainObject(postdeploy) &&
    postdeploy.phase === "post-deploy" &&
    Array.isArray(postdeploy.gaps) &&
    postdeploy.gaps.length === 0
  );
}

function stableJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (isPlainObject(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function postdeployBindsManifest(postdeploy, manifest) {
  const snapshot = postdeploy?.deployment_manifest;
  const current = manifest?.deployment_manifest;
  return isPlainObject(snapshot) &&
    isPlainObject(current) &&
    stableJson(snapshot) === stableJson(current);
}

async function readOptionalJson(reader, relativePath) {
  if (!(await reader.exists(relativePath))) {
    return { state: "missing", value: null };
  }
  try {
    const value = await reader.readJson(relativePath);
    return value === null ? { state: "invalid", value: null } : { state: "present", value };
  } catch (error) {
    if (isArtifactParseError(error)) {
      return { state: "invalid", value: null };
    }
    throw error;
  }
}

async function costEvidenceState(reader, manifest) {
  if (!isPlainObject(manifest)) {
    return null;
  }
  const forecast = await readOptionalJson(reader, "specs/cost-manifest.json");
  if (forecast.state !== "present" || !isPlainObject(forecast.value)) {
    return null;
  }

  const actuals = await readOptionalJson(reader, "specs/cost-actuals-manifest.json");
  if (actuals.state === "missing") {
    return "forecast-only";
  }
  if (actuals.state === "invalid" || !isValidActualsManifest(actuals.value)) {
    return "actuals-invalid";
  }
  const expectedScope = {
    subscription_id: manifest?.deployment_manifest?.subscription_id,
    resource_group: manifest?.deployment_manifest?.resource_group,
  };
  if (
    !nonEmptyText(expectedScope.subscription_id) ||
    !nonEmptyText(expectedScope.resource_group)
  ) {
    return "scope-mismatch";
  }
  if (
    !hasScopeMatch(actuals.value.scope, expectedScope)
  ) {
    return "scope-mismatch";
  }

  const reconciliation = await readOptionalJson(
    reader,
    "specs/cost-reconciliation-manifest.json",
  );
  if (reconciliation.state === "missing") {
    return "actuals-collected";
  }
  if (reconciliation.state === "invalid") {
    return "reconciliation-invalid";
  }
  return classifyReconciliation(reconciliation.value);
}

function readinessEvidenceState(assurance, readiness, safeCheckStatus) {
  if (
    safeCheckStatus !== "complete" ||
    assurance?.govern !== "governed" ||
    assurance?.evals !== "comprehensive" ||
    assurance?.redteam !== "hardened" ||
    !isPlainObject(readiness) ||
    readiness.go_live_recommendation !== "ready" ||
    readiness.would_fail_hard_gate !== false
  ) {
    return "readiness-incomplete";
  }

  const kpiScorecard = readiness.kpi_scorecard;
  if (!isPlainObject(kpiScorecard)) {
    return "readiness-incomplete";
  }
  if (
    READINESS_REQUIRED_BOOLEANS.some((field) => kpiScorecard[field] !== true) ||
    !isFiniteNumber(kpiScorecard.eval_pass_rate) ||
    !isFiniteNumber(kpiScorecard.cost_per_interaction_usd)
  ) {
    return "readiness-incomplete";
  }
  return "readiness-proof";
}

async function skillEvidenceState(definition, reader, manifest, projectedSkills) {
  if (definition.id === "threadlight-consumption-iq") {
    return costEvidenceState(reader, manifest);
  }
  if (definition.id === "threadlight-production-ready") {
    const [govern, evals, redteam, readiness, postdeploy] = await Promise.all([
      readOptionalJson(reader, "specs/govern-manifest.json"),
      readOptionalJson(reader, "specs/evals-manifest.json"),
      readOptionalJson(reader, "specs/redteam-manifest.json"),
      readOptionalJson(reader, "tests/production-readiness-manifest.json"),
      readOptionalJson(reader, "tests/postdeploy-manifest.json"),
    ]);
    const hasAnyReadinessEvidence = [govern, evals, redteam, readiness, postdeploy].some(
      (artifact) => artifact.state !== "missing",
    );
    if (!hasAnyReadinessEvidence) {
      return undefined;
    }
    const safeCheckStatus = projectedSkills.get("threadlight-safe-check")?.status ?? null;
    return readinessEvidenceState(
      {
        govern: govern.value?.verdict ?? null,
        evals: evals.value?.verdict ?? null,
        redteam: redteam.value?.verdict ?? null,
      },
      readiness.value,
      safeCheckStatus,
    );
  }
  return undefined;
}

async function hasDeployCompletionEvidence(reader) {
  try {
    const azdEnvDirs = await reader.readDir(".azure");
    if (!Array.isArray(azdEnvDirs) || azdEnvDirs.length !== 1) {
      return false;
    }

    for (const envDir of azdEnvDirs) {
      const value = parseEnvAssignment(
        await reader.readAzdEnvValue(envDir, "AGENT_FQDN"),
      );
      if (value) {
        return true;
      }
    }
    return false;
  } catch (error) {
    if (error instanceof ArtifactAccessError) {
      return false;
    }
    throw error;
  }
}

function parseEnvAssignment(value) {
  if (typeof value !== "string") {
    return "";
  }
  if (value.trimStart().startsWith("#")) {
    return "";
  }
  const trimmed = value.split(/\s+#/u, 1)[0].trim();
  if (
    trimmed.length >= 2 &&
    (trimmed.startsWith('"') && trimmed.endsWith('"') ||
      trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1).trim();
  }
  return trimmed;
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

function statusWithFreshness(status, definition, timestamp, now, jsonPath = null) {
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
  if (jsonPath === "tests/postdeploy-manifest.json") {
    return ageHours >= definition.freshnessHours ? "stale" : status;
  }
  return ageHours > definition.freshnessHours ? "stale" : status;
}

async function collectEvidence(reader, definition) {
  const requiredEvidence = [];

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
      requiredEvidence.push(match);
    }
  }

  const evidence = [...requiredEvidence];
  for (const artifactPath of definition.optionalArtifacts) {
    const metadata = await reader.metadata(artifactPath);
    if (metadata) {
      evidence.push({
        path: metadata.relativePath,
        kind: metadata.kind,
        modifiedAt: metadata.modifiedAt,
        size: metadata.size,
      });
    }
  }

  return { requiredEvidence, evidence };
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

  const { requiredEvidence, evidence } = await collectEvidence(reader, definition);
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
    requiredEvidence.length === definition.requiredArtifactGroups.length
  ) {
    status = "complete";
  } else if (requiredEvidence.length > 0) {
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

  const jsonPath = firstJsonPath(definition.requiredArtifactGroups);
  let timestamp = evidenceTimestamp(null, evidence[0], jsonPath, now);
  const legContract = jsonPath ? LEG_ENVELOPE_CONTRACTS[jsonPath] : undefined;
  if (evidence.length > 0 && jsonPath) {
    try {
      let json = null;
      const jsonExists = await reader.exists(jsonPath);
      if (jsonExists && jsonPath === "specs/manifest.json") {
        json = manifest;
        if (manifestInvalid) {
          status = "failed";
        }
      } else if (jsonExists) {
        json = await reader.readJson(jsonPath);
      }
      if (
        jsonExists &&
        json === null &&
        !(jsonPath === "specs/manifest.json" && manifestInvalid)
      ) {
        status = "failed";
        errors.push({
          code: "artifact-invalid",
          path: jsonPath,
          message: "Required JSON evidence must not be null.",
        });
      } else if (jsonExists && legContract) {
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
      } else if (jsonExists) {
        const evidenceOverride = evidenceStatus(json, jsonPath, now);
        if (evidenceOverride) {
          status = evidenceOverride;
        }
        if (
          definition.id === "threadlight-safe-check" &&
          !postdeployBindsManifest(json, manifest)
        ) {
          status = "failed";
          errors.push({
            code: "artifact-invalid",
            path: jsonPath,
            message:
              "Post-deploy proof no longer matches specs/manifest.json. Re-run threadlight-safe-check.",
          });
        }
        timestamp = evidenceTimestamp(json, evidence[0], jsonPath, now);
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
    status = statusWithFreshness(status, definition, timestamp, now, jsonPath);
  }

  if (
    definition.id === "threadlight-deploy" &&
    status === "complete" &&
    !(await hasDeployCompletionEvidence(reader))
  ) {
    status = "running";
  }

  if (
    incompletePrerequisite &&
    status === "complete" &&
    (
      definition.id === "threadlight-safe-check" ||
      definition.id === "threadlight-production-ready"
    )
  ) {
    status = "blocked";
  }

  const evidenceState = await skillEvidenceState(
    definition,
    reader,
    manifest,
    projectedSkills,
  );

  if (
    definition.id === "threadlight-production-ready" &&
    status === "complete" &&
    evidenceState !== undefined &&
    evidenceState !== "readiness-proof"
  ) {
    status = "running";
  }

  return {
    definition,
    status,
    ...(evidenceState !== undefined ? { evidenceState } : {}),
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
