import assert from "node:assert/strict";
import { readFile, utimes } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { STAGE_CATALOG } from "../../.github/extensions/threadlight-lifecycle/lib/process-model.mjs";
import { projectWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/projector.mjs";
import {
  copyExampleWorkspace,
  pythonAvailable,
  runOrchestrator,
} from "./example-workspace.mjs";

const HAS_PYTHON = pythonAvailable();
const SKIP = HAS_PYTHON ? false : "python3 is not available; cannot run the threadlight-auto planner";

test("every stage threadlight-auto can plan has a process-language entry", async () => {
  const source = await readFile(
    new URL("../../skills/threadlight-auto/references/orchestrator.py", import.meta.url),
    "utf8",
  );
  const lists = [...source.matchAll(/^(?:GOVERNANCE_)?STAGES = \[([\s\S]*?)\]/gm)];
  assert.equal(lists.length, 2, "STAGES and GOVERNANCE_STAGES found");
  const stages = new Set(
    lists.flatMap((match) => [...match[1].matchAll(/"([a-z_]+)"/g)].map((item) => item[1])),
  );
  for (const extra of ["presenter_package", "presenter_ready"]) {
    assert.match(source, new RegExp(`"${extra}"`));
    stages.add(extra);
  }
  assert.deepEqual(new Set(Object.keys(STAGE_CATALOG)), stages);
});

test("canvas gates equal the planner's stages for the governed example", { skip: SKIP }, async () => {
  const example = await copyExampleWorkspace("contract");
  try {
    const report = runOrchestrator(example.workspace, { commit: true });
    const model = await projectWorkspace(example.workspace);
    const view = model.view;

    assert.equal(view.plan.status, "current");
    assert.equal(view.proofPath.planned, true);
    const allGates = [
      ...(view.proofPath.workspaceCheck ? [view.proofPath.workspaceCheck] : []),
      ...view.proofPath.milestones.flatMap((milestone) => milestone.gates),
    ];
    const canvasStages = allGates.map((gate) => gate.stage);
    assert.deepEqual(canvasStages, report.stages);

    const root =
      report.next_action.type === "hard_stop"
        ? report.next_action.stage
        : report.next_action.stages_to_run[0];
    const current = allGates.filter((gate) => gate.current);
    assert.deepEqual(current.map((gate) => gate.stage), [root]);
    assert.deepEqual(view.now.action.intent, { type: "advance_plan", stage: root });
    assert.equal(model.summary, view.now.headline);
  } finally {
    await example.cleanup();
  }
});

test("a workspace change after the plan marks it out of date", { skip: SKIP }, async () => {
  const example = await copyExampleWorkspace("stale");
  try {
    const report = runOrchestrator(example.workspace, { commit: true });
    const later = new Date(new Date(report.decided_at).getTime() + 60_000);
    await utimes(path.join(example.workspace, "specs/SPEC.md"), later, later);

    const model = await projectWorkspace(example.workspace, { now: later });
    assert.equal(model.view.plan.status, "stale");
    assert.deepEqual(model.view.now.action.intent, { type: "refresh_plan" });
  } finally {
    await example.cleanup();
  }
});

test("canvas plan freshness covers the planner's gate fingerprint inputs", async () => {
  const { FRESHNESS_DIRECTORIES, FRESHNESS_FILES, FRESHNESS_PREFIXES } = await import(
    "../../.github/extensions/threadlight-lifecycle/lib/artifact-reader.mjs"
  );
  const source = await readFile(
    new URL("../../skills/threadlight-auto/references/orchestrator.py", import.meta.url),
    "utf8",
  );
  const body = source.slice(
    source.indexOf("def _gate_fingerprint("),
    source.indexOf("def _deployment_fingerprint("),
  );
  const deployment = source.slice(
    source.indexOf("def _deployment_fingerprint("),
    source.indexOf("GOVERNANCE_GATE_STATE ="),
  );
  const constants = Object.fromEntries(
    [...source.matchAll(/^([A-Z_]+) = "([^"]+)"$/gm)].map((match) => [match[1], match[2]]),
  );
  const resolve = (token) => (token.startsWith('"') ? token.slice(1, -1) : constants[token]);
  const list = (pattern, text) => {
    const match = pattern.exec(text);
    assert.ok(match, `pattern ${pattern} found in orchestrator.py`);
    return match[1].split(",").map((token) => token.trim()).filter(Boolean).map(resolve);
  };
  const files = [
    ...list(/paths = \[([\s\S]*?)\]/, body),
    ...list(/definitions = \{([\s\S]*?)\}/, body),
    ...list(/for name in \(([\s\S]*?)\):/, deployment),
  ];
  const directories = list(/directories = \{([\s\S]*?)\}/, body);
  // Deliberately not watched: azd environment inputs live under .azure, which
  // the Canvas never reads (its .env files hold secrets).
  const covered = (file) =>
    FRESHNESS_FILES.includes(file) ||
    FRESHNESS_PREFIXES.some((prefix) => file.startsWith(prefix)) ||
    FRESHNESS_DIRECTORIES.some((directory) => file.startsWith(`${directory}/`));
  for (const file of files) {
    assert.ok(file, "every listed input resolves to a path");
    assert.ok(covered(file), `${file} is a freshness input`);
  }
  for (const directory of directories) {
    assert.ok(FRESHNESS_DIRECTORIES.includes(directory), `${directory}/ is scanned`);
  }
});
