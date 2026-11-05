import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  STAGE_CATALOG,
  assessPlan,
  buildProcessModel,
  parseSpec,
} from "../../.github/extensions/threadlight-lifecycle/lib/process-model.mjs";
import { validateIntent } from "../../.github/extensions/threadlight-lifecycle/lib/intents.mjs";

const EXAMPLE_SPEC = new URL(
  "../../examples/returns-triage-governed/specs/SPEC.md",
  import.meta.url,
);
const DECIDED_AT = "2026-08-06T08:30:00+00:00";
const GOVERNED = [
  "preflight",
  "design",
  "govern",
  "governed_actions_gate",
  "deploy",
  "governance_probe",
  "safe_check",
  "cost_projection",
  "invoke",
  "agentops",
  "evals",
  "redteam",
];

function plan(decisions, { stages = GOVERNED, nextAction } = {}) {
  const rows = stages.map((stage) => ({
    stage,
    decision: decisions[stage]?.decision ?? "skip",
    reason: decisions[stage]?.reason ?? `${stage} evidence present`,
    artifacts_seen: decisions[stage]?.seen ?? [],
    artifacts_missing: decisions[stage]?.missing ?? [],
    hard_stop_signature: null,
  }));
  const hardStop = rows.find((row) => row.decision === "hard_stop");
  return {
    stages,
    decisions: rows,
    manual_handoffs: [],
    next_action:
      nextAction ??
      (hardStop
        ? { type: "hard_stop", stage: hardStop.stage, reason: hardStop.reason }
        : {
            type: "run",
            stages_to_run: rows.filter((row) => row.decision === "run").map((row) => row.stage),
            stages_to_skip: rows.filter((row) => row.decision === "skip").map((row) => row.stage),
          }),
    decided_at: DECIDED_AT,
  };
}

async function exampleSpec() {
  return parseSpec(await readFile(EXAMPLE_SPEC, "utf8"));
}

function gates(view) {
  return view.proofPath.milestones.flatMap((milestone) => milestone.gates);
}

function base(overrides = {}) {
  return {
    spec: null,
    manifest: { name: "returns-triage" },
    plan: null,
    planStatus: { status: "missing", decidedAt: null },
    phases: [],
    governanceText: null,
    cost: null,
    readiness: null,
    generatedAt: "2026-08-06T09:00:00.000Z",
    ...overrides,
  };
}

test("parseSpec reads the process in the owner's own words", async () => {
  const spec = await exampleSpec();

  assert.equal(spec.name, "Returns Triage");
  assert.match(spec.description, /^When a customer requests a return/);
  assert.deepEqual(
    spec.steps.map((step) => step.title),
    ["Intake & correlation", "Eligibility check", "Risk & value gate", "Decision & audit"],
  );
  assert.equal(spec.steps[0].actor, "agent");
  assert.deepEqual(
    spec.rules.map((rule) => rule.id),
    ["BR-001", "BR-002", "BR-003", "BR-004", "BR-005"],
  );
  assert.equal(spec.rules[2].title, "Escalate high-value or high-risk cases");
  const review = spec.decisions.find((item) => item.title === "Supervisor escalation review");
  assert.ok(review, "§ 8 decision point parsed");
  assert.deepEqual(review.rules, ["BR-003", "BR-005"]);
  assert.equal(review.actor, "Returns supervisor");
  assert.equal(spec.scenarios.length, 9);
  assert.deepEqual(
    spec.scenarios.find((scenario) => scenario.id === "S-004").rules,
    ["BR-003", "BR-005"],
  );
  const citation = spec.kpis.find((kpi) => kpi.name === "policy_citation_rate");
  assert.deepEqual(citation, { rule: "BR-005", name: "policy_citation_rate", target: "100%" });
});

test("parseSpec tolerates missing sections and never throws", () => {
  assert.equal(parseSpec(null), null);
  const spec = parseSpec("# Pilot spec\n");
  assert.equal(spec.name, "Pilot spec");
  assert.deepEqual(spec.rules, []);
  assert.deepEqual(spec.steps, []);
  assert.deepEqual(spec.decisions, []);
  assert.deepEqual(spec.kpis, []);
});

test("assessPlan distinguishes missing, invalid, stale and current plans", () => {
  assert.equal(assessPlan(null, {}).status, "missing");
  assert.equal(assessPlan({ stages: "x" }, {}).status, "invalid");
  assert.equal(assessPlan({ ...plan({}), decisions: [{ stage: "deploy", decision: "maybe" }] }, {}).status, "invalid");
  assert.equal(assessPlan({ ...plan({}), decided_at: "yesterday" }, {}).status, "invalid");

  const current = assessPlan(plan({}), { newestArtifactAt: "2026-08-06T08:00:00Z" });
  assert.equal(current.status, "current");
  assert.equal(current.decidedAt, "2026-08-06T08:30:00.000Z");

  const stale = assessPlan(plan({}), {
    newestArtifactAt: "2026-08-06T08:45:00Z",
    newestArtifactPath: "specs/SPEC.md",
  });
  assert.equal(stale.status, "stale");
  assert.equal(stale.changedPath, "specs/SPEC.md");
});

test("empty workspace offers only the describe-your-process start", () => {
  const view = buildProcessModel(base({ manifest: null }));

  assert.equal(view.empty, true);
  assert.equal(view.now.action.label, "Describe your process");
  assert.deepEqual(view.now.action.intent, { type: "start_pilot" });
});

test("a current plan names one action at the root gate and waits the rest on it", async () => {
  const view = buildProcessModel(
    base({
      spec: await exampleSpec(),
      plan: plan({
        deploy: {
          decision: "run",
          reason: "infra + azure.yaml exist but no AGENT_FQDN in azd env",
          seen: ["infra/main.bicep", "azure.yaml"],
        },
        governance_probe: { decision: "run" },
        safe_check: { decision: "run" },
        cost_projection: { decision: "run" },
        invoke: { decision: "run" },
        agentops: { decision: "skip", reason: "AgentOps not opted in." },
        evals: { decision: "run" },
        redteam: { decision: "run" },
      }),
      planStatus: { status: "current", decidedAt: "2026-08-06T08:30:00.000Z" },
    }),
  );

  assert.equal(view.empty, false);
  assert.equal(view.pilot.name, "Returns Triage");
  assert.equal(view.now.headline, "Designed and prepared offline — not yet live on Azure");
  assert.equal(view.now.blocker, STAGE_CATALOG.deploy.need);
  assert.deepEqual(view.now.action.intent, { type: "advance_plan", stage: "deploy" });
  assert.equal(view.now.action.label, STAGE_CATALOG.deploy.action);
  assert.doesNotThrow(() => validateIntent(view.now.action.intent));

  const all = gates(view);
  assert.deepEqual(all.map((gate) => gate.stage), GOVERNED);
  assert.equal(all.filter((gate) => gate.current).length, 1);
  const deploy = all.find((gate) => gate.stage === "deploy");
  assert.equal(deploy.state, "prepared");
  assert.equal(deploy.stateLabel, "Prepared offline");
  const waiting = all.filter((gate) => gate.state === "waiting");
  assert.deepEqual(
    waiting.map((gate) => gate.stage),
    ["governance_probe", "safe_check", "cost_projection", "invoke", "evals", "redteam"],
  );
  assert.ok(waiting.every((gate) => gate.waitingOn === "deploy"));
  assert.equal(all.find((gate) => gate.stage === "agentops").state, "skipped");
  assert.equal(all.find((gate) => gate.stage === "design").state, "done");
  assert.equal(all.find((gate) => gate.stage === "govern").state, "prepared");

  const publish = view.proofPath.milestones.find((milestone) => milestone.id === "publish");
  assert.equal(publish.current, true);
  assert.equal(view.proofPath.milestones.filter((milestone) => milestone.current).length, 1);
  // The root cause appears once; downstream gates do not repeat it.
  const blockerMentions = all.filter((gate) => gate.detail === STAGE_CATALOG.deploy.need);
  assert.equal(blockerMentions.length, 1);
});

test("a hard stop is shown as blocked with its plain reason", () => {
  const view = buildProcessModel(
    base({
      spec: parseSpec("# SpecKit: Claims\n\n**Name**: Claims intake\n"),
      plan: plan({
        preflight: { decision: "run" },
        design: { decision: "run" },
        govern: {
          decision: "hard_stop",
          reason: "Governance configuration invalid or unresolved; not verified. Deployment blocked.",
        },
        governed_actions_gate: { decision: "run" },
        deploy: { decision: "run" },
      }),
      planStatus: { status: "current", decidedAt: "2026-08-06T08:30:00.000Z" },
    }),
  );

  assert.equal(view.now.tone, "blocked");
  assert.match(view.now.blocker, /Governance configuration invalid/);
  assert.deepEqual(view.now.action.intent, { type: "advance_plan", stage: "govern" });
  const govern = gates(view).find((gate) => gate.stage === "govern");
  assert.equal(govern.state, "failed");
  assert.equal(govern.current, true);
  assert.equal(gates(view).find((gate) => gate.stage === "deploy").waitingOn, "govern");
  assert.equal(gates(view).find((gate) => gate.stage === "preflight").state, "todo");
});

test("a missing plan asks to refresh the plan instead of guessing", async () => {
  const view = buildProcessModel(base({ spec: await exampleSpec() }));

  assert.equal(view.plan.status, "missing");
  assert.equal(view.now.action.label, "Refresh the plan");
  assert.deepEqual(view.now.action.intent, { type: "refresh_plan" });
  assert.equal(view.proofPath.planned, false);
});

test("a stale plan keeps the last gates visible but asks for a refresh", async () => {
  const view = buildProcessModel(
    base({
      spec: await exampleSpec(),
      plan: plan({ deploy: { decision: "run" }, safe_check: { decision: "run" } }),
      planStatus: {
        status: "stale",
        decidedAt: "2026-08-06T08:30:00.000Z",
        changedPath: "specs/SPEC.md",
      },
    }),
  );

  assert.equal(view.plan.status, "stale");
  assert.deepEqual(view.now.action.intent, { type: "refresh_plan" });
  assert.match(view.now.blocker, /specs\/SPEC\.md changed/);
  assert.equal(view.proofPath.planned, true);
  assert.equal(view.proofPath.stale, true);
  assert.ok(gates(view).some((gate) => gate.stage === "deploy" && gate.current));
});

test("business rules show only derivable proof", async () => {
  const view = buildProcessModel(
    base({
      spec: await exampleSpec(),
      governanceText: '{"policy":"BR-003 requires supervisor approval"}',
    }),
  );
  const byId = Object.fromEntries(view.process.rules.map((rule) => [rule.id, rule]));

  assert.deepEqual(
    byId["BR-003"].proofs.map((proof) => proof.kind),
    ["scenarios", "control", "human"],
  );
  assert.match(byId["BR-003"].proofs[0].label, /^3 test scenarios$/);
  assert.deepEqual(
    byId["BR-002"].proofs.map((proof) => proof.kind),
    ["scenarios"],
  );

  const bare = buildProcessModel(
    base({ spec: parseSpec("## 3. Business Rules\n\n### BR-009: Unproven rule\n") }),
  );
  assert.deepEqual(bare.process.rules[0].proofs, []);
  assert.equal(bare.process.rules[0].unproven, "Not yet proven");
});

test("go-live trusts readiness only with the full proof chain", () => {
  const passed = plan({});
  const readinessValue = {
    go_live_recommendation: "ready",
    would_fail_hard_gate: false,
  };
  const incomplete = buildProcessModel(
    base({
      plan: passed,
      planStatus: { status: "current", decidedAt: "2026-08-06T08:30:00.000Z" },
      readiness: {
        value: readinessValue,
        checkedAt: "2026-08-06T08:00:00.000Z",
        evidenceState: "readiness-incomplete",
      },
    }),
  );
  assert.notEqual(incomplete.goLive.readiness.label, "Ready to go live");
  assert.match(incomplete.goLive.readiness.detail, /proof chain is incomplete/);
  assert.deepEqual(incomplete.now.action.intent, {
    type: "invoke_skill",
    skillId: "threadlight-production-ready",
    phase: "go-live",
  });

  const proven = buildProcessModel(
    base({
      plan: passed,
      planStatus: { status: "current", decidedAt: "2026-08-06T08:30:00.000Z" },
      readiness: {
        value: readinessValue,
        checkedAt: "2026-08-06T08:00:00.000Z",
        evidenceState: "readiness-proof",
      },
    }),
  );
  assert.equal(proven.goLive.readiness.label, "Ready to go live");
  assert.equal(proven.now.headline, "Ready to go live");
  assert.deepEqual(proven.now.action.intent, { type: "prepare_handoff" });
});

test("cost shows an estimate and never sums an incomplete bill", () => {
  const complete = buildProcessModel(
    base({
      cost: {
        value: {
          totals: {
            complete: true,
            monthly_cost_current_usd: 1234.5,
            cost_per_transaction_usd: 0.042,
          },
        },
        checkedAt: "2026-08-06T07:00:00.000Z",
      },
    }),
  );
  assert.equal(complete.goLive.cost.label, "About $1,235 a month (estimate)");
  assert.equal(complete.goLive.cost.detail, "$0.042 per interaction");
  assert.equal(complete.goLive.cost.checkedAt, "2026-08-06T07:00:00.000Z");

  const partial = buildProcessModel(
    base({
      cost: {
        value: {
          totals: {
            complete: false,
            monthly_cost_current_usd: null,
            monthly_cost_known_usd: 300,
            cost_per_transaction_usd: null,
          },
        },
        checkedAt: "2026-08-06T07:00:00.000Z",
      },
    }),
  );
  assert.equal(partial.goLive.cost.label, "Partial estimate: $300 a month priced so far");
  assert.equal(partial.goLive.cost.detail, "Some lines are not priced yet.");

  const none = buildProcessModel(base());
  assert.equal(none.goLive.cost.label, "Not estimated yet");
});

test("every catalogued action is a valid chat intent", () => {
  for (const [stage, entry] of Object.entries(STAGE_CATALOG)) {
    assert.ok(entry.title && entry.action && entry.need, stage);
    assert.doesNotThrow(() => validateIntent({ type: "advance_plan", stage }), stage);
  }
});

test("a plan older than the planner's 24-hour freshness window is out of date", () => {
  const result = assessPlan(plan({}), {
    newestArtifactAt: "2026-08-06T08:00:00Z",
    now: "2026-08-07T08:30:00Z",
  });
  assert.equal(result.status, "stale");
  assert.equal(result.reason, "expired");

  const view = buildProcessModel(base({ plan: plan({}), planStatus: result }));
  assert.deepEqual(view.now.action.intent, { type: "refresh_plan" });
  assert.match(view.now.blocker, /more than a day old/);
  assert.equal(
    assessPlan(plan({}), { now: "2026-08-07T08:29:00Z" }).status,
    "current",
  );
});

test("a plan without one decision per stage, or with an inconsistent next action, is invalid", () => {
  assert.equal(assessPlan({ ...plan({}), decisions: [] }, {}).status, "invalid");
  const duplicated = plan({});
  duplicated.decisions.push({ ...duplicated.decisions[0] });
  assert.equal(assessPlan(duplicated, {}).status, "invalid");
  const hidden = plan({ govern: { decision: "hard_stop", reason: "x" } });
  assert.equal(assessPlan({ ...hidden, next_action: { type: "run", stages_to_run: [] } }, {}).status, "invalid");
  assert.equal(
    assessPlan({ ...hidden, next_action: { type: "hard_stop", stage: "deploy" } }, {}).status,
    "invalid",
  );
  assert.equal(assessPlan(hidden, {}).status, "current");
});

test("with nothing left to run, stale or failed evidence never reads as proven", () => {
  const view = buildProcessModel(
    base({
      plan: plan({}),
      planStatus: { status: "current", decidedAt: "2026-08-06T08:30:00.000Z" },
      phases: [
        {
          id: "verify",
          skills: [
            {
              definition: { id: "threadlight-safe-check", label: "Verify", role: "stage", stage: "safe_check" },
              status: "stale",
              evidence: [],
            },
          ],
        },
      ],
    }),
  );
  assert.notEqual(view.now.headline, "Proven live — go-live decision pending");
  assert.deepEqual(view.now.action.intent, { type: "refresh_plan" });
});
