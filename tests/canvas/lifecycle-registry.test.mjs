import assert from "node:assert/strict";
import test from "node:test";

import {
  LIFECYCLE_PHASES,
  SKILL_REGISTRY,
} from "../../.github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs";
import { validateIntent } from "../../.github/extensions/threadlight-lifecycle/lib/intents.mjs";

const NEW_ADVISORY_LEGS = new Set([
  "threadlight-connect",
  "threadlight-ground",
  "threadlight-loadtest",
  "threadlight-upgrade",
]);

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
    "threadlight-upgrade",
    "threadlight-production-ready",
    "threadlight-cicd",
    "threadlight-customize",
  ]);

  const actualIds = SKILL_REGISTRY.map((skill) => skill.id);

  assert.equal(actualIds.length, 22);
  assert.equal(new Set(actualIds).size, 22);
  assert.deepEqual(new Set(actualIds), expectedIds);
});

test("registry exposes the exact phase order and memberships", () => {
  assert.deepEqual(
    LIFECYCLE_PHASES.map(({ id }) => id),
    [
      "design",
      "build-deploy",
      "discover",
      "protect-govern",
      "improve",
      "handoff",
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
    ["threadlight-deploy", ["threadlight-design"]],
    ["threadlight-safe-check", ["threadlight-deploy"]],
    ["threadlight-connect", ["threadlight-safe-check"]],
    ["threadlight-consumption-iq", ["threadlight-safe-check"]],
    ["threadlight-evals", ["threadlight-safe-check"]],
    ["threadlight-redteam", ["threadlight-safe-check"]],
    ["threadlight-ground", ["threadlight-safe-check"]],
    ["threadlight-loadtest", ["threadlight-safe-check"]],
    ["threadlight-govern", ["threadlight-safe-check"]],
    ["threadlight-router-bench", ["threadlight-evals"]],
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
    "threadlight-connect": "discover",
    "threadlight-ground": "discover",
    "threadlight-loadtest": "discover",
    "threadlight-upgrade": "improve",
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
  for (const id of [...NEW_ADVISORY_LEGS, "threadlight-qualify"]) {
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
  const named = new Set([...NEW_ADVISORY_LEGS, "threadlight-qualify"]);
  const existing = SKILL_REGISTRY.filter((skill) => !named.has(skill.id));

  // 22 registry skills minus the 5 skill-named legs leaves 17 phase intents.
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

test("skills remain declarative and have no command handlers", () => {
  for (const skill of SKILL_REGISTRY) {
    assert.equal(Object.hasOwn(skill, "command"), false, skill.id);
    assert.equal(Object.hasOwn(skill, "handler"), false, skill.id);
  }
});
