import assert from "node:assert/strict";
import test from "node:test";

import { projectWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/projector.mjs";
import { createWorkspaceFixture } from "./fixtures.mjs";

const NOW = new Date("2026-08-06T09:00:00Z");

async function withFixture(name, callback) {
  const fixture = await createWorkspaceFixture(name);
  try {
    return await callback(fixture);
  } finally {
    await fixture.cleanup();
  }
}

function findPhase(model, id) {
  return model.phases.find((phase) => phase.id === id);
}

function findSkill(model, id) {
  for (const phase of model.phases) {
    const skill = phase.skills.find((candidate) => candidate.definition.id === id);
    if (skill) {
      return skill;
    }
  }
  return undefined;
}

test("empty workspace is ready to start pilot design", async () => {
  await withFixture("empty", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });

    assert.equal(findPhase(model, "design").status, "ready");
    assert.deepEqual(model.primaryAction, { type: "start_pilot" });
  });
});

test("design-only workspace runs design and leaves event triggers not applicable", async () => {
  await withFixture("design-only", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });

    assert.equal(findPhase(model, "design").status, "running");
    assert.equal(findSkill(model, "threadlight-event-triggers").status, "not-applicable");
    assert.equal(findSkill(model, "threadlight-workspace-ui").status, "ready");
  });
});

test("deploy-blocked workspace waits for verified deployment evidence", async () => {
  await withFixture("deploy-blocked", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });
    const phase = findPhase(model, "build-deploy");

    assert.notEqual(phase.status, "complete");
    assert.match(phase.blockers.join("\n"), /Verify the deployment/);
  });
});

test("partial assurance keeps discovery running and governance ready", async () => {
  await withFixture("partial-assurance", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });

    assert.equal(findPhase(model, "discover").status, "running");
    assert.equal(findPhase(model, "protect-govern").status, "ready");
  });
});

test("stale assurance marks threadlight evals stale", async () => {
  await withFixture("stale-assurance", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });

    assert.equal(findSkill(model, "threadlight-evals").status, "stale");
  });
});

test("partial governance evidence keeps govern skill running", async () => {
  await withFixture("complete-pilot", async ({ workspace, writeJson }) => {
    await writeJson("specs/govern-manifest.json", {
      verdict: "partial",
      not_verified: ["JWT policy"],
      must_fix: [],
    });

    const model = await projectWorkspace(workspace, { now: NOW });

    assert.equal(findSkill(model, "threadlight-govern").status, "running");
  });
});

test("complete pilot projects all phases complete without errors", async () => {
  await withFixture("complete-pilot", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });

    assert.equal(findSkill(model, "threadlight-production-ready").status, "complete");
    assert.deepEqual(
      model.phases.map((phase) => phase.status),
      ["complete", "complete", "complete", "complete", "complete", "complete"],
    );
    assert.deepEqual(model.errors, []);
  });
});
