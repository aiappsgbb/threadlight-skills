import assert from "node:assert/strict";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { watchWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/workspace-watcher.mjs";

const SCRATCH_ROOT = path.resolve(".test-workspaces");

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForRefreshCount(getCount, expected, timeoutMs = 2_000) {
  const startedAt = Date.now();
  while (getCount() < expected) {
    if (Date.now() - startedAt >= timeoutMs) {
      assert.fail(
        `Timed out waiting for ${expected} refresh(es); received ${getCount()}`,
      );
    }
    await delay(10);
  }
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
    await waitForRefreshCount(() => refreshes, 1);
    // Any duplicate callback from this burst is due within one debounce window.
    await delay(80);

    assert.equal(refreshes, 1);

    await writeFile(path.join(root, "specs", "SPEC.md"), "# Pilot\n\nUpdated\n");
    await waitForRefreshCount(() => refreshes, 2);

    assert.equal(refreshes, 2);
  } finally {
    watcher?.close();
    await rm(root, { recursive: true, force: true });
    await rm(SCRATCH_ROOT, { recursive: true, force: true });
  }
});

test("workspace watcher refreshes Improve and Handoff evidence", async () => {
  const root = await createScratchWorkspace("workspace-watcher-evidence");
  let watcher;
  let refreshes = 0;

  try {
    await mkdir(path.join(root, "tests"));
    await mkdir(path.join(root, "router-bench-out"));
    watcher = await watchWorkspace(
      root,
      async () => {
        refreshes += 1;
      },
      { debounceMs: 40 },
    );

    await writeFile(
      path.join(root, "tests", "production-readiness-manifest.json"),
      "{}\n",
    );
    await waitForRefreshCount(() => refreshes, 1);

    await writeFile(
      path.join(root, "router-bench-out", "learnings-1.md"),
      "# Learnings\n",
    );
    await waitForRefreshCount(() => refreshes, 2);

    assert.equal(refreshes, 2);
  } finally {
    watcher?.close();
    await rm(root, { recursive: true, force: true });
    await rm(SCRATCH_ROOT, { recursive: true, force: true });
  }
});
