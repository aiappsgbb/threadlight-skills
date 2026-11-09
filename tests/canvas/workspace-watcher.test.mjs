import assert from "node:assert/strict";
import { appendFile, chmod, mkdir, rename, rm, symlink, truncate, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { watchWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/workspace-watcher.mjs";

const SCRATCH_ROOT = path.resolve(".test-workspaces");
// Generous bound: only reached on a real failure, never on the happy path.
const WAIT_TIMEOUT_MS = 15_000;
const DEBOUNCE_MS = 40;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Poll a condition with a bounded timeout instead of sleeping a fixed time:
// a loaded runner only makes this slower, never wrong.
async function waitFor(condition, describe, timeoutMs = WAIT_TIMEOUT_MS) {
  const startedAt = Date.now();
  while (!condition()) {
    if (Date.now() - startedAt >= timeoutMs) {
      assert.fail(`Timed out after ${timeoutMs} ms waiting for ${describe()}`);
    }
    await delay(10);
  }
}

// Watch a scratch workspace and record every refresh and file-system event.
async function observe(root, { debounceMs = DEBOUNCE_MS } = {}) {
  const state = { refreshes: 0, events: [], errors: [], watcher: null, baseline: 0 };
  state.watcher = await watchWorkspace(
    root,
    async () => {
      state.refreshes += 1;
    },
    {
      debounceMs,
      onEvent: (event) => state.events.push(event),
      onError: async (error) => {
        state.errors.push(error);
      },
    },
  );
  state.baseline = (await ready(state, root)).refreshes;
  return state;
}

// Wait until no event or refresh has happened for a few debounce windows, so a
// debounced refresh that is still pending cannot land after the caller's check.
async function settle(state, debounceMs = DEBOUNCE_MS) {
  const quietMs = debounceMs * 4;
  let seen = -1;
  let quietSince = Date.now();
  await waitFor(() => {
    const now = state.events.length + state.refreshes;
    if (now !== seen) {
      seen = now;
      quietSince = Date.now();
    }
    return Date.now() - quietSince >= quietMs;
  }, () => `the watcher to settle (${state.events.length} events, ${state.refreshes} refreshes)`);
}

// fs.watch can start delivering events a little after it returns (macOS
// FSEvents under load). Touch a probe until its event arrives, then settle, so
// every test acts on a live watcher and starts from a quiet baseline.
async function ready(state, root) {
  const probe = path.join(root, ".watch-probe");
  let attempt = 0;
  await waitFor(() => {
    if (state.events.some((e) => e.filename === ".watch-probe")) {
      return true;
    }
    if (attempt++ % 20 === 0) {
      void writeFile(probe, `${attempt}\n`);
    }
    return false;
  }, () => "the watcher to report its probe file");
  await settle(state);
  await rm(probe, { force: true });
  await settle(state);
  return { refreshes: state.refreshes, mark: state.events.length };
}

async function waitForRefreshes(state, atLeast) {
  await waitFor(
    () => state.refreshes >= atLeast,
    () => `${atLeast} refresh(es); received ${state.refreshes}`,
  );
}

// Under load an editor save can reach the watcher as several events further
// apart than the debounce window; each may legitimately publish a refresh.
async function splitWrite(file, content) {
  await truncate(file);
  await delay(DEBOUNCE_MS * 3);
  await appendFile(file, content);
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
  const staging = await createScratchWorkspace("workspace-watcher-staging");
  let state;

  try {
    state = await observe(root);
    // One atomic rename: the root watcher sees a single new `specs` entry.
    await writeFile(path.join(staging, "SPEC.md"), "# Pilot\n");
    await writeFile(path.join(staging, "manifest.json"), "{}\n");
    await rename(staging, path.join(root, "specs"));
    await waitForRefreshes(state, state.baseline + 1);
    await settle(state);
    assert.equal(state.refreshes, state.baseline + 1, "one debounced refresh for one burst");

    // The refresh attached `specs`, so a later edit inside it is seen.
    const before = state.refreshes;
    await writeFile(path.join(root, "specs", "SPEC.md"), "# Pilot\n\nUpdated\n");
    await waitForRefreshes(state, before + 1);
    assert.ok(state.events.some((e) => e.target === path.join(root, "specs") && !e.ignored));
    assert.deepEqual(state.errors, []);
  } finally {
    state?.watcher.close();
    await rm(root, { recursive: true, force: true });
    await rm(staging, { recursive: true, force: true });
  }
});

test("workspace watcher refreshes Improve and Handoff evidence", async () => {
  const root = await createScratchWorkspace("workspace-watcher-evidence");
  let state;

  try {
    state = await observe(root);

    await mkdir(path.join(root, "tests"));
    await mkdir(path.join(root, "router-bench-out"));
    await waitForRefreshes(state, state.baseline + 1);
    await settle(state);

    let before = state.refreshes;
    await writeFile(path.join(root, "tests", "production-readiness-manifest.json"), "{}\n");
    await waitForRefreshes(state, before + 1);
    await settle(state);

    before = state.refreshes;
    await writeFile(path.join(root, "router-bench-out", "learnings-1.md"), "# Learnings\n");
    await waitForRefreshes(state, before + 1);
    assert.deepEqual(state.errors, []);
  } finally {
    state?.watcher.close();
    await rm(root, { recursive: true, force: true });
  }
});

test("workspace watcher refreshes when azd env evidence appears under .azure", async () => {
  const root = await createScratchWorkspace("workspace-watcher-azure-env");
  let state;

  try {
    state = await observe(root);

    await mkdir(path.join(root, ".azure"));
    await waitForRefreshes(state, state.baseline + 1);
    await settle(state);

    let before = state.refreshes;
    await mkdir(path.join(root, ".azure", "dev"));
    await waitForRefreshes(state, before + 1);
    await settle(state);

    const env = path.join(root, ".azure", "dev", ".env");
    before = state.refreshes;
    await writeFile(env, "AGENT_FQDN=threadlight-dev.example.com\n");
    await waitForRefreshes(state, before + 1);
    await settle(state);

    before = state.refreshes;
    await splitWrite(env, "AGENT_FQDN=threadlight-prod.example.com\n");
    await waitForRefreshes(state, before + 1);
    assert.ok(state.events.some((e) => e.target === path.join(root, ".azure", "dev") && !e.ignored));
    assert.deepEqual(state.errors, []);
  } finally {
    state?.watcher.close();
    await rm(root, { recursive: true, force: true });
  }
});

test("workspace watcher ignores symlinked .azure roots", async () => {
  const root = await createScratchWorkspace("workspace-watcher-azure-symlink");
  const external = await createScratchWorkspace("workspace-watcher-azure-external");
  let state;

  try {
    await mkdir(path.join(external, "dev"), { recursive: true });
    await symlink(external, path.join(root, ".azure"));

    state = await observe(root);
    await settle(state);
    const baseline = state.refreshes;
    const mark = state.events.length;

    await writeFile(path.join(external, "dev", ".env"), "AGENT_FQDN=threadlight-dev.example.com\n");
    // A watched sentinel proves the watcher is live; await its own event.
    await writeFile(path.join(root, "sentinel.txt"), "x\n");
    await waitFor(
      () => state.events.slice(mark).some((e) => e.filename === "sentinel.txt"),
      () => "the sentinel event",
    );
    await waitForRefreshes(state, baseline + 1);
    await settle(state);

    const later = state.events.slice(mark);
    assert.ok(later.every((e) => !e.target.startsWith(external)), "no watcher follows the .azure symlink");
    assert.ok(later.every((e) => e.filename === "sentinel.txt"), JSON.stringify(later));
    assert.deepEqual(state.errors, []);
  } finally {
    state?.watcher.close();
    await rm(root, { recursive: true, force: true });
    await rm(external, { recursive: true, force: true });
  }
});

test("workspace watcher ignores .azure files and still watches other roots", async () => {
  const root = await createScratchWorkspace("workspace-watcher-azure-file");
  let state;

  try {
    await writeFile(path.join(root, ".azure"), "not a directory\n");
    await mkdir(path.join(root, "specs"));
    await writeFile(path.join(root, "specs", "SPEC.md"), "# Pilot\n");

    state = await observe(root);
    await settle(state);
    const baseline = state.refreshes;
    const mark = state.events.length;

    await writeFile(path.join(root, ".azure"), "still not a directory\n");
    await waitFor(
      () => state.events.slice(mark).some((e) => e.filename === ".azure"),
      () => "the .azure file event",
    );
    await settle(state);
    const azureEvents = state.events.slice(mark);
    assert.ok(azureEvents.every((e) => e.filename === ".azure" && e.ignored), JSON.stringify(azureEvents));
    assert.equal(state.refreshes, baseline, "an ignored event never schedules a refresh");
    assert.deepEqual(state.errors, []);

    await splitWrite(path.join(root, "specs", "SPEC.md"), "# Pilot\n\nUpdated\n");
    await waitForRefreshes(state, baseline + 1);
    await settle(state);
    assert.deepEqual(state.errors, []);
  } finally {
    state?.watcher.close();
    await rm(root, { recursive: true, force: true });
  }
});

test("workspace watcher ignores unreadable .azure roots and still watches other roots", async () => {
  const root = await createScratchWorkspace("workspace-watcher-azure-unreadable");
  let state;

  try {
    await mkdir(path.join(root, ".azure"));
    await chmod(path.join(root, ".azure"), 0o000);
    await mkdir(path.join(root, "specs"));
    await writeFile(path.join(root, "specs", "SPEC.md"), "# Pilot\n");

    state = await observe(root);
    await settle(state);
    const baseline = state.refreshes;
    await writeFile(path.join(root, "specs", "SPEC.md"), "# Pilot\n\nUpdated\n");
    await waitForRefreshes(state, baseline + 1);
    await settle(state);

    assert.deepEqual(state.errors, []);
  } finally {
    await chmod(path.join(root, ".azure"), 0o755).catch(() => {});
    state?.watcher.close();
    await rm(root, { recursive: true, force: true });
  }
});

test("workspace watcher ignores unreadable azd env directories and still watches other roots", async () => {
  const root = await createScratchWorkspace("workspace-watcher-azure-env-unreadable");
  let state;

  try {
    await mkdir(path.join(root, ".azure", "dev"), { recursive: true });
    await chmod(path.join(root, ".azure", "dev"), 0o000);
    await mkdir(path.join(root, "specs"));
    await writeFile(path.join(root, "specs", "SPEC.md"), "# Pilot\n");

    state = await observe(root);
    await settle(state);
    const baseline = state.refreshes;
    await writeFile(path.join(root, "specs", "SPEC.md"), "# Pilot\n\nUpdated\n");
    await waitForRefreshes(state, baseline + 1);
    await settle(state);

    assert.deepEqual(state.errors, []);
  } finally {
    await chmod(path.join(root, ".azure", "dev"), 0o755).catch(() => {});
    state?.watcher.close();
    await rm(root, { recursive: true, force: true });
  }
});
