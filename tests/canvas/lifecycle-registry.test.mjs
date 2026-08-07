import assert from "node:assert/strict";
import test from "node:test";

import {
  LIFECYCLE_PHASES,
  SKILL_REGISTRY,
} from "../../.github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs";

test("registry exposes the exact lifecycle skill ids", () => {
  const expectedIds = new Set([
    "threadlight-design",
    "threadlight-demo-data-factory",
    "threadlight-event-triggers",
    "threadlight-hitl-patterns",
    "threadlight-workspace-ui",
    "threadlight-auto",
    "threadlight-local-test",
    "threadlight-deploy",
    "threadlight-safe-check",
    "threadlight-consumption-iq",
    "threadlight-evals",
    "threadlight-redteam",
    "threadlight-govern",
    "threadlight-router-bench",
    "threadlight-production-ready",
    "threadlight-cicd",
    "threadlight-customize",
  ]);

  const actualIds = SKILL_REGISTRY.map((skill) => skill.id);

  assert.equal(actualIds.length, 17);
  assert.equal(new Set(actualIds).size, 17);
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
    ["threadlight-design", []],
    ["threadlight-demo-data-factory", ["threadlight-design"]],
    ["threadlight-event-triggers", ["threadlight-design"]],
    ["threadlight-hitl-patterns", ["threadlight-design"]],
    ["threadlight-workspace-ui", ["threadlight-design"]],
    ["threadlight-auto", ["threadlight-design"]],
    ["threadlight-local-test", ["threadlight-design"]],
    ["threadlight-deploy", ["threadlight-design"]],
    ["threadlight-safe-check", ["threadlight-deploy"]],
    ["threadlight-consumption-iq", ["threadlight-safe-check"]],
    ["threadlight-evals", ["threadlight-safe-check"]],
    ["threadlight-redteam", ["threadlight-safe-check"]],
    ["threadlight-govern", ["threadlight-safe-check"]],
    ["threadlight-router-bench", ["threadlight-evals"]],
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

test("skills remain declarative and have no command handlers", () => {
  for (const skill of SKILL_REGISTRY) {
    assert.equal(Object.hasOwn(skill, "command"), false, skill.id);
    assert.equal(Object.hasOwn(skill, "handler"), false, skill.id);
  }
});
