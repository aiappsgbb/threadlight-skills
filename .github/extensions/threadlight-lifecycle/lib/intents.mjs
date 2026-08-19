import { SKILL_REGISTRY } from "./lifecycle-registry.mjs";

const PHASES = new Set([
  "design",
  "build-deploy",
  "discover",
  "protect-govern",
  "improve",
  "handoff",
]);

// Allowlist of invocable skills, sourced from the registry so a Canvas can
// never name a skill the lifecycle does not define.
const SKILL_IDS = new Set(SKILL_REGISTRY.map((definition) => definition.id));

const TYPES = new Set([
  "start_pilot",
  "resume_phase",
  "inspect_evidence",
  "invoke_skill",
  "prepare_handoff",
]);

// Path-like tokens only: no whitespace, newlines, quotes, backticks, or other
// characters that could smuggle instructions into the chat prompt.
const SAFE_ARTIFACT = /^[A-Za-z0-9][A-Za-z0-9._/-]*$/;

const PROMPT_PREFIX = "[Threadlight Canvas intent]";
const PROMPT_SUFFIX = `Treat this as a user-visible request from the active Canvas.
Explain the proposed next action in chat, resolve it to the correct
Threadlight skill or command, and preserve all normal permission and
confirmation gates before any file, process, or Azure side effect.`;

export class IntentValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "IntentValidationError";
  }
}

function fail(message) {
  throw new IntentValidationError(message);
}

function isPlainObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function requireExactKeys(intent, allowedKeys) {
  for (const key of Object.keys(intent)) {
    if (!allowedKeys.has(key)) {
      fail(`Unknown intent field: ${key}`);
    }
  }
}

function requireString(value, field) {
  if (typeof value !== "string") {
    fail(`Intent field ${field} must be a string`);
  }
  return value;
}

function trimBounded(value, field, maxLength) {
  const trimmed = value.trim();
  if (!trimmed) {
    fail(`Intent field ${field} must not be empty`);
  }
  if (trimmed.length > maxLength) {
    fail(`Intent field ${field} exceeds maximum length`);
  }
  return trimmed;
}

function validateStartPilot(intent) {
  requireExactKeys(intent, new Set(["type", "brief"]));
  if (intent.type !== "start_pilot") {
    fail("Unsupported intent type");
  }
  const brief = trimBounded(requireString(intent.brief, "brief"), "brief", 4000);
  return { type: "start_pilot", brief };
}

function validateResumePhase(intent) {
  requireExactKeys(intent, new Set(["type", "phase"]));
  if (intent.type !== "resume_phase") {
    fail("Unsupported intent type");
  }
  const phase = requireString(intent.phase, "phase");
  if (!PHASES.has(phase)) {
    fail("Invalid intent phase");
  }
  return { type: "resume_phase", phase };
}

function validateInspectEvidence(intent) {
  requireExactKeys(intent, new Set(["type", "phase", "evidenceId"]));
  if (intent.type !== "inspect_evidence") {
    fail("Unsupported intent type");
  }
  const phase = requireString(intent.phase, "phase");
  if (!PHASES.has(phase)) {
    fail("Invalid intent phase");
  }
  const evidenceId = trimBounded(
    requireString(intent.evidenceId, "evidenceId"),
    "evidenceId",
    160,
  );
  return { type: "inspect_evidence", phase, evidenceId };
}

function validatePrepareHandoff(intent) {
  requireExactKeys(intent, new Set(["type"]));
  if (intent.type !== "prepare_handoff") {
    fail("Unsupported intent type");
  }
  return { type: "prepare_handoff" };
}

function validateInvokeSkill(intent) {
  requireExactKeys(intent, new Set(["type", "skillId", "phase", "artifact"]));
  if (intent.type !== "invoke_skill") {
    fail("Unsupported intent type");
  }
  const skillId = requireString(intent.skillId, "skillId");
  if (!SKILL_IDS.has(skillId)) {
    fail("Unknown Threadlight skill id");
  }
  const result = { type: "invoke_skill", skillId };
  if (intent.phase !== undefined) {
    const phase = requireString(intent.phase, "phase");
    if (!PHASES.has(phase)) {
      fail("Invalid intent phase");
    }
    result.phase = phase;
  }
  if (intent.artifact !== undefined) {
    const artifact = trimBounded(
      requireString(intent.artifact, "artifact"),
      "artifact",
      200,
    );
    if (!SAFE_ARTIFACT.test(artifact)) {
      fail("Intent field artifact contains unsupported characters");
    }
    result.artifact = artifact;
  }
  return result;
}

export function validateIntent(input) {
  if (!isPlainObject(input)) {
    fail("Intent must be a plain object");
  }

  if (typeof input.type !== "string" || !TYPES.has(input.type)) {
    fail("Unsupported intent type");
  }

  switch (input.type) {
    case "start_pilot":
      return validateStartPilot(input);
    case "resume_phase":
      return validateResumePhase(input);
    case "inspect_evidence":
      return validateInspectEvidence(input);
    case "invoke_skill":
      return validateInvokeSkill(input);
    case "prepare_handoff":
      return validatePrepareHandoff(input);
    default:
      fail("Unsupported intent type");
  }
}

function manualSkillInstruction(intent) {
  const context = [];
  if (intent.phase) {
    context.push(`phase "${intent.phase}"`);
  }
  if (intent.artifact) {
    context.push(`artifact "${intent.artifact}"`);
  }
  const scope = context.length > 0 ? ` for ${context.join(" and ")}` : "";
  return `Manually invoke the Threadlight skill "${intent.skillId}"${scope}. Name that exact skill in chat, explain what it does, and run it only by hand after the user confirms — do not auto-run any command, tool, or live action on the Canvas's behalf.`;
}

export function createIntentBroker({ send } = {}) {
  if (typeof send !== "function") {
    throw new TypeError("createIntentBroker requires a send function");
  }

  return {
    async submit(rawIntent) {
      const intent = validateIntent(rawIntent);
      const head = `${PROMPT_PREFIX}\n${JSON.stringify(intent, null, 2)}`;
      const body =
        intent.type === "invoke_skill"
          ? `${manualSkillInstruction(intent)}\n\n${PROMPT_SUFFIX}`
          : PROMPT_SUFFIX;
      const prompt = `${head}\n\n${body}`;
      await send({ prompt });
      return { accepted: true, intent };
    },
  };
}
