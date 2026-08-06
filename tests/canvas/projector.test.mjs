import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { projectWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/projector.mjs";
import { createWorkspaceFixture } from "./fixtures.mjs";

const NOW = new Date("2026-08-06T09:00:00Z");
const REPO_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

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

test("canonical failed production readiness blocks handoff", async () => {
  await withFixture("complete-pilot", async ({ workspace, writeJson }) => {
    await writeJson("tests/production-readiness-manifest.json", {
      checked_at: "2026-08-06T08:00:00Z",
      go_live_recommendation: "not_ready",
      would_fail_hard_gate: true,
    });

    const model = await projectWorkspace(workspace, { now: NOW });

    assert.equal(findSkill(model, "threadlight-production-ready").status, "failed");
    assert.notEqual(findPhase(model, "handoff").status, "complete");
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

test("projects the governed returns-triage sample without secret reads", async () => {
  const workspace = path.join(REPO_ROOT, "examples/returns-triage-governed");
  const model = await projectWorkspace(workspace);
  const evidencePaths = model.phases.flatMap((phase) =>
    phase.evidence.map((item) => item.path),
  );
  const skillIds = model.phases.flatMap((phase) =>
    phase.skills.map((skill) => skill.definition.id),
  );

  assert.equal(model.phases.length, 6);
  assert.deepEqual(model.errors, []);
  assert.ok(
    evidencePaths.every(
      (itemPath) =>
        !itemPath.includes(".env") && !itemPath.split("/").includes(".azure"),
    ),
  );
  assert.ok(skillIds.includes("threadlight-govern"));
});
