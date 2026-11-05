import { spawnSync } from "node:child_process";
import { cp, mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = fileURLToPath(new URL("../../", import.meta.url));
const EXAMPLE = path.join(REPO_ROOT, "examples/returns-triage-governed");
const ORCHESTRATOR = path.join(
  REPO_ROOT,
  "skills/threadlight-auto/references/orchestrator.py",
);

export function pythonAvailable() {
  const probe = spawnSync("python3", ["--version"], { encoding: "utf8" });
  return probe.status === 0;
}

/**
 * Run threadlight-auto's real planner. Test/fixture setup only: the Canvas
 * itself never spawns processes; it reads the committed .threadlight/auto-next.json.
 */
export function runOrchestrator(workspace, { commit = true } = {}) {
  const args = [ORCHESTRATOR, "--workspace", workspace, "--output", "json"];
  args.push(commit ? "--commit" : "--dry-run");
  const result = spawnSync("python3", args, { encoding: "utf8", cwd: REPO_ROOT });
  if (result.status !== 0 && result.status !== 1) {
    throw new Error(`orchestrator failed (${result.status}): ${result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

/** Copy the governed returns example into a disposable workspace. */
export async function copyExampleWorkspace(name) {
  const root = fileURLToPath(
    new URL(`./.tmp-${name}-${process.pid}-${Date.now()}/`, import.meta.url),
  );
  const workspace = path.join(root, "returns-triage-governed");
  await mkdir(root, { recursive: true });
  await cp(EXAMPLE, workspace, {
    recursive: true,
    filter: (source) =>
      !source.split(path.sep).some((part) => part === "__pycache__" || part === ".threadlight"),
  });
  return {
    root,
    workspace,
    async cleanup() {
      await rm(root, { recursive: true, force: true });
    },
  };
}
