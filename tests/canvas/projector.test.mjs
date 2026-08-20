import assert from "node:assert/strict";
import path from "node:path";
import { rm } from "node:fs/promises";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { projectWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/projector.mjs";
import { createIntentBroker } from "../../.github/extensions/threadlight-lifecycle/lib/intents.mjs";
import {
  createWorkspaceFixture,
  legEnvelope,
  producerLegEnvelope,
} from "./fixtures.mjs";

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

function costActualsManifest(overrides = {}) {
  return {
    schema: "threadlight-cost-actuals/v1",
    status: "pass",
    generated_at: "2026-08-06T08:00:00Z",
    window: {
      start: "2026-08-01T00:00:00Z",
      end: "2026-08-06T00:00:00Z",
      ...(overrides.window ?? {}),
    },
    scope: {
      subscription_id: "sub-1",
      resource_group: "rg-pilot",
      ...(overrides.scope ?? {}),
    },
    cost: {
      period_total_usd: 130,
      ...(overrides.cost ?? {}),
    },
    usage: {
      successful_interactions: 1200,
      interaction_status: "pass",
      model_attribution_status: "pass",
      ...(overrides.usage ?? {}),
    },
    provenance: {
      sources: [],
      ...(overrides.provenance ?? {}),
    },
    warnings: [...(overrides.warnings ?? [])],
    ...overrides,
  };
}

function costReconciliationManifest(overrides = {}) {
  return {
    schema: "threadlight-cost-reconciliation/v1",
    status: "pass",
    generated_at: "2026-08-06T08:30:00Z",
    variance_status: "pass",
    maturity: {
      status: "pass",
      checks: [],
      ...(overrides.maturity ?? {}),
    },
    unit_economics: {
      status: "pass",
      target_status: "pass",
      ...(overrides.unit_economics ?? {}),
    },
    totals: {
      forecast_window_usd: 125,
      forecast_monthly_usd: 500,
      actual_window_usd: 130,
      variance_pct: 0.04,
      ...(overrides.totals ?? {}),
    },
    coverage: {
      projection_attribution_coverage_pct: 1,
      source_resource_id_coverage_pct: 1,
      ...(overrides.coverage ?? {}),
    },
    drivers: {
      payg_ptu: {
        status: "pass",
        observed_volume_variance_pct: 0.01,
        threshold_pct: 0.1,
      },
      ...(overrides.drivers ?? {}),
    },
    policy_snapshot: {
      max_window_end_age_days: 7,
      ...(overrides.policy_snapshot ?? {}),
    },
    policy_errors: [...(overrides.policy_errors ?? [])],
    warnings: [...(overrides.warnings ?? [])],
    ...overrides,
  };
}

function readinessManifest(overrides = {}) {
  return {
    checked_at: "2026-08-06T08:00:00Z",
    go_live_recommendation: "ready",
    would_fail_hard_gate: false,
    kpi_scorecard: {
      latency_declared: true,
      cost_per_interaction_declared: true,
      success_rate_declared: true,
      deviation_alert_present: true,
      traces_emit: true,
      eval_pass_rate: 0.99,
      cost_per_interaction_usd: 0.01,
      ...(overrides.kpi_scorecard ?? {}),
    },
    ...overrides,
  };
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

test("invalid design manifest reports only the parse failure", async () => {
  await withFixture("design-only", async ({ workspace, writeString }) => {
    await writeString("specs/manifest.json", "{\n");

    const model = await projectWorkspace(workspace, { now: NOW });
    const errors = model.errors.filter((error) => error.path === "specs/manifest.json");

    assert.deepEqual(
      errors.map((error) => error.code),
      ["artifact-parse-failed"],
    );
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

test("threadlight-consumption-iq keeps explicit null evidence state before forecast evidence exists", async () => {
  await withFixture("design-only", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });
    const skill = findSkill(model, "threadlight-consumption-iq");

    assert.equal(Object.hasOwn(skill, "evidenceState"), true);
    assert.equal(skill.evidenceState, null);
  });
});

test("threadlight-consumption-iq surfaces non-gating cost evidence state transitions", async () => {
  await withFixture("complete-pilot", async ({ workspace, writeJson }) => {
    let model = await projectWorkspace(workspace, { now: NOW });
    let skill = findSkill(model, "threadlight-consumption-iq");

    assert.equal(skill.status, "complete");
    assert.equal(skill.evidenceState, "forecast-only");

    await writeJson("specs/cost-actuals-manifest.json", costActualsManifest());
    model = await projectWorkspace(workspace, { now: NOW });
    skill = findSkill(model, "threadlight-consumption-iq");

    assert.equal(skill.status, "complete");
    assert.equal(skill.evidenceState, "actuals-collected");

    await writeJson(
      "specs/cost-reconciliation-manifest.json",
      costReconciliationManifest(),
    );
    model = await projectWorkspace(workspace, { now: NOW });
    skill = findSkill(model, "threadlight-consumption-iq");

    assert.equal(skill.status, "complete");
    assert.equal(skill.evidenceState, "reconciled");

    await writeJson(
      "specs/cost-actuals-manifest.json",
      costActualsManifest({
        scope: {
          subscription_id: "sub-2",
          resource_group: "rg-other",
        },
      }),
    );
    model = await projectWorkspace(workspace, { now: NOW });
    skill = findSkill(model, "threadlight-consumption-iq");

    assert.equal(skill.status, "complete");
    assert.equal(skill.evidenceState, "scope-mismatch");
  });
});

test("threadlight-production-ready surfaces readiness proof evidence separately from status", async () => {
  await withFixture("complete-pilot", async ({ workspace, writeJson }) => {
    await writeJson("specs/evals-manifest.json", {
      captured_at: "2026-08-06T08:00:00Z",
      verdict: "partial",
      must_fix: [],
    });
    await writeJson(
      "tests/production-readiness-manifest.json",
      readinessManifest(),
    );

    let model = await projectWorkspace(workspace, { now: NOW });
    let skill = findSkill(model, "threadlight-production-ready");

    assert.equal(skill.status, "complete");
    assert.equal(skill.evidenceState, "readiness-incomplete");

    await writeJson(
      "specs/evals-manifest.json",
      {
        captured_at: "2026-08-06T08:00:00Z",
        verdict: "comprehensive",
        must_fix: [],
      },
    );
    await writeJson(
      "tests/production-readiness-manifest.json",
      readinessManifest(),
    );

    model = await projectWorkspace(workspace, { now: NOW });
    skill = findSkill(model, "threadlight-production-ready");

    assert.equal(skill.status, "complete");
    assert.equal(skill.evidenceState, "readiness-proof");
  });
});

test("threadlight-production-ready treats missing or invalid readiness evidence as incomplete proof", async () => {
  await withFixture("complete-pilot", async ({ workspace, writeString }) => {
    await rm(path.join(workspace, "tests/production-readiness-manifest.json"));

    let model = await projectWorkspace(workspace, { now: NOW });
    let skill = findSkill(model, "threadlight-production-ready");

    assert.notEqual(skill.status, "failed");
    assert.equal(skill.evidenceState, "readiness-incomplete");

    await writeString("tests/production-readiness-manifest.json", "{\n");

    model = await projectWorkspace(workspace, { now: NOW });
    skill = findSkill(model, "threadlight-production-ready");

    assert.equal(skill.status, "failed");
    assert.equal(skill.evidenceState, "readiness-incomplete");
  });
});

test("threadlight-production-ready leaves evidence state unset before readiness work starts", async () => {
  await withFixture("empty", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });
    const skill = findSkill(model, "threadlight-production-ready");

    assert.equal(Object.hasOwn(skill, "evidenceState"), false);
  });
});

test("threadlight-consumption-iq does not claim reconciliation when target scope is unavailable", async () => {
  await withFixture("complete-pilot", async ({ workspace, writeJson }) => {
    await writeJson("specs/manifest.json", {
      traits: ["human-approval"],
      mock_systems: ["orders"],
      deployment_manifest: {
        module_selectors: {
          "workspace-ui": "yes",
          "aca-job": "no",
          "event-grid": "no",
          "service-bus": "no",
        },
        scheduled_jobs: [],
      },
    });
    await writeJson("specs/cost-actuals-manifest.json", costActualsManifest());
    await writeJson(
      "specs/cost-reconciliation-manifest.json",
      costReconciliationManifest(),
    );

    const model = await projectWorkspace(workspace, { now: NOW });
    const skill = findSkill(model, "threadlight-consumption-iq");

    assert.equal(skill.status, "complete");
    assert.equal(skill.evidenceState, "scope-mismatch");
  });
});

test("threadlight-consumption-iq surfaces invalid actuals artifacts instead of treating them as missing", async () => {
  await withFixture("complete-pilot", async ({ workspace, writeString }) => {
    await writeString("specs/cost-actuals-manifest.json", "null\n");

    const model = await projectWorkspace(workspace, { now: NOW });
    const skill = findSkill(model, "threadlight-consumption-iq");

    assert.equal(skill.status, "complete");
    assert.equal(skill.evidenceState, "actuals-invalid");
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

// ---------------------------------------------------------------------------
// Live-leg shared-envelope status projection (Task 7).
// ---------------------------------------------------------------------------

const LEG = {
  "threadlight-connect": "specs/connect-manifest.json",
  "threadlight-ground": "specs/ground-manifest.json",
  "threadlight-loadtest": "specs/load-manifest.json",
  "threadlight-upgrade": "specs/upgrade-manifest.json",
};

async function projectLeg(skillId, envelope, now = NOW) {
  return withFixture("empty", async ({ workspace, writeJson }) => {
    await writeJson(LEG[skillId], envelope);
    const model = await projectWorkspace(workspace, { now });
    return findSkill(model, skillId);
  });
}

async function projectLegModel(skillId, envelope, now = NOW) {
  return withFixture("empty", async ({ workspace, writeJson }) => {
    await writeJson(LEG[skillId], envelope);
    const model = await projectWorkspace(workspace, { now });
    return { model, skill: findSkill(model, skillId) };
  });
}

test("partial and aborted envelopes do not render complete", async () => {
  const partial = await projectLeg(
    "threadlight-ground",
    legEnvelope({ schema: "threadlight.ground/v1", status: "partial" }),
  );
  const aborted = await projectLeg(
    "threadlight-loadtest",
    legEnvelope({ schema: "threadlight.load/v1", status: "aborted" }),
  );
  assert.equal(partial.status, "running");
  assert.equal(aborted.status, "failed");
});

test("a fresh complete envelope renders complete", async () => {
  const complete = await projectLeg(
    "threadlight-connect",
    legEnvelope({
      schema: "threadlight-connect-manifest/v1",
      status: "complete",
    }),
  );
  assert.equal(complete.status, "complete");
});

test("a stale complete envelope must not render complete", async () => {
  const stale = await projectLeg(
    "threadlight-upgrade",
    legEnvelope({
      schema: "threadlight.upgrade/v1",
      status: "complete",
      // 3 days before NOW, past the envelope's own 24h validity window.
      generatedAt: "2026-08-03T09:00:00Z",
    }),
  );
  assert.equal(stale.status, "stale");
});

test("a stale partial envelope renders stale with a manual rerun action", async () => {
  const { model, skill } = await projectLegModel(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "partial",
      generatedAt: "2026-08-03T09:00:00Z",
    }),
  );

  assert.equal(skill.status, "stale");
  assert.ok(
    findPhase(model, "discover").nextActions.some(
      (intent) =>
        intent.type === "invoke_skill" &&
        intent.skillId === "threadlight-ground" &&
        intent.phase === "discover",
    ),
    "stale partial evidence offers the named manual skill rerun",
  );
});

test("a stale aborted envelope remains failed", async () => {
  const aborted = await projectLeg(
    "threadlight-loadtest",
    legEnvelope({
      schema: "threadlight.load/v1",
      status: "aborted",
      generatedAt: "2026-08-03T09:00:00Z",
    }),
  );
  assert.equal(aborted.status, "failed");
});

test("a must-fix finding fails the leg even in a complete envelope", async () => {
  const failed = await projectLeg(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "complete",
      overrides: { "GRD-001": "must-fix" },
    }),
  );
  assert.equal(failed.status, "failed");
});

test("a stale must-fix finding remains failed", async () => {
  const failed = await projectLeg(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "complete",
      generatedAt: "2026-08-03T09:00:00Z",
      overrides: { "GRD-001": "must-fix" },
    }),
  );
  assert.equal(failed.status, "failed");
});

test("a partial must-fix finding fails instead of rendering running", async () => {
  const failed = await projectLeg(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "partial",
      overrides: { "GRD-001": "must-fix" },
    }),
  );
  assert.equal(failed.status, "failed");
});

test("an expired pass finding renders stale", async () => {
  const stale = await projectLeg(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "complete",
      generatedAt: "2026-08-03T09:00:00Z",
      overrides: { "GRD-001": "pass" },
    }),
  );
  assert.equal(stale.status, "stale");
});

// --- Strict trust boundary: malformed leg evidence never renders complete ----

test("an unknown envelope status ('done') is rejected as malformed, not complete", async () => {
  const { model, skill } = await projectLegModel(
    "threadlight-connect",
    legEnvelope({ schema: "threadlight-connect-manifest/v1", status: "done" }),
  );
  assert.equal(skill.status, "failed");
  const error = model.errors.find(
    (candidate) => candidate.code === "leg-envelope-invalid",
  );
  assert.ok(error, "a payload-free leg-envelope-invalid error is surfaced");
  assert.equal(error.path, "specs/connect-manifest.json");
  // The safe error echoes only the expected shape, never the forged value.
  assert.doesNotMatch(error.message, /done/);
});

test("a leg manifest missing a required envelope key renders failed", async () => {
  const envelope = legEnvelope({ schema: "threadlight.load/v1" });
  delete envelope.tool_version;
  const skill = await projectLeg("threadlight-loadtest", envelope);
  assert.equal(skill.status, "failed");
});

test("a leg manifest with the wrong schema for its path renders failed", async () => {
  // A ground-schema envelope written to the connect leg's path: the projector
  // pins schema identity per file, so the mismatched producer is rejected.
  const skill = await projectLeg(
    "threadlight-connect",
    legEnvelope({ schema: "threadlight.ground/v1", status: "complete" }),
  );
  assert.equal(skill.status, "failed");
});

test("actual-producer-like manifests are accepted for every leg", async () => {
  const cases = [
    ["threadlight-connect", "threadlight-connect-manifest/v1"],
    ["threadlight-ground", "threadlight.ground/v1"],
    ["threadlight-loadtest", "threadlight.load/v1"],
    ["threadlight-upgrade", "threadlight.upgrade/v1"],
  ];

  for (const [skillId, schema] of cases) {
    const { model, skill } = await projectLegModel(
      skillId,
      producerLegEnvelope({ schema, status: "complete" }),
    );
    assert.equal(skill.status, "complete", skillId);
    assert.equal(
      model.errors.some((error) => error.code === "leg-envelope-invalid"),
      false,
      skillId,
    );
  }
});

test("an unknown top-level key is rejected for every leg without payload leaks", async () => {
  const cases = [
    ["threadlight-connect", "threadlight-connect-manifest/v1"],
    ["threadlight-ground", "threadlight.ground/v1"],
    ["threadlight-loadtest", "threadlight.load/v1"],
    ["threadlight-upgrade", "threadlight.upgrade/v1"],
  ];

  for (const [skillId, schema] of cases) {
    const envelope = producerLegEnvelope({ schema, status: "complete" });
    envelope["secret-customer-payload"] = `sensitive-${skillId}`;
    const { model, skill } = await projectLegModel(skillId, envelope);
    const error = model.errors.find(
      (candidate) => candidate.code === "leg-envelope-invalid",
    );

    assert.equal(skill.status, "failed", skillId);
    assert.ok(error, skillId);
    assert.equal(error.message, "manifest contains unsupported top-level key(s)");
    assert.doesNotMatch(error.message, /secret-customer-payload|sensitive-/);
  }
});

test("freshness rejects additional properties consistently for every leg", async () => {
  const cases = [
    ["threadlight-connect", "threadlight-connect-manifest/v1"],
    ["threadlight-ground", "threadlight.ground/v1"],
    ["threadlight-loadtest", "threadlight.load/v1"],
    ["threadlight-upgrade", "threadlight.upgrade/v1"],
  ];

  for (const [skillId, schema] of cases) {
    const envelope = producerLegEnvelope({ schema });
    envelope.freshness.untrusted_timestamp = "customer-payload";
    const { model, skill } = await projectLegModel(skillId, envelope);
    const error = model.errors.find(
      (candidate) => candidate.code === "leg-envelope-invalid",
    );

    assert.equal(skill.status, "failed", skillId);
    assert.equal(error?.message, "freshness contains unsupported key(s)");
    assert.doesNotMatch(error.message, /untrusted_timestamp|customer-payload/);
  }
});

test("a leg manifest with unexpected finding ids renders failed", async () => {
  const skill = await projectLeg(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "complete",
      findings: [
        { id: "GRD-001", status: "pass" },
        { id: "GRD-002", status: "pass" },
        { id: "GRD-003", status: "pass" },
        { id: "WRONG-999", status: "pass" },
      ],
    }),
  );
  assert.equal(skill.status, "failed");
});

test("a leg manifest with duplicate finding ids renders failed", async () => {
  const skill = await projectLeg(
    "threadlight-loadtest",
    legEnvelope({
      schema: "threadlight.load/v1",
      status: "complete",
      findings: [
        { id: "LOAD-001", status: "pass" },
        { id: "LOAD-001", status: "pass" },
        { id: "LOAD-002", status: "pass" },
      ],
    }),
  );
  assert.equal(skill.status, "failed");
});

test("non-object finding entries never throw and render failed", async () => {
  // Arrays / null / primitives where a finding object is expected: the
  // projector must stay throw-free (no set/hash of an unhashable JS shape) and
  // treat the evidence as malformed.
  const { model, skill } = await projectLegModel(
    "threadlight-loadtest",
    legEnvelope({
      schema: "threadlight.load/v1",
      status: "complete",
      findings: [[], null, 1],
    }),
  );
  assert.equal(skill.status, "failed");
  assert.ok(
    model.errors.some((error) => error.code === "leg-envelope-invalid"),
    "malformed findings surface a safe error rather than throwing",
  );
});

test("a non-object leg manifest renders failed without throwing", async () => {
  for (const shape of [[1, 2, 3], 42, "manifest", null]) {
    const skill = await projectLeg("threadlight-upgrade", shape);
    assert.equal(skill.status, "failed");
  }
});

test("freshness uses the envelope's own valid_for_hours (1h window => stale)", async () => {
  // Generated 2h before NOW with a 1h validity window: the leg is stale even
  // though the registry's fixed 24h window would still call it fresh.
  const skill = await projectLeg(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "complete",
      generatedAt: "2026-08-06T07:00:00Z",
      validForHours: 1,
    }),
  );
  assert.equal(skill.status, "stale");
});

test("a short validity window still renders complete inside its own hours", async () => {
  // Generated 1h before NOW with a 2h window: fresh by the envelope's clock.
  const skill = await projectLeg(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "complete",
      generatedAt: "2026-08-06T08:00:00Z",
      validForHours: 2,
    }),
  );
  assert.equal(skill.status, "complete");
});

test("a non-integer valid_for_hours is rejected as malformed", async () => {
  const skill = await projectLeg(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "complete",
      validForHours: 1.5,
    }),
  );
  assert.equal(skill.status, "failed");
});

test("an integral valid_for_hours is accepted (Draft-07 integer semantics)", async () => {
  // 24.0 collapses to 24 in JSON/JS and must be honored as an integer.
  const skill = await projectLeg(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "complete",
      validForHours: 24.0,
    }),
  );
  assert.equal(skill.status, "complete");
});

test("producer-like envelopes map partial->running, aborted->failed, complete->complete", async () => {
  // Realistic manifests carry a mix of non-must-fix finding statuses.
  const partial = await projectLeg(
    "threadlight-ground",
    legEnvelope({
      schema: "threadlight.ground/v1",
      status: "partial",
      overrides: { "GRD-002": "should-fix", "GRD-004": "not-verified" },
    }),
  );
  const aborted = await projectLeg(
    "threadlight-loadtest",
    legEnvelope({
      schema: "threadlight.load/v1",
      status: "aborted",
      overrides: { "LOAD-003": "not-verified" },
    }),
  );
  const complete = await projectLeg(
    "threadlight-upgrade",
    legEnvelope({
      schema: "threadlight.upgrade/v1",
      status: "complete",
      overrides: { "UPG-002": "should-fix" },
    }),
  );
  assert.equal(partial.status, "running");
  assert.equal(aborted.status, "failed");
  assert.equal(complete.status, "complete");
});

test("a malformed advisory leg does not disturb its phase status", async () => {
  // The connect/ground legs live in the discover phase but are advisory
  // (affectsPhaseStatus: false). A malformed manifest fails the skill without
  // changing the phase roll-up.
  await withFixture("empty", async ({ workspace, writeJson }) => {
    const baseline = await projectWorkspace(workspace, { now: NOW });
    const discoverBefore = findPhase(baseline, "discover").status;

    await writeJson(
      "specs/ground-manifest.json",
      legEnvelope({ schema: "threadlight.ground/v1", status: "done" }),
    );
    const model = await projectWorkspace(workspace, { now: NOW });

    assert.equal(findSkill(model, "threadlight-ground").status, "failed");
    assert.equal(findPhase(model, "discover").status, discoverBefore);
  });
});

test("a missing leg manifest never renders complete (blocked by prerequisites)", async () => {
  await withFixture("empty", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });
    const connect = findSkill(model, "threadlight-connect");
    // safe-check is not complete in an empty workspace -> blocked per registry
    // prerequisites; never complete.
    assert.notEqual(connect.status, "complete");
    assert.equal(connect.status, "blocked");
  });
});

test("advisory live legs do not disturb complete-pilot phase completion", async () => {
  // The complete-pilot fixture ships NO connect/ground/load/upgrade manifests.
  // Because those legs are advisory (affectsPhaseStatus: false) every phase must
  // still project as complete.
  await withFixture("complete-pilot", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });
    assert.deepEqual(
      model.phases.map((phase) => phase.status),
      ["complete", "complete", "complete", "complete", "complete", "complete"],
    );
    // The advisory legs are present in their phases but sit at `ready`.
    assert.equal(findSkill(model, "threadlight-connect").status, "ready");
    assert.equal(findSkill(model, "threadlight-upgrade").status, "ready");
  });
});

test("a projected advisory next intent brokers a named manual-invocation prompt", async () => {
  await withFixture("complete-pilot", async ({ workspace }) => {
    const model = await projectWorkspace(workspace, { now: NOW });
    const discover = findPhase(model, "discover");

    // The projector surfaces the advisory legs as skill-named invoke_skill
    // intents — not a generic resume_phase.
    const connect = discover.nextActions.find(
      (intent) => intent.skillId === "threadlight-connect",
    );
    assert.ok(connect, "connect advisory leg surfaced as a next action");
    assert.deepEqual(connect, {
      type: "invoke_skill",
      skillId: "threadlight-connect",
      phase: "discover",
    });

    // Brokering that projected intent asks chat to invoke the exact named skill
    // by hand, with the existing confirmation-gate suffix and no auto-run.
    const sent = [];
    const broker = createIntentBroker({
      send: async (payload) => {
        sent.push(payload);
      },
    });
    const result = await broker.submit(connect);

    assert.equal(result.accepted, true);
    assert.equal(sent.length, 1);
    assert.match(
      sent[0].prompt,
      /Manually invoke the Threadlight skill "threadlight-connect"/,
    );
    assert.match(
      sent[0].prompt,
      /do not auto-run any command, tool, or live action/,
    );
    assert.match(sent[0].prompt, /Explain the proposed next action in chat/);
  });
});
