import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  LEG_ENVELOPE_CONTRACTS,
  LIFECYCLE_PHASES,
  SKILL_REGISTRY,
} from "../../.github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs";
import { validateIntent } from "../../.github/extensions/threadlight-lifecycle/lib/intents.mjs";

const NEW_ADVISORY_LEGS = new Set([
  "threadlight-agentops",
  "threadlight-connect",
  "threadlight-ground",
  "threadlight-loadtest",
  "threadlight-upgrade",
]);

// Platform and design helpers that are optional for a given pilot: they are
// listed for technical detail and can be invoked by name, but never gate a
// milestone (threadlight-auto's plan decides the gates).
const ADVISORY_PLATFORM_SKILLS = new Set([
  "threadlight-hitl-patterns",
  "threadlight-citadel-hub",
  "threadlight-citadel-spoke",
  "threadlight-mcp-aca",
  "threadlight-tenant-isolation",
]);

const LEG_SCHEMA_PATHS = {
  "specs/connect-manifest.json":
    "../../skills/threadlight-connect/references/connect-manifest.schema.json",
  "specs/ground-manifest.json":
    "../../skills/threadlight-ground/references/ground-manifest.schema.json",
  "specs/load-manifest.json":
    "../../skills/threadlight-loadtest/references/load-manifest.schema.json",
  "specs/upgrade-manifest.json":
    "../../skills/threadlight-upgrade/references/upgrade-manifest.schema.json",
};

test("leg contracts mirror producer top-level and freshness allowlists", async () => {
  for (const [artifactPath, contract] of Object.entries(
    LEG_ENVELOPE_CONTRACTS,
  )) {
    const schema = JSON.parse(
      await readFile(new URL(LEG_SCHEMA_PATHS[artifactPath], import.meta.url)),
    );

    assert.deepEqual(
      new Set(contract.allowedTopLevelKeys),
      new Set(Object.keys(schema.properties)),
      artifactPath,
    );
    assert.equal(
      schema.properties.freshness.additionalProperties,
      false,
      artifactPath,
    );
  }
});

test("registry exposes the exact lifecycle skill ids", () => {
  const expectedIds = new Set([
    "threadlight-qualify",
    "threadlight-design",
    "threadlight-demo-data-factory",
    "threadlight-event-triggers",
    "threadlight-hitl-patterns",
    "threadlight-workspace-ui",
    "threadlight-auto",
    "threadlight-local-test",
    "threadlight-deploy",
    "threadlight-safe-check",
    "threadlight-connect",
    "threadlight-consumption-iq",
    "threadlight-evals",
    "threadlight-redteam",
    "threadlight-ground",
    "threadlight-loadtest",
    "threadlight-govern",
    "threadlight-router-bench",
    "threadlight-agentops",
    "threadlight-upgrade",
    "threadlight-production-ready",
    "threadlight-cicd",
    "threadlight-customize",
    "threadlight-governed-actions",
    "threadlight-citadel-hub",
    "threadlight-citadel-spoke",
    "threadlight-mcp-aca",
    "threadlight-tenant-isolation",
  ]);

  const actualIds = SKILL_REGISTRY.map((skill) => skill.id);

  assert.equal(actualIds.length, 28);
  assert.equal(new Set(actualIds).size, 28);
  assert.deepEqual(new Set(actualIds), expectedIds);
});

test("registry exposes the exact phase order and memberships", () => {
  assert.deepEqual(
    LIFECYCLE_PHASES.map(({ id }) => id),
    [
      "design",
      "controls",
      "publish",
      "verify",
      "evaluate",
      "go-live",
    ],
  );

  const phaseIds = new Set(LIFECYCLE_PHASES.map(({ id }) => id));
  for (const skill of SKILL_REGISTRY) {
    assert.ok(phaseIds.has(skill.phase), `missing phase for ${skill.id}`);
  }
});

test("registry preserves the exact prerequisite contracts", () => {
  const expectedPrerequisites = new Map([
    ["threadlight-qualify", []],
    ["threadlight-design", []],
    ["threadlight-demo-data-factory", ["threadlight-design"]],
    ["threadlight-event-triggers", ["threadlight-design"]],
    ["threadlight-hitl-patterns", ["threadlight-design"]],
    ["threadlight-workspace-ui", ["threadlight-design"]],
    ["threadlight-auto", ["threadlight-design"]],
    ["threadlight-local-test", ["threadlight-design"]],
    ["threadlight-govern", ["threadlight-design"]],
    ["threadlight-governed-actions", ["threadlight-govern"]],
    ["threadlight-deploy", ["threadlight-design", "threadlight-governed-actions"]],
    ["threadlight-citadel-hub", []],
    ["threadlight-citadel-spoke", ["threadlight-design"]],
    ["threadlight-mcp-aca", ["threadlight-design"]],
    ["threadlight-tenant-isolation", []],
    ["threadlight-safe-check", ["threadlight-deploy"]],
    ["threadlight-connect", ["threadlight-safe-check"]],
    ["threadlight-consumption-iq", ["threadlight-safe-check"]],
    ["threadlight-evals", ["threadlight-safe-check"]],
    ["threadlight-redteam", ["threadlight-safe-check"]],
    ["threadlight-ground", ["threadlight-safe-check"]],
    ["threadlight-loadtest", ["threadlight-safe-check"]],
    ["threadlight-router-bench", ["threadlight-evals"]],
    ["threadlight-agentops", ["threadlight-safe-check"]],
    ["threadlight-upgrade", ["threadlight-safe-check"]],
    ["threadlight-production-ready", ["threadlight-safe-check"]],
    ["threadlight-cicd", ["threadlight-production-ready"]],
    ["threadlight-customize", ["threadlight-production-ready"]],
  ]);

  assert.equal(SKILL_REGISTRY.length, expectedPrerequisites.size);
  assert.deepEqual(
    new Set(SKILL_REGISTRY.map(({ id }) => id)),
    new Set(expectedPrerequisites.keys()),
  );

  for (const skill of SKILL_REGISTRY) {
    assert.deepEqual(
      skill.prerequisiteSkills,
      expectedPrerequisites.get(skill.id),
      skill.id,
    );
  }
});

test("qualify is a no-repo Design entry that does not gate the phase", () => {
  const qualify = SKILL_REGISTRY.find((s) => s.id === "threadlight-qualify");
  assert.ok(qualify, "threadlight-qualify registered");
  assert.equal(qualify.phase, "design");
  assert.deepEqual(qualify.prerequisiteSkills, []);
  assert.equal(qualify.affectsPhaseStatus, false);
  assert.deepEqual(qualify.requiredArtifactGroups, [["qualification/sizing-manifest.json"]]);
});

test("new live legs are advisory manual handoffs in their phases", () => {
  const byId = new Map(SKILL_REGISTRY.map((s) => [s.id, s]));
  const expectedPhase = {
    "threadlight-agentops": "evaluate",
    "threadlight-connect": "evaluate",
    "threadlight-ground": "evaluate",
    "threadlight-loadtest": "evaluate",
    "threadlight-upgrade": "go-live",
  };
  for (const [id, phase] of Object.entries(expectedPhase)) {
    const skill = byId.get(id);
    assert.ok(skill, `${id} registered`);
    assert.equal(skill.phase, phase, id);
    // Advisory: never gates its phase (keeps existing pilots' phase status).
    assert.equal(skill.affectsPhaseStatus, false, id);
    assert.equal(skill.role, "advisory", id);
    // Freshness-aware so a stale complete manifest is not rendered complete.
    assert.equal(skill.freshnessHours, 24, id);
  }
});

test("new advisory legs + qualify next intents name their own skill", () => {
  // Every next intent must pass the intent validator (proving it is a real,
  // permission-gated chat intent) — there is no automatic live/tool action.
  for (const id of [...NEW_ADVISORY_LEGS, ...ADVISORY_PLATFORM_SKILLS, "threadlight-qualify"]) {
    const skill = SKILL_REGISTRY.find((s) => s.id === id);
    assert.ok(skill, `${id} registered`);
    // Declarative payload names its own skill id (and phase context).
    assert.equal(skill.nextIntent.type, "invoke_skill", id);
    assert.equal(skill.nextIntent.skillId, id, id);
    assert.equal(skill.nextIntent.phase, skill.phase, id);
    // Round-trips through the validator unchanged — a real chat-mediated intent.
    assert.deepEqual(
      validateIntent({ ...skill.nextIntent }),
      { type: "invoke_skill", skillId: id, phase: skill.phase },
      id,
    );
    // Declarative: no imperative command / handler / action that could auto-run.
    assert.equal(Object.hasOwn(skill, "command"), false, id);
    assert.equal(Object.hasOwn(skill, "handler"), false, id);
    assert.equal(Object.hasOwn(skill, "action"), false, id);
    // Advisory legs remain non-gating (qualify included).
    assert.equal(skill.affectsPhaseStatus, false, id);
  }
});

test("existing phase skills keep their resume_phase next intents unchanged", () => {
  const named = new Set([
    ...NEW_ADVISORY_LEGS,
    ...ADVISORY_PLATFORM_SKILLS,
    "threadlight-qualify",
  ]);
  const existing = SKILL_REGISTRY.filter((skill) => !named.has(skill.id));

  assert.equal(existing.length, 17);
  for (const skill of existing) {
    assert.deepEqual(
      { ...skill.nextIntent },
      { type: "resume_phase", phase: skill.phase },
      skill.id,
    );
    assert.deepEqual(
      validateIntent({ ...skill.nextIntent }),
      { type: "resume_phase", phase: skill.phase },
      skill.id,
    );
  }
});

test("AgentOps is per-agent opt-in and does not replace assurance owners", () => {
  const definition = SKILL_REGISTRY.find(({ id }) => id === "threadlight-agentops");
  assert.equal(definition.phase, "evaluate");
  assert.equal(definition.applicability, "agentops-opt-in");
  assert.deepEqual(definition.requiredArtifactGroups, [["specs/agentops-manifest.json"]]);
  assert.equal(definition.freshnessHours, 24);
  assert.equal(definition.affectsPhaseStatus, false);
  assert.equal(definition.role, "advisory");
});

test("skills remain declarative and have no command handlers", () => {
  for (const skill of SKILL_REGISTRY) {
    assert.equal(Object.hasOwn(skill, "command"), false, skill.id);
    assert.equal(Object.hasOwn(skill, "handler"), false, skill.id);
  }
});

test("govern reads the real governance manifest, never the legacy govern manifest", () => {
  const govern = SKILL_REGISTRY.find(({ id }) => id === "threadlight-govern");
  assert.deepEqual(govern.requiredArtifactGroups, [["specs/governance-manifest.json"]]);
  const paths = SKILL_REGISTRY.flatMap((skill) => [
    ...skill.requiredArtifactGroups.flat(),
    ...skill.optionalArtifacts,
  ]);
  assert.equal(paths.includes("specs/govern-manifest.json"), false);
});

test("governed actions gate is registered with its real manifest", () => {
  const gate = SKILL_REGISTRY.find(({ id }) => id === "threadlight-governed-actions");
  assert.equal(gate.phase, "controls");
  assert.deepEqual(gate.requiredArtifactGroups, [["tests/governed-actions-manifest.json"]]);
  assert.equal(gate.stage, "governed_actions_gate");
});

test("human-approval design is tied to approval cards, not any bot folder", () => {
  const hitl = SKILL_REGISTRY.find(({ id }) => id === "threadlight-hitl-patterns");
  assert.deepEqual(hitl.requiredArtifactGroups, [["src/bot/cards/card_registry.json"]]);
});

test("registry stage ids map onto threadlight-auto stages in milestone order", () => {
  const byStage = Object.fromEntries(
    SKILL_REGISTRY.filter((skill) => skill.stage).map((skill) => [skill.stage, skill.id]),
  );
  assert.deepEqual(byStage, {
    design: "threadlight-design",
    govern: "threadlight-govern",
    governed_actions_gate: "threadlight-governed-actions",
    deploy: "threadlight-deploy",
    safe_check: "threadlight-safe-check",
    cost_projection: "threadlight-consumption-iq",
    agentops: "threadlight-agentops",
    evals: "threadlight-evals",
    redteam: "threadlight-redteam",
  });
  const order = LIFECYCLE_PHASES.map(({ id }) => id);
  const milestoneIndex = (id) =>
    order.indexOf(SKILL_REGISTRY.find((skill) => skill.id === id).phase);
  // Real governed chain: design -> govern -> gate -> deploy -> safe-check -> evals.
  const chain = [
    "threadlight-design",
    "threadlight-govern",
    "threadlight-governed-actions",
    "threadlight-deploy",
    "threadlight-safe-check",
    "threadlight-evals",
  ];
  for (let index = 1; index < chain.length; index += 1) {
    assert.ok(
      milestoneIndex(chain[index - 1]) <= milestoneIndex(chain[index]),
      `${chain[index - 1]} before ${chain[index]}`,
    );
  }
});
