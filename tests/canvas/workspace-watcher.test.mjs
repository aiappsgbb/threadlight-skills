import assert from "node:assert/strict";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { watchWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/workspace-watcher.mjs";

const SCRATCH_ROOT = path.resolve(".test-workspaces");

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function createScratchWorkspace(name) {
  const workspace = path.join(
    SCRATCH_ROOT,
    `${name}-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  );
  await mkdir(workspace, { recursive: true });
  return workspace;
}

test("workspace watcher attaches newly created roots before publishing debounced refreshes", async () => {
  const root = await createScratchWorkspace("workspace-watcher");
  let watcher;
  let refreshes = 0;

  try {
    watcher = await watchWorkspace(
      root,
      async () => {
        refreshes += 1;
      },
      { debounceMs: 40 },
    );

    await mkdir(path.join(root, "specs"));
    await writeFile(path.join(root, "specs", "SPEC.md"), "# Pilot\n");
    await writeFile(path.join(root, "specs", "manifest.json"), "{}\n");
    await delay(120);

    assert.equal(refreshes, 1);

    await writeFile(path.join(root, "specs", "SPEC.md"), "# Pilot\n\nUpdated\n");
    await delay(120);

    assert.equal(refreshes, 2);
  } finally {
    watcher?.close();
    await rm(root, { recursive: true, force: true });
    await rm(SCRATCH_ROOT, { recursive: true, force: true });
  }
});
