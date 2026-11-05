// Pure composition of the Canvas view for the process owner. Inputs are the
// already-read workspace artifacts (SPEC, threadlight-auto plan, projector
// output, cost and readiness evidence); nothing here reads files, spawns
// processes or trusts a status the projector has not validated.
import { LIFECYCLE_PHASES, SKILL_REGISTRY } from "./lifecycle-registry.mjs";

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) {
      deepFreeze(nested);
    }
    Object.freeze(value);
  }
  return value;
}

// Every stage threadlight-auto's planner can emit (orchestrator.py STAGES,
// GOVERNANCE_STAGES and the presenter stages), in process language.
// `done` is how a passed stage is described: offline work is "done" or
// "prepared" offline; only live stages can be "proven" live.
export const STAGE_CATALOG = deepFreeze({
  preflight: {
    milestone: "design",
    live: false,
    done: "done",
    title: "Workspace check",
    action: "Check the workspace prerequisites",
    need: "This machine and workspace haven't passed the prerequisite check yet.",
    headline: "Getting started — prerequisites not checked yet",
    why: "Threadlight runs this from chat; it is needed before anything touches Azure.",
  },
  design: {
    milestone: "design",
    live: false,
    done: "done",
    title: "Process design",
    action: "Finish the process design",
    need: "The process design isn't complete yet.",
    headline: "Process design in progress — nothing is live yet",
    why: "Threadlight drafts it with you in chat; you review the process before anything is built.",
  },
  govern: {
    milestone: "controls",
    live: false,
    done: "prepared",
    title: "Control selection",
    action: "Select the runtime controls",
    need: "The runtime controls for this agent haven't been selected and checked offline.",
    headline: "Designed — runtime controls not yet selected",
    why: "Threadlight prepares the controls from chat; they are needed before the agent can change business records.",
  },
  governed_actions_gate: {
    milestone: "controls",
    live: false,
    done: "prepared",
    title: "Validate governed actions",
    action: "Validate the governed actions",
    need: "The actions that change business records haven't passed their pre-publish check.",
    headline: "Designed — governed actions not yet validated",
    why: "Threadlight validates this from chat; publishing stays blocked until it passes.",
  },
  deploy: {
    milestone: "publish",
    live: true,
    done: "proven",
    title: "Publish the agent",
    action: "Publish the agent to Microsoft Foundry",
    need: "The agent hasn't been published to Azure yet.",
    headline: "Designed and prepared offline — not yet live on Azure",
    why: "Threadlight runs this from chat once you confirm; it creates Azure resources in your subscription.",
  },
  governance_probe: {
    milestone: "publish",
    live: true,
    done: "proven",
    title: "Prove the controls live",
    action: "Prove the controls on the live agent",
    need: "The selected controls haven't been proven on the live agent.",
    headline: "Published on Foundry — controls not yet proven live",
    why: "Threadlight runs this from chat against the staging agent; it proves the controls on the live agent.",
  },
  presenter_package: {
    milestone: "publish",
    live: false,
    done: "done",
    title: "Package the presenter demo",
    action: "Verify the presenter package",
    need: "The presenter package hasn't been verified.",
    headline: "Designed — presenter package not yet verified",
    why: "Threadlight checks this from chat; the demo package must match what will be published.",
  },
  safe_check: {
    milestone: "verify",
    live: true,
    done: "proven",
    title: "Post-deploy safety check",
    action: "Run the post-deploy safety check",
    need: "The live deployment hasn't passed the post-deploy safety check.",
    headline: "Published on Foundry — not yet verified live",
    why: "Threadlight runs this from chat against the live agent; later checks rely on it.",
  },
  cost_projection: {
    milestone: "verify",
    live: false,
    done: "done",
    title: "Estimate the running cost",
    action: "Estimate the running cost",
    need: "There is no current cost estimate for this pilot.",
    headline: "Live and verified — running cost not yet estimated",
    why: "Threadlight runs this from chat; it reads the design and calls no paid service.",
  },
  invoke: {
    milestone: "verify",
    live: true,
    done: "proven",
    title: "Run the demo scenarios",
    action: "Run the demo scenarios live",
    need: "The demo scenarios haven't been run against the live agent.",
    headline: "Live and verified — demo scenarios not yet run",
    why: "Threadlight runs the scenarios from chat against the live agent once you confirm.",
  },
  agentops: {
    milestone: "evaluate",
    live: true,
    done: "proven",
    title: "Check agent operations",
    action: "Check agent operations",
    need: "Agent operations evidence is missing or out of date.",
    headline: "Live — agent operations not yet checked",
    why: "Threadlight runs this from chat for agents you opted in; it reads existing evidence.",
  },
  evals: {
    milestone: "evaluate",
    live: true,
    done: "proven",
    title: "Measure answer quality",
    action: "Measure answer quality",
    need: "Answer quality hasn't been measured against the live agent.",
    headline: "Live — answer quality not yet measured",
    why: "Threadlight runs this from chat against the live agent once you confirm.",
  },
  redteam: {
    milestone: "evaluate",
    live: true,
    done: "proven",
    title: "Probe adversarial safety",
    action: "Probe adversarial safety",
    need: "The live agent hasn't been probed for adversarial safety.",
    headline: "Live — adversarial safety not yet probed",
    why: "Threadlight runs this from chat against the live agent once you confirm.",
  },
  presenter_ready: {
    milestone: "go-live",
    live: false,
    done: "done",
    title: "Presenter acceptance",
    action: "Record the presenter acceptance",
    need: "The process owner hasn't recorded the presenter acceptance yet.",
    headline: "Proven live — presenter acceptance pending",
    why: "You record this yourself; Threadlight cannot replay the business acceptance for you.",
  },
});

export const STATE_LABELS = deepFreeze({
  done: "Done",
  prepared: "Prepared offline",
  proven: "Proven live",
  skipped: "Not needed",
  todo: "Not started",
  "in-progress": "In progress",
  waiting: "Waiting",
  stale: "Out of date",
  failed: "Blocked",
  unknown: "Not computed",
  ready: "Ready",
});

const PASSED_STATES = new Set(["done", "prepared", "proven", "skipped"]);
const DECISIONS = new Set(["run", "skip", "hard_stop"]);
const NOT_NEEDED = /not opted in|not applicable|not selected|not required|not enabled|disabled/i;
const CASCADE_REASON = /cascade re-run/i;
// Workspace bootstrap, not process progress: never defines the process state.
const BOOTSTRAP_STAGES = new Set(["preflight"]);
const EVIDENCED_STATES = new Set(["done", "prepared", "proven"]);
// Includes the presenter's "Recorded evidence validity elapsed; refresh
// observation" skips (skills/_shared/presenter.py), which the planner emits as
// `skip` so it does not replay effects: elapsed evidence is not proof.
const STALE_REASON =
  /\bstale\b|out of date|expired|older than|no longer match|validity elapsed|\belapsed\b|refresh observation/i;
const STAGE_ID = /^[a-z][a-z0-9_]{0,63}$/;
const STAGE_SKILLS = new Map(
  SKILL_REGISTRY.filter((definition) => definition.stage).map((definition) => [
    definition.stage,
    definition.id,
  ]),
);
const SKILL_IDS = new Set(SKILL_REGISTRY.map((definition) => definition.id));

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseTime(value) {
  if (typeof value !== "string" || value.trim() === "") {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function humanize(value) {
  const text = String(value ?? "").replace(/[-_]+/g, " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : "";
}

function stageEntry(stage) {
  return (
    STAGE_CATALOG[stage] ?? {
      milestone: "verify",
      live: false,
      done: "done",
      title: humanize(stage),
      action: `Continue: ${humanize(stage)}`,
      need: `${humanize(stage)} hasn't passed yet.`,
      headline: `${humanize(stage)} pending`,
      why: "Threadlight continues this from chat once you confirm.",
    }
  );
}

// ---------------------------------------------------------------------------
// SPEC parsing (speckit template headings; tolerant of missing sections)
// ---------------------------------------------------------------------------

function cleanInline(text) {
  return String(text ?? "")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\*\*([^*]*)\*\*/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

function sectionLines(lines, heading) {
  const start = lines.findIndex((line) => /^##\s/.test(line) && heading.test(line));
  if (start < 0) {
    return [];
  }
  const end = lines.findIndex((line, index) => index > start && /^##\s/.test(line));
  return lines.slice(start + 1, end < 0 ? undefined : end);
}

function subsectionLines(lines, heading) {
  const start = lines.findIndex((line) => /^###\s/.test(line) && heading.test(line));
  if (start < 0) {
    return [];
  }
  const end = lines.findIndex(
    (line, index) => index > start && /^#{2,3}\s/.test(line),
  );
  return lines.slice(start + 1, end < 0 ? undefined : end);
}

function fieldValue(lines, name) {
  const pattern = new RegExp(`^\\s*(?:-\\s*)?\\*\\*${name}\\*\\*:\\s*(.*)$`, "i");
  const index = lines.findIndex((line) => pattern.test(line));
  if (index < 0) {
    return "";
  }
  const parts = [pattern.exec(lines[index])[1]];
  for (const line of lines.slice(index + 1)) {
    if (!line.trim() || /^\s*(?:-\s*)?\*\*[^*]+\*\*:/.test(line) || /^\s*[-#|>]/.test(line)) {
      break;
    }
    parts.push(line);
  }
  return cleanInline(parts.join(" "));
}

function ruleIds(text) {
  return [...new Set(String(text ?? "").match(/BR-\d{3,}/g) ?? [])];
}

function parseTable(lines) {
  const rows = lines.filter((line) => /^\s*\|/.test(line));
  if (rows.length < 2) {
    return [];
  }
  const split = (row) =>
    row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  const header = split(rows[0]).map((cell) => cell.toLowerCase());
  return rows
    .slice(1)
    .filter((row) => !/^\s*\|[\s:|-]+\|\s*$/.test(row))
    .map((row) => {
      const cells = split(row);
      return Object.fromEntries(header.map((name, index) => [name, cells[index] ?? ""]));
    });
}

function headingBlocks(lines, level) {
  const marker = "#".repeat(level);
  const blocks = [];
  let current = null;
  for (const line of lines) {
    const match = new RegExp(`^${marker}\\s+(.*)$`).exec(line);
    if (match) {
      current = { heading: match[1].trim(), lines: [] };
      blocks.push(current);
    } else if (/^#{1,3}\s/.test(line) && level === 4) {
      current = null;
    } else if (current) {
      current.lines.push(line);
    }
  }
  return blocks;
}

export function parseSpec(text) {
  if (typeof text !== "string") {
    return null;
  }
  const lines = text.split(/\r?\n/);
  const titleLine = lines.find((line) => /^#\s/.test(line)) ?? "";
  const title = cleanInline(titleLine.replace(/^#\s+/, "").replace(/^SpecKit:\s*/i, ""));
  const overview = sectionLines(lines, /process overview/i);
  const name = fieldValue(overview, "Name") || fieldValue(lines, "Name") || title;
  const description = fieldValue(overview, "Description");

  const flow = sectionLines(lines, /process flow/i);
  const steps = headingBlocks(flow, 4)
    .map((block) => {
      const match = /^Step\s+\d+\s*:\s*(.*)$/i.exec(block.heading);
      return match
        ? { title: cleanInline(match[1]), actor: fieldValue(block.lines, "Actor") || null }
        : null;
    })
    .filter((step) => step && step.title && !/^\[.*\]$/.test(step.title));

  const rules = headingBlocks(sectionLines(lines, /business rules/i), 3)
    .map((block) => /^(BR-\d{3,})\s*:\s*(.*)$/.exec(block.heading))
    .filter(Boolean)
    .map((match) => ({ id: match[1], title: cleanInline(match[2]) }));

  const decisions = headingBlocks(sectionLines(lines, /human interaction points/i), 3)
    .filter((block) => !/^\[.*\]$/.test(block.heading))
    .map((block) => ({
      title: cleanInline(block.heading),
      actor: fieldValue(block.lines, "Actor") || null,
      trigger: fieldValue(block.lines, "Trigger") || null,
      rules: ruleIds(fieldValue(block.lines, "Linked business rules")),
    }));

  const criteria = sectionLines(lines, /success criteria/i);
  const scenarios = parseTable(subsectionLines(criteria, /evaluation scenarios/i))
    .filter((row) => /^S-\d+/.test(row.id ?? ""))
    .map((row) => ({
      id: row.id,
      title: cleanInline(row.scenario),
      rules: ruleIds(row["business rules"]),
    }));
  const kpis = parseTable(subsectionLines(criteria, /business kpis/i))
    .filter((row) => /^BR-\d+/.test(row.br ?? "") && cleanInline(row["kpi name"]))
    .map((row) => {
      const label = cleanInline(row.label ?? row.description ?? "");
      return {
        rule: row.br,
        name: cleanInline(row["kpi name"]),
        target: cleanInline(row.target),
        ...(label ? { label } : {}),
      };
    });

  return { title, name, description, steps, rules, decisions, scenarios, kpis };
}

// ---------------------------------------------------------------------------
// threadlight-auto plan (.threadlight/auto-next.json)
// ---------------------------------------------------------------------------

function isValidPlan(plan) {
  if (!isPlainObject(plan) || !Array.isArray(plan.stages) || plan.stages.length === 0) {
    return false;
  }
  if (!plan.stages.every((stage) => typeof stage === "string" && STAGE_ID.test(stage))) {
    return false;
  }
  const stages = new Set(plan.stages);
  if (stages.size !== plan.stages.length) {
    return false;
  }
  // The planner writes exactly one decision per stage, in stage order.
  if (
    !Array.isArray(plan.decisions) ||
    plan.decisions.length !== plan.stages.length ||
    !plan.decisions.every(
      (decision, index) =>
        isPlainObject(decision) &&
        decision.stage === plan.stages[index] &&
        DECISIONS.has(decision.decision),
    )
  ) {
    return false;
  }
  const next = plan.next_action;
  if (!isPlainObject(next) || !["run", "hard_stop"].includes(next.type)) {
    return false;
  }
  const firstHardStop = plan.decisions.find((decision) => decision.decision === "hard_stop");
  if (next.type === "hard_stop" ? next.stage !== firstHardStop?.stage : firstHardStop) {
    return false;
  }
  return parseTime(plan.decided_at) !== null;
}

// threadlight-auto skips live stages only while their evidence is younger than
// FRESHNESS_SECONDS (24 h); an older plan may hide expired evidence.
const PLAN_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export function assessPlan(
  plan,
  { newestArtifactAt = null, newestArtifactPath = null, now = null } = {},
) {
  if (plan === null || plan === undefined) {
    return { status: "missing", decidedAt: null };
  }
  if (!isValidPlan(plan)) {
    return { status: "invalid", decidedAt: null };
  }
  const decidedAt = parseTime(plan.decided_at);
  const newest = parseTime(newestArtifactAt);
  // Two seconds of tolerance for filesystem timestamp granularity.
  if (newest && newest.getTime() > decidedAt.getTime() + 2000) {
    return {
      status: "stale",
      reason: "changed",
      decidedAt: decidedAt.toISOString(),
      changedPath: newestArtifactPath,
    };
  }
  const current = parseTime(now);
  if (current && current.getTime() - decidedAt.getTime() >= PLAN_MAX_AGE_MS) {
    return { status: "stale", reason: "expired", decidedAt: decidedAt.toISOString(), changedPath: null };
  }
  return { status: "current", decidedAt: decidedAt.toISOString() };
}

function findSkill(phases, skillId) {
  for (const phase of phases ?? []) {
    for (const skill of phase?.skills ?? []) {
      if (skill?.definition?.id === skillId) {
        return skill;
      }
    }
  }
  return null;
}

function rootStage(plan) {
  if (plan.next_action.type === "hard_stop") {
    return plan.next_action.stage;
  }
  const decisions = new Map(plan.decisions.map((decision) => [decision.stage, decision]));
  return plan.stages.find((stage) => decisions.get(stage)?.decision === "run") ?? null;
}

function gateFor(stage, decision, { root, rootIndex, index, phases }) {
  const entry = stageEntry(stage);
  const technical = {
    decision: decision?.decision ?? null,
    reason: typeof decision?.reason === "string" ? decision.reason : null,
    artifactsSeen: Array.isArray(decision?.artifacts_seen) ? decision.artifacts_seen.filter((item) => typeof item === "string") : [],
    artifactsMissing: Array.isArray(decision?.artifacts_missing) ? decision.artifacts_missing.filter((item) => typeof item === "string") : [],
    skillId: STAGE_SKILLS.get(stage) ?? null,
  };
  const gate = {
    stage,
    title: entry.title,
    live: entry.live,
    current: stage === root,
    state: "todo",
    detail: null,
    note: null,
    waitingOn: null,
    technical,
  };
  const reason = technical.reason ?? "";

  if (decision?.decision === "hard_stop") {
    gate.state = "failed";
    gate.detail = reason || entry.need;
  } else if (decision?.decision === "skip") {
    gate.state = NOT_NEEDED.test(reason)
      ? "skipped"
      : STALE_REASON.test(reason)
        ? "stale"
        : entry.done;
    const projected = technical.skillId ? findSkill(phases, technical.skillId) : null;
    if (projected?.status === "failed" || projected?.status === "stale") {
      gate.state = projected.status;
    }
  } else if (stage === root) {
    if (STALE_REASON.test(reason)) {
      gate.state = "stale";
    } else if (technical.artifactsSeen.length > 0) {
      gate.state = entry.live ? "prepared" : "in-progress";
    }
    gate.detail = entry.need;
  } else {
    // A planned re-run is not lost progress. Keep what the stage's own
    // evidence shows (offline at most; never "proven" from a re-run), and
    // only wait on the root when the stage has no evidence of its own.
    const projected = technical.skillId ? findSkill(phases, technical.skillId) : null;
    // The planner's own missing-artifact list wins: a stage still missing its
    // artifacts has no evidence to be prepared or out of date.
    const ownEvidence =
      technical.artifactsMissing.length === 0 &&
      (technical.artifactsSeen.length > 0 || ["prepared", "complete"].includes(projected?.status));
    const cascade = CASCADE_REASON.test(reason) && technical.artifactsMissing.length === 0;
    if (ownEvidence && cascade) {
      gate.state = entry.live ? "stale" : entry.done;
      gate.note = "Will be re-checked";
    } else if (ownEvidence && STALE_REASON.test(reason)) {
      gate.state = "stale";
    } else if (ownEvidence) {
      gate.state = "prepared";
      gate.note = entry.live ? "Not yet live" : "Not yet passed";
    } else if (root !== null && index > rootIndex) {
      gate.state = "waiting";
      gate.waitingOn = root;
      gate.detail = `Waiting on “${stageEntry(root).title}”`;
    }
  }
  gate.stateLabel = gate.state === "waiting" ? STATE_LABELS.waiting : STATE_LABELS[gate.state];
  return gate;
}

function milestoneState(gates) {
  const current = gates.find((gate) => gate.current);
  if (current && BOOTSTRAP_STAGES.has(current.stage)) {
    // A workspace check is the next step, but the milestone reads from its
    // process gates so existing design work is not shown as "in progress".
    const process = gates.filter((gate) => !BOOTSTRAP_STAGES.has(gate.stage));
    if (process.length > 0) {
      return milestoneState(process);
    }
  }
  if (current) {
    return current.state === "failed" ? "failed" : "current";
  }
  if (gates.length > 0 && gates.every((gate) => PASSED_STATES.has(gate.state))) {
    if (gates.every((gate) => gate.state === "skipped")) return "skipped";
    if (gates.some((gate) => gate.state === "proven")) return "proven";
    if (gates.some((gate) => gate.state === "prepared")) return "prepared";
    return "done";
  }
  for (const state of ["failed", "stale"]) {
    if (gates.some((gate) => gate.state === state)) return state;
  }
  if (gates.some((gate) => EVIDENCED_STATES.has(gate.state))) {
    return "partial";
  }
  if (gates.some((gate) => gate.state === "waiting")) return "waiting";
  return "todo";
}

const MILESTONE_LABELS = Object.freeze({
  current: "In progress",
  failed: "Blocked",
  proven: "Proven live",
  prepared: "Prepared offline",
  done: "Done",
  skipped: "Not needed",
  stale: "Out of date",
  partial: "Partly done",
  waiting: "Waiting",
  todo: "Not started",
  unknown: "Not computed",
  ready: "Ready to go live",
});

function phaseMeta(id) {
  return LIFECYCLE_PHASES.find((phase) => phase.id === id) ?? { id, label: humanize(id), title: humanize(id) };
}

function buildProofPath({ plan, planStatus, phases, readinessState }) {
  if (!["current", "stale"].includes(planStatus.status)) {
    return {
      planned: false,
      stale: false,
      root: null,
      workspaceCheck: null,
      milestones: LIFECYCLE_PHASES.map((phase) => ({
        id: phase.id,
        label: phase.label,
        title: phase.title,
        state: "unknown",
        // "Not computed" is said once for the whole path, not per milestone.
        stateLabel: null,
        current: false,
        gates: [],
      })),
    };
  }

  const decisions = new Map(plan.decisions.map((decision) => [decision.stage, decision]));
  const root = rootStage(plan);
  const rootIndex = root === null ? -1 : plan.stages.indexOf(root);
  const groups = [];
  let workspaceCheck = null;
  plan.stages.forEach((stage, index) => {
    const gate = gateFor(stage, decisions.get(stage), { root, rootIndex, index, phases });
    if (BOOTSTRAP_STAGES.has(stage)) {
      // A machine/workspace check, not process progress: its own row.
      workspaceCheck = gate;
      return;
    }
    const milestone = stageEntry(stage).milestone;
    const last = groups.at(-1);
    if (last && last.id === milestone) {
      last.gates.push(gate);
    } else {
      groups.push({ id: milestone, gates: [gate] });
    }
  });
  if (groups.at(-1)?.id !== "go-live") {
    groups.push({ id: "go-live", gates: [] });
  }

  const milestones = groups.map((group) => {
    const meta = phaseMeta(group.id);
    let state = milestoneState(group.gates);
    let detail = null;
    if (group.id === "go-live" && group.gates.every((gate) => PASSED_STATES.has(gate.state))) {
      if (root !== null) {
        state = "waiting";
        detail = `Waiting on “${stageEntry(root).title}”`;
      } else if (readinessState === "ready") {
        state = "ready";
      } else {
        state = "current";
        detail = "Assess production readiness before deciding on go-live.";
      }
    }
    return {
      id: group.id,
      label: meta.label,
      title: meta.title,
      state,
      stateLabel: MILESTONE_LABELS[state],
      current: state === "current" || group.gates.some((gate) => gate.current),
      detail,
      gates: group.gates,
    };
  });

  return { planned: true, stale: planStatus.status === "stale", root, workspaceCheck, milestones };
}

// ---------------------------------------------------------------------------
// Process (SPEC) with derivable proof per business rule
// ---------------------------------------------------------------------------

// Scenario proof comes only from validated evaluation evidence (the
// projector's evals status plus the manifest's verdict and pass-rate check);
// a scenario listed in the SPEC is a plan, not a result.
function scenarioResult(evals) {
  const status = evals?.status ?? null;
  if (!status || ["ready", "blocked", "not-started", "not-applicable"].includes(status)) {
    return { state: "todo", text: "not run yet" };
  }
  if (status === "failed") return { state: "failed", text: "evaluation failing" };
  if (status === "stale") return { state: "stale", text: "evaluation out of date" };
  if (status === "complete" && evals.passRate === "pass") {
    return evals.verdict === "comprehensive"
      ? { state: "proven", text: "passed live" }
      : { state: "done", text: "passed offline" };
  }
  return { state: "attention", text: "evaluation incomplete" };
}

// Runtime controls are "proven live" only through the planner: a fresh,
// non-elapsed governance_probe skip in a current plan. A governance manifest
// on its own is offline evidence and never reads as live proof here.
function controlResult(phases, proofPath) {
  const status = findSkill(phases, "threadlight-govern")?.status ?? null;
  if (status === "failed") return { state: "failed", text: "failing" };
  if (status === "stale") return { state: "stale", text: "out of date" };
  const probe =
    proofPath.planned && !proofPath.stale
      ? allGates(proofPath).find((gate) => gate.stage === "governance_probe")
      : null;
  if (probe?.state === "proven") return { state: "proven", text: "proven live" };
  if (probe?.state === "stale") return { state: "stale", text: "out of date" };
  if (status === "prepared" || status === "complete") {
    return { state: "prepared", text: "prepared offline" };
  }
  return { state: "todo", text: "not checked yet" };
}

const GOVERNANCE_GATES = ["govern", "governed_actions_gate", "governance_probe"];

/**
 * True when a current plan has no selected runtime governance: the planner
 * used its standard stage list (no governed-actions gate, no live probe). A
 * legacy "govern" stage in that list must itself have passed freshly.
 */
export function planOmitsGovernance(plan, planStatus) {
  if (
    planStatus?.status !== "current" ||
    !isPlainObject(plan) ||
    !Array.isArray(plan.stages) ||
    plan.stages.some((stage) => GOVERNANCE_GATES.slice(1).includes(stage))
  ) {
    return false;
  }
  const govern = (plan.decisions ?? []).find((decision) => decision.stage === "govern");
  return (
    !govern ||
    (govern.decision === "skip" && !STALE_REASON.test(govern.reason ?? ""))
  );
}

/** True only when a current plan shows every governance gate passed freshly. */
export function planProvesGovernance(plan, planStatus) {
  if (planStatus?.status !== "current" || !isPlainObject(plan) || !Array.isArray(plan.decisions)) {
    return false;
  }
  if (!plan.stages.includes("governance_probe")) {
    return false;
  }
  return plan.decisions
    .filter((decision) =>
      ["govern", "governed_actions_gate", "governance_probe"].includes(decision.stage),
    )
    .every(
      (decision) =>
        decision.decision === "skip" &&
        !STALE_REASON.test(decision.reason ?? "") &&
        !NOT_NEEDED.test(decision.reason ?? ""),
    );
}

function buildProcess(spec, governanceText, evals, phases, proofPath) {
  if (!spec) {
    return null;
  }
  const controls = typeof governanceText === "string" ? governanceText : "";
  const scenarioState = scenarioResult(evals);
  const controlState = controlResult(phases, proofPath);
  const rules = spec.rules.map((rule) => {
    const proofs = [];
    const scenarios = spec.scenarios.filter((scenario) => scenario.rules.includes(rule.id));
    if (scenarios.length > 0) {
      proofs.push({
        kind: "scenarios",
        state: scenarioState.state,
        label: `${scenarios.length} scenario${scenarios.length === 1 ? "" : "s"} · ${scenarioState.text}`,
        scenarios: scenarios.map((scenario) => scenario.id),
      });
    }
    if (new RegExp(`\\b${rule.id}\\b`).test(controls)) {
      proofs.push({
        kind: "control",
        state: controlState.state,
        label: `Runtime control · ${controlState.text}`,
      });
    }
    const decision = spec.decisions.find((item) => item.rules.includes(rule.id));
    if (decision) {
      proofs.push({ kind: "human", state: "human", label: `Human review: ${decision.title}` });
    }
    return { ...rule, proofs, unproven: proofs.length === 0 ? "Not yet proven" : null };
  });
  return {
    steps: spec.steps,
    rules,
    decisions: spec.decisions,
    kpis: spec.kpis.map((kpi) => ({ ...kpi, status: "Measured after go-live" })),
  };
}

// ---------------------------------------------------------------------------
// Go-live and value
// ---------------------------------------------------------------------------

function formatUsd(value) {
  if (value >= 1) {
    return `$${Math.round(value).toLocaleString("en-US")}`;
  }
  return `$${Number(value.toPrecision(2))}`;
}

function buildReadiness(readiness) {
  const value = readiness?.value;
  if (!isPlainObject(value)) {
    return { state: "none", label: "Not assessed yet", detail: null, checkedAt: null };
  }
  const checkedAt = readiness.checkedAt ?? null;
  if (readiness.evidenceState === "readiness-proof") {
    return {
      state: "ready",
      label: "Ready to go live",
      detail: "The readiness report and its proof chain agree.",
      checkedAt,
    };
  }
  if (value.go_live_recommendation === "ready") {
    return {
      state: "attention",
      label: "Not proven ready",
      detail: "The readiness report says ready, but the proof chain is incomplete.",
      checkedAt,
    };
  }
  const recommendation =
    typeof value.go_live_recommendation === "string"
      ? humanize(value.go_live_recommendation).toLowerCase()
      : "not stated";
  return {
    state: "failed",
    label: "Not ready",
    detail: `Recommendation: ${recommendation}${value.would_fail_hard_gate === true ? "; a hard gate would fail." : "."}`,
    checkedAt,
  };
}

function buildCost(cost) {
  const totals = cost?.value?.totals;
  if (!isPlainObject(totals)) {
    return { label: "Not estimated yet", detail: null, checkedAt: null };
  }
  const checkedAt = cost.checkedAt ?? null;
  const monthly = totals.monthly_cost_current_usd;
  if (totals.complete === true && typeof monthly === "number" && Number.isFinite(monthly)) {
    const perTransaction = totals.cost_per_transaction_usd;
    return {
      label: `About ${formatUsd(monthly)} a month (estimate)`,
      detail:
        typeof perTransaction === "number" && Number.isFinite(perTransaction)
          ? `${formatUsd(perTransaction)} per interaction`
          : null,
      checkedAt,
    };
  }
  const known = totals.monthly_cost_known_usd;
  return {
    label:
      typeof known === "number" && Number.isFinite(known)
        ? `Partial estimate: ${formatUsd(known)} a month priced so far`
        : "Partial estimate",
    detail: "Some lines are not priced yet.",
    checkedAt,
  };
}

// ---------------------------------------------------------------------------
// Now: state, root blocker and exactly one action
// ---------------------------------------------------------------------------

function progressHeadline(gate) {
  const milestone = stageEntry(gate.stage).milestone;
  if (gate.state === "proven") {
    return milestone === "publish"
      ? "Published on Foundry — not yet verified live"
      : "Live and verified — remaining checks pending";
  }
  if (milestone === "design") return "Designed — nothing is live yet";
  if (milestone === "controls") return "Designed, controls prepared offline — not yet live on Azure";
  return "Designed and prepared offline — not yet live on Azure";
}

// The state names the furthest evidenced progress; the action stays the
// planner's real next step (which may be an earlier check or re-run).
function allGates(proofPath) {
  return [
    ...(proofPath.workspaceCheck ? [proofPath.workspaceCheck] : []),
    ...proofPath.milestones.flatMap((milestone) => milestone.gates),
  ];
}

function stateHeadline(gates, root) {
  const rootIndex = gates.findIndex((gate) => gate.stage === root);
  let furthest = -1;
  gates.forEach((gate, index) => {
    if (!BOOTSTRAP_STAGES.has(gate.stage) && EVIDENCED_STATES.has(gate.state)) {
      furthest = index;
    }
  });
  return furthest > rootIndex ? progressHeadline(gates[furthest]) : stageEntry(root).headline;
}

const REFRESH_WHY =
  "Threadlight recomputes the plan from chat; it reads files only and changes nothing in Azure.";

function buildNow({ empty, planStatus, proofPath, plan, readiness }) {
  if (empty) {
    return {
      tone: "empty",
      blockerTone: null,
      headline: "No pilot in this workspace yet",
      blocker: null,
      why: null,
      action: { label: "Describe your process", intent: { type: "start_pilot" } },
    };
  }

  const refresh = { label: "Refresh the plan", intent: { type: "refresh_plan" } };
  if (planStatus.status === "missing" || planStatus.status === "invalid") {
    return {
      tone: "unknown",
      blockerTone: "attention",
      headline: "Status not computed yet",
      blocker:
        planStatus.status === "missing"
          ? "Threadlight hasn't worked out the next step for this workspace yet."
          : "The saved plan can't be read, so the next step is unknown.",
      why: REFRESH_WHY,
      action: refresh,
    };
  }

  const root = proofPath.root;
  const hardStop = plan.next_action.type === "hard_stop";
  const gates = allGates(proofPath);
  let now;
  if (root === null && gates.some((gate) => gate.state === "failed" || gate.state === "stale")) {
    // The plan has nothing left to run, but evidence behind a passed stage has
    // since failed or expired: never claim it is proven.
    return {
      tone: "attention",
      blockerTone: "attention",
      headline: "Some evidence is out of date",
      blocker: "Evidence behind a completed step has expired or failed since the plan was computed.",
      why: REFRESH_WHY,
      action: refresh,
    };
  }
  if (root === null) {
    now =
      readiness.state === "ready"
        ? {
            tone: "ready",
            blockerTone: null,
            headline: "Ready to go live",
            blocker: null,
            why: "Threadlight prepares the handoff from chat; you decide when to go live.",
            action: { label: "Prepare the production handoff", intent: { type: "prepare_handoff" } },
          }
        : {
            tone: "progress",
            blockerTone: null,
            headline: "Proven live — go-live decision pending",
            blocker:
              readiness.state === "none"
                ? "Production readiness hasn't been assessed yet."
                : readiness.detail,
            why: "Threadlight assesses readiness from chat; the go-live decision stays yours.",
            action: {
              label: "Assess production readiness",
              intent: { type: "invoke_skill", skillId: "threadlight-production-ready", phase: "go-live" },
            },
          };
  } else {
    const entry = stageEntry(root);
    const gate = gates.find((candidate) => candidate.stage === root);
    const expired = gates.filter(
      (candidate) => !candidate.current && (candidate.state === "stale" || candidate.state === "failed"),
    );
    now = hardStop
      ? {
          tone: "blocked",
          blockerTone: "blocked",
          headline: `Blocked at “${entry.title}”`,
          blocker: gate?.technical.reason || entry.need,
          why: entry.why,
          action: { label: `Resolve the block in “${entry.title}”`, intent: { type: "advance_plan", stage: root } },
        }
      : expired.length > 0
        ? {
            // Evidence behind an earlier step expired or failed: never claim
            // progress past it, but keep the planner's real next step.
            tone: "attention",
            blockerTone: "attention",
            headline: "Some evidence is out of date",
            blocker: `Evidence for ${expired.map((item) => `“${item.title}”`).join(", ")} is out of date or failing; refresh it before relying on it.`,
            why: entry.why,
            action: { label: entry.action, intent: { type: "advance_plan", stage: root } },
          }
        : {
            tone: "progress",
            blockerTone: null,
            headline: stateHeadline(gates, root),
            blocker: entry.need,
            why: entry.why,
            action: { label: entry.action, intent: { type: "advance_plan", stage: root } },
          };
  }

  if (planStatus.status === "stale") {
    const changed = planStatus.changedPath ? ` (${planStatus.changedPath} changed)` : "";
    // The headline stays the last known state; the blocker carries the warning.
    return {
      ...now,
      blockerTone: "attention",
      blocker:
        planStatus.reason === "expired"
          ? "The plan is more than a day old, so live evidence may have expired."
          : `Files changed after the plan was computed${changed}, so the next step may be wrong.`,
      why: REFRESH_WHY,
      action: refresh,
    };
  }
  return now;
}

// ---------------------------------------------------------------------------
// Technical detail (secondary): skills, evidence and the planner's reasons
// ---------------------------------------------------------------------------

function buildTechnical({ phases, plan, planStatus, errors }) {
  const skills = [];
  const evidence = [];
  for (const phase of phases ?? []) {
    for (const skill of phase?.skills ?? []) {
      const id = skill?.definition?.id;
      if (!id) continue;
      const definition = skill.definition;
      skills.push({
        id,
        label: definition.label,
        milestone: phase.id,
        status: skill.status,
        evidenceState: skill.evidenceState ?? null,
        role: definition.role,
        stage: definition.stage ?? null,
      });
      for (const item of skill.evidence ?? []) {
        evidence.push({
          path: item.path,
          milestone: phase.id,
          skillId: id,
          status: skill.status,
          evidenceState: skill.evidenceState ?? null,
          modifiedAt: item.modifiedAt ?? null,
          live: definition.stage ? stageEntry(definition.stage).live : false,
        });
      }
    }
  }
  const planned = ["current", "stale"].includes(planStatus.status);
  const handoffs = planned && Array.isArray(plan.manual_handoffs)
    ? plan.manual_handoffs
        .filter((handoff) => isPlainObject(handoff) && SKILL_IDS.has(handoff.skill))
        .map((handoff) => ({
          skillId: handoff.skill,
          manifest: typeof handoff.manifest === "string" ? handoff.manifest : null,
          status: typeof handoff.status === "string" ? handoff.status : null,
        }))
    : [];
  return { skills, evidence, handoffs, errors: Array.isArray(errors) ? errors : [] };
}

export function buildProcessModel({
  spec = null,
  manifest = null,
  plan = null,
  planStatus = { status: "missing", decidedAt: null },
  phases = [],
  governanceText = null,
  cost = null,
  readiness = null,
  evals = null,
  errors = [],
  generatedAt = null,
} = {}) {
  const empty = !spec && !isPlainObject(manifest);
  const readinessView = buildReadiness(readiness);
  const proofPath = buildProofPath({
    plan,
    planStatus,
    phases,
    readinessState: readinessView.state,
  });
  const name =
    spec?.name ||
    (typeof manifest?.name === "string" ? humanize(manifest.name) : "") ||
    (empty ? "" : "Untitled pilot");
  const description =
    spec?.description || (typeof manifest?.description === "string" ? manifest.description : "");

  return {
    empty,
    updatedAt: generatedAt,
    pilot: { name, description },
    now: buildNow({ empty, planStatus, proofPath, plan, readiness: readinessView }),
    plan: {
      status: planStatus.status,
      reason: planStatus.reason ?? null,
      decidedAt: planStatus.decidedAt ?? null,
      changedPath: planStatus.changedPath ?? null,
    },
    process: buildProcess(spec, governanceText, evals, phases, proofPath),
    proofPath,
    goLive: {
      readiness: { ...readinessView, source: `Assessed in “${phaseMeta("go-live").title}”` },
      cost: {
        ...buildCost(cost),
        source: `Estimated in “${phaseMeta(STAGE_CATALOG.cost_projection.milestone).title}”`,
      },
    },
    technical: buildTechnical({ phases, plan, planStatus, errors }),
  };
}
