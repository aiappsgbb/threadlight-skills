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
  return [
    ...(view.proofPath.workspaceCheck ? [view.proofPath.workspaceCheck] : []),
    ...view.proofPath.milestones.flatMap((milestone) => milestone.gates),
  ];
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
  assert.equal(byId["BR-003"].proofs[0].label, "3 scenarios · not run yet");
  assert.equal(byId["BR-003"].proofs[0].state, "todo");
  assert.deepEqual(byId["BR-003"].proofs[0].scenarios, ["S-002", "S-004", "S-007"]);
  assert.equal(byId["BR-003"].proofs[1].label, "Runtime control · not checked yet");
  assert.equal(byId["BR-003"].proofs[2].label, "Human review: Supervisor escalation review");
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

function skillPhase(id, skillId, stage, status) {
  return {
    id,
    skills: [{ definition: { id: skillId, label: skillId, role: "stage", stage }, status, evidence: [] }],
  };
}

test("a prerequisite check root does not erase evidenced design and offline preparation", async () => {
  const cascade = "Cascade re-run: an earlier stage will re-run; downstream stage must follow.";
  const view = buildProcessModel(
    base({
      spec: await exampleSpec(),
      plan: plan({
        preflight: { decision: "run", reason: "No preflight marker; bootstrap must run.", missing: [".threadlight/preflight-passed.json"] },
        design: { decision: "run", reason: cascade, seen: ["specs/SPEC.md"] },
        govern: { decision: "run", reason: "Binding inventory missing, invalid or changed." },
        governed_actions_gate: { decision: "run", reason: "Mandatory pre-deploy gate unverified." },
        deploy: { decision: "run", reason: "infra + azure.yaml exist but no AGENT_FQDN", seen: ["infra/main.bicep", "azure.yaml"] },
        governance_probe: { decision: "run" },
        safe_check: { decision: "run", missing: ["tests/postdeploy-manifest.json"] },
        cost_projection: { decision: "run", missing: ["specs/cost-manifest.json"] },
        invoke: { decision: "run" },
        agentops: { decision: "skip", reason: "AgentOps not opted in." },
        evals: { decision: "run" },
        redteam: { decision: "run" },
      }),
      planStatus: { status: "current", decidedAt: "2026-08-06T08:30:00.000Z" },
      phases: [skillPhase("controls", "threadlight-govern", "govern", "prepared")],
    }),
  );
  const byStage = Object.fromEntries(gates(view).map((gate) => [gate.stage, gate]));

  assert.equal(view.now.headline, "Designed and prepared offline — not yet live on Azure");
  assert.deepEqual(view.now.action.intent, { type: "advance_plan", stage: "preflight" });
  assert.equal(view.now.action.label, "Check the workspace prerequisites");
  assert.equal(view.now.blocker, STAGE_CATALOG.preflight.need);

  assert.equal(byStage.preflight.current, true);
  assert.equal(byStage.design.state, "done");
  assert.equal(byStage.design.note, "Will be re-checked");
  assert.equal(byStage.govern.state, "prepared");
  assert.equal(byStage.governed_actions_gate.state, "waiting");
  assert.equal(byStage.governed_actions_gate.waitingOn, "preflight");
  assert.equal(byStage.deploy.state, "prepared");
  assert.equal(byStage.deploy.note, "Not yet live");
  assert.equal(byStage.safe_check.state, "waiting");
  assert.ok(gates(view).every((gate) => gate.state !== "proven"));

  const design = view.proofPath.milestones.find((milestone) => milestone.id === "design");
  // The workspace check is its own row before the milestones; Design is
  // simply done and not the current step.
  assert.equal(view.proofPath.workspaceCheck.stage, "preflight");
  assert.equal(view.proofPath.workspaceCheck.current, true);
  assert.ok(design.gates.every((gate) => gate.stage !== "preflight"));
  assert.equal(design.current, false);
  assert.equal(design.state, "done");
  assert.equal(design.stateLabel, "Done");
  assert.match(view.now.why, /Threadlight runs this from chat/);
});

test("a cascade re-run of a live stage is out of date, never proven", () => {
  const cascade = "Cascade re-run: an earlier stage will re-run; downstream stage must follow.";
  const view = buildProcessModel(
    base({
      plan: plan({
        deploy: { decision: "run", reason: "AGENT_FQDN missing" },
        safe_check: { decision: "run", reason: cascade, seen: ["tests/postdeploy-manifest.json"] },
      }),
      planStatus: { status: "current", decidedAt: "2026-08-06T08:30:00.000Z" },
    }),
  );
  const safe = gates(view).find((gate) => gate.stage === "safe_check");
  assert.equal(safe.state, "stale");
  assert.equal(safe.note, "Will be re-checked");
});

test("a stage whose own artifacts are missing never reads as prepared or out of date", () => {
  const view = buildProcessModel(
    base({
      plan: plan({
        preflight: { decision: "run", reason: "No preflight marker; bootstrap must run.", missing: [".threadlight/preflight-passed.json"] },
        cost_projection: {
          decision: "run",
          reason: "specs/cost-manifest.json missing or stale — running threadlight-consumption-iq.",
          seen: ["specs/SPEC.md"],
          missing: ["specs/cost-manifest.json"],
        },
        safe_check: {
          decision: "run",
          reason: "tests/postdeploy-manifest.json missing; safe-check requires manifest evidence, not docs alone.",
          missing: ["tests/postdeploy-manifest.json"],
        },
      }),
      planStatus: { status: "current", decidedAt: "2026-08-06T08:30:00.000Z" },
      phases: [skillPhase("verify", "threadlight-safe-check", "safe_check", "stale")],
    }),
  );
  const byStage = Object.fromEntries(gates(view).map((gate) => [gate.stage, gate]));
  for (const stage of ["cost_projection", "safe_check"]) {
    assert.equal(byStage[stage].state, "waiting", stage);
    assert.equal(byStage[stage].waitingOn, "preflight", stage);
  }
});

function evalsPhase(status) {
  return skillPhase("evaluate", "threadlight-evals", "evals", status);
}

test("scenario proof follows evaluation evidence, never the plan alone", async () => {
  const spec = await exampleSpec();
  const label = (evals, phases = []) =>
    buildProcessModel(base({ spec, evals, phases })).process.rules.find(
      (rule) => rule.id === "BR-002",
    ).proofs[0];

  assert.deepEqual(
    { label: label(null).label, state: label(null).state },
    { label: "2 scenarios · not run yet", state: "todo" },
  );
  const offline = label(
    { status: "complete", verdict: "offline-only", passRate: "pass" },
  );
  assert.deepEqual([offline.label, offline.state], ["2 scenarios · passed offline", "done"]);
  const live = label({ status: "complete", verdict: "comprehensive", passRate: "pass" });
  assert.deepEqual([live.label, live.state], ["2 scenarios · passed live", "proven"]);
  const failing = label({ status: "failed", verdict: "partial", passRate: "must-fix" });
  assert.deepEqual([failing.label, failing.state], ["2 scenarios · evaluation failing", "failed"]);
  const stale = label({ status: "stale", verdict: "comprehensive", passRate: "pass" });
  assert.deepEqual([stale.label, stale.state], ["2 scenarios · evaluation out of date", "stale"]);
  // A complete manifest without a passing pass-rate check is not "passed".
  const unclear = label({ status: "complete", verdict: "comprehensive", passRate: "not-verified" });
  assert.equal(unclear.state, "attention");
  assert.doesNotMatch(unclear.label, /passed/);
});

test("runtime-control chips follow the governance gate's evidence", async () => {
  const spec = await exampleSpec();
  const chip = (status) =>
    buildProcessModel(
      base({
        spec,
        governanceText: "BR-003",
        phases: status ? [skillPhase("controls", "threadlight-govern", "govern", status)] : [],
      }),
    ).process.rules.find((rule) => rule.id === "BR-003").proofs[1];
  assert.equal(chip(null).label, "Runtime control · not checked yet");
  assert.equal(chip("prepared").label, "Runtime control · prepared offline");
  assert.equal(chip("prepared").state, "prepared");
  // Even a "complete" governance skill alone never reads as proven live.
  assert.equal(chip("complete").state, "prepared");
  assert.doesNotMatch(chip("complete").label, /proven live/);
  assert.equal(chip("failed").state, "failed");
});

test("runtime controls are proven live only by a fresh planner governance probe", async () => {
  const spec = await exampleSpec();
  const chip = (probe, planStatus = { status: "current", decidedAt: DECIDED_AT }) =>
    buildProcessModel(
      base({
        spec,
        governanceText: "BR-003",
        plan: plan({ governance_probe: probe }),
        planStatus,
        phases: [skillPhase("controls", "threadlight-govern", "govern", "prepared")],
      }),
    ).process.rules.find((rule) => rule.id === "BR-003").proofs[1];
  const fresh = chip({ decision: "skip", reason: "Exact live binding proof is current." });
  assert.deepEqual([fresh.state, fresh.label], ["proven", "Runtime control · proven live"]);
  const elapsed = chip({
    decision: "skip",
    reason: "Presenter deployment: Recorded evidence validity elapsed; refresh observation, not the underlying effect.",
  });
  assert.equal(elapsed.state, "stale");
  const stalePlan = chip(
    { decision: "skip", reason: "Exact live binding proof is current." },
    { status: "stale", decidedAt: DECIDED_AT, changedPath: "specs/SPEC.md" },
  );
  assert.notEqual(stalePlan.state, "proven");
});

test("a skip whose recorded evidence validity elapsed is out of date, never proven live", () => {
  const elapsed =
    "Presenter deployment: Recorded evidence validity elapsed; refresh observation, not the underlying effect.";
  const stages = [...GOVERNED, "presenter_ready"];
  const view = buildProcessModel(
    base({
      plan: plan(
        {
          deploy: { decision: "skip", reason: elapsed },
          invoke: { decision: "skip", reason: elapsed.replace("deployment", "journey") },
          presenter_ready: { decision: "run", reason: "Process owner must supply presenter check: human; no automatic business replay." },
        },
        { stages },
      ),
      planStatus: { status: "current", decidedAt: DECIDED_AT },
    }),
  );
  const byStage = Object.fromEntries(gates(view).map((gate) => [gate.stage, gate]));
  assert.equal(byStage.deploy.state, "stale");
  assert.equal(byStage.invoke.state, "stale");
  assert.ok(gates(view).filter((gate) => gate.state === "proven").every((gate) => !["deploy", "invoke"].includes(gate.stage)));
  assert.equal(view.now.headline, "Some evidence is out of date");
  assert.doesNotMatch(view.now.headline, /Proven live/);
  assert.match(view.now.blocker, /Publish the agent/);
  // The planner's real next step is kept as the one action.
  assert.deepEqual(view.now.action.intent, { type: "advance_plan", stage: "presenter_ready" });
});


test("every Now state says who takes the next step and why", async () => {
  const spec = await exampleSpec();
  const missing = buildProcessModel(base({ spec }));
  assert.match(missing.now.why, /reads files only/);
  assert.equal(buildProcessModel(base({ manifest: null })).now.why, null);
  for (const stage of Object.keys(STAGE_CATALOG)) {
    assert.ok(STAGE_CATALOG[stage].why, stage);
    assert.doesNotMatch(STAGE_CATALOG[stage].why, /\b\d+\s*(min|minutes|hours|h)\b/, stage);
  }
});

test("an out-of-date plan keeps a neutral headline; the blocker carries the warning", async () => {
  const view = buildProcessModel(
    base({
      spec: await exampleSpec(),
      plan: plan({ deploy: { decision: "run" } }),
      planStatus: { status: "stale", decidedAt: "2026-08-06T08:30:00.000Z", changedPath: "specs/SPEC.md" },
    }),
  );
  assert.equal(view.now.tone, "progress");
  assert.equal(view.now.blockerTone, "attention");
});

test("go-live figures name the proof step that produces them", () => {
  const view = buildProcessModel(base());
  assert.equal(view.goLive.readiness.source, "Assessed in “Decide on go-live”");
  assert.equal(view.goLive.cost.source, "Estimated in “Verify it live”");
});

test("KPIs keep the SPEC's own wording and say when they are measured", async () => {
  const spec = await exampleSpec();
  const kpi = spec.kpis.find((item) => item.name === "restock_a_pct");
  assert.equal(kpi.target, "60–80% (apparel)");
  const labelled = parseSpec(
    "## 9. Success Criteria\n\n### Business KPIs\n\n| BR | KPI Name | Label | Target |\n|---|---|---|---|\n| BR-001 | `restock_a_pct` | Restocked as new | 60–80% |\n",
  );
  assert.equal(labelled.kpis[0].label, "Restocked as new");
  const view = buildProcessModel(base({ spec }));
  assert.ok(view.process.kpis.every((item) => item.status === "Measured after go-live"));
});

test("a missing plan says 'Not computed' once, not per milestone", async () => {
  const view = buildProcessModel(base({ spec: await exampleSpec() }));
  assert.equal(view.proofPath.planned, false);
  assert.ok(view.proofPath.milestones.every((milestone) => milestone.stateLabel === null));
});

test("an unverified plan never headlines live results, whatever the next step", () => {
  for (const next of ["invoke", "evals", "safe_check"]) {
    const decisions = {};
    for (const stage of ["preflight", "design", "govern", "governed_actions_gate", "deploy", "governance_probe", "safe_check", "cost_projection", "invoke", "agentops", "evals", "redteam"]) {
      decisions[stage] = { decision: "skip", reason: "Fresh evidence." };
      if (stage === next) break;
    }
    decisions[next] = { decision: "run", reason: "Not yet run." };
    const view = buildProcessModel(
      base({
        plan: plan(decisions),
        planStatus: { status: "unverified", reason: "unreadable", decidedAt: "2026-08-06T08:30:00.000Z" },
      }),
    );
    assert.doesNotMatch(view.now.headline, /\blive\b|verified|Ready to go live/i, next);
    assert.equal(view.now.headline, "Progress recorded; live results not re-confirmed", next);
  }
});
