// Playwright fixture: real Canvas server over disposable copies of the governed
// returns example. Fixture setup may run threadlight-auto's planner; the Canvas
// itself only reads the committed .threadlight/auto-next.json.
//   +0  example with a current plan (planner run with --commit)
//   +1  empty workspace (no SPEC)
//   +2  example without a plan (never computed)
//   +3  example whose plan is out of date (SPEC changed after the plan)
import { readdirSync, rmSync } from "node:fs";
import { mkdir, rm, utimes } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createLoopbackServer } from "../../.github/extensions/threadlight-lifecycle/lib/http-server.mjs";
import { projectWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/projector.mjs";
import { copyExampleWorkspace, pythonAvailable, runOrchestrator } from "./example-workspace.mjs";

const fixtureDir = fileURLToPath(new URL("./", import.meta.url));

// Playwright may stop this server with SIGKILL; sweep earlier fixture copies.
function sweepFixtureCopies() {
  for (const entry of readdirSync(fixtureDir)) {
    if (entry.startsWith(".tmp-fixture-")) {
      rmSync(path.join(fixtureDir, entry), { recursive: true, force: true });
    }
  }
}
sweepFixtureCopies();
process.once("exit", sweepFixtureCopies);

const webRoot = new URL("../../.github/extensions/threadlight-lifecycle/web/", import.meta.url);
const cleanups = [];

async function serve(port, root) {
  const server = await createLoopbackServer({
    webRoot,
    token: "canvas-test",
    port,
    getModel: () => projectWorkspace(root),
    onIntent: async () => {},
  });
  console.log(`${server.origin} -> ${path.basename(path.dirname(root))}`);
  return server;
}

// CANVAS_FIXTURE_PORT moves all four servers (default 4187..4190), so a run
// never reuses a fixture server someone else left running on the defaults.
const BASE_PORT = Number(process.env.CANVAS_FIXTURE_PORT ?? 4187);

const hasPython = pythonAvailable();
if (!hasPython) {
  console.warn("python3 not found: the 'current plan' fixture will show a missing plan.");
}

const current = await copyExampleWorkspace("fixture-current");
cleanups.push(current.cleanup);
if (hasPython) runOrchestrator(current.workspace, { commit: true });

const emptyRoot = fileURLToPath(new URL(`./.tmp-fixture-empty-${process.pid}/`, import.meta.url));
await mkdir(emptyRoot, { recursive: true });
cleanups.push(() => rm(emptyRoot, { recursive: true, force: true }));

const missing = await copyExampleWorkspace("fixture-missing");
cleanups.push(missing.cleanup);

const stale = await copyExampleWorkspace("fixture-stale");
cleanups.push(stale.cleanup);
if (hasPython) {
  const report = runOrchestrator(stale.workspace, { commit: true });
  const later = new Date(new Date(report.decided_at).getTime() + 60_000);
  await utimes(path.join(stale.workspace, "specs/SPEC.md"), later, later);
}

const servers = [
  await serve(BASE_PORT + 0, current.workspace),
  await serve(BASE_PORT + 1, emptyRoot),
  await serve(BASE_PORT + 2, missing.workspace),
  await serve(BASE_PORT + 3, stale.workspace),
];

async function shutdown() {
  await Promise.all(servers.map((server) => server.close()));
  for (const cleanup of cleanups) {
    await cleanup();
  }
  process.exit(0);
}

process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
