import assert from "node:assert/strict";
import test from "node:test";

import { createLifecycleCanvas } from "../../.github/extensions/threadlight-lifecycle/lib/canvas-provider.mjs";

function createHarness({ projectWorkspace, watchWorkspace } = {}) {
  const sent = [];
  const logs = [];
  const projected = [];
  const servers = [];
  const watchers = [];
  const closeEvents = [];
  const canvas = createLifecycleCanvas({
    createCanvas: (options) => options,
    webRoot: new URL("../../.github/extensions/threadlight-lifecycle/web/", import.meta.url),
    getSession: () => ({
      send: async (payload) => {
        sent.push(payload);
      },
      log: async (message, options) => {
        logs.push({ message, options });
      },
    }),
    projectWorkspace: async (workspace) => {
      projected.push(workspace);
      if (projectWorkspace) {
        return projectWorkspace(workspace);
      }
      return { summary: `Projected ${workspace}`, phases: [], errors: [] };
    },
    watchWorkspace:
      watchWorkspace ??
      (async (workspace, callback, options) => {
        const watcher = {
          workspace,
          callback,
          options,
          closed: false,
          close() {
            this.closed = true;
            closeEvents.push("watcher");
          },
        };
        watchers.push(watcher);
        return watcher;
      }),
    createServer: async (options) => {
      const server = {
        url: "http://127.0.0.1/fake",
        publishCount: 0,
        closed: false,
        options,
        publish() {
          this.publishCount += 1;
        },
        async close() {
          this.closed = true;
          closeEvents.push("server");
        },
      };
      servers.push(server);
      return server;
    },
  });

  return { canvas, projected, sent, logs, servers, watchers, closeEvents };
}

test("unsupported hosts report unavailable without starting a server", async () => {
  const { canvas, servers } = createHarness();

  const result = await canvas.open({
    instanceId: "unsupported",
    host: { capabilities: { canvases: false } },
    session: { workingDirectory: "/tmp/pilot" },
  });

  assert.deepEqual(result, {
    title: "Threadlight Lifecycle",
    status: "Canvas rendering unavailable",
  });
  assert.equal(servers.length, 0);
});

test("provider exposes refresh and prepare_intent actions in order", () => {
  const { canvas } = createHarness();

  assert.deepEqual(
    canvas.actions.map((action) => action.name),
    ["refresh", "prepare_intent"],
  );
  assert.equal(canvas.inputSchema.properties.phase.type, "string");
  assert.equal(Object.hasOwn(canvas, "title"), false);
});

test("supported open projects workspace and closes its loopback server and watcher", async () => {
  const { canvas, projected, servers, watchers, closeEvents } = createHarness();

  const result = await canvas.open({
    instanceId: "threadlight-spike",
    session: { workingDirectory: "/tmp/pilot" },
  });

  assert.deepEqual(result, {
    url: "http://127.0.0.1/fake",
    title: "Threadlight Lifecycle",
    status: "Projected /tmp/pilot",
  });
  assert.deepEqual(projected, ["/tmp/pilot"]);
  assert.equal(servers.length, 1);
  assert.equal(watchers.length, 1);
  assert.equal(watchers[0].workspace, "/tmp/pilot");
  assert.equal(typeof watchers[0].callback, "function");
  assert.equal(watchers[0].options.debounceMs, undefined);
  assert.equal(typeof watchers[0].options.onError, "function");
  assert.equal(typeof servers[0].options.getModel, "function");
  assert.deepEqual(await servers[0].options.getModel(), {
    summary: "Projected /tmp/pilot",
    phases: [],
    errors: [],
  });

  await canvas.onClose({ instanceId: "threadlight-spike" });
  assert.equal(watchers[0].closed, true);
  assert.equal(servers[0].closed, true);
  assert.deepEqual(closeEvents, ["watcher", "server"]);
});

test("supported open reuses an existing instance for the same canvas id", async () => {
  const { canvas, projected, servers, watchers } = createHarness();

  const first = await canvas.open({
    instanceId: "threadlight-spike",
    session: { workingDirectory: "/tmp/pilot" },
  });
  const second = await canvas.open({
    instanceId: "threadlight-spike",
    session: { workingDirectory: "/tmp/pilot" },
  });

  assert.deepEqual(first, {
    url: "http://127.0.0.1/fake",
    title: "Threadlight Lifecycle",
    status: "Projected /tmp/pilot",
  });
  assert.deepEqual(second, first);
  assert.equal(servers.length, 1);
  assert.equal(watchers.length, 1);
  assert.deepEqual(projected, ["/tmp/pilot"]);

  await canvas.onClose({ instanceId: "threadlight-spike" });
  assert.equal(watchers[0].closed, true);
  assert.equal(servers[0].closed, true);
});

test("watcher callback reprojects the workspace model and publishes it", async () => {
  let projectionCount = 0;
  const { canvas, projected, servers, watchers } = createHarness({
    projectWorkspace: async (workspace) => {
      projectionCount += 1;
      return {
        summary: `Projected ${workspace} #${projectionCount}`,
        phases: [],
        errors: [],
      };
    },
  });

  await canvas.open({
    instanceId: "threadlight-spike",
    session: { workingDirectory: "/tmp/pilot" },
  });
  await watchers[0].callback();

  assert.deepEqual(projected, ["/tmp/pilot", "/tmp/pilot"]);
  assert.equal(servers[0].publishCount, 1);
  assert.deepEqual(await servers[0].options.getModel(), {
    summary: "Projected /tmp/pilot #2",
    phases: [],
    errors: [],
  });
});

test("watcher errors become visible model errors and extension logs", async () => {
  const existingError = {
    code: "artifact-parse-failed",
    path: "specs/manifest.json",
    message: "Bad JSON",
  };
  const { canvas, logs, servers, watchers } = createHarness({
    projectWorkspace: async (workspace) => ({
      summary: `Projected ${workspace}`,
      phases: [],
      errors: [existingError],
    }),
  });

  await canvas.open({
    instanceId: "threadlight-spike",
    session: { workingDirectory: "/tmp/pilot" },
  });
  await watchers[0].options.onError(new Error("watch blew up"));

  assert.equal(servers[0].publishCount, 1);
  assert.deepEqual(await servers[0].options.getModel(), {
    summary: "Workspace refresh failed",
    phases: [],
    errors: [
      existingError,
      {
        code: "workspace-refresh-failed",
        path: null,
        message: "watch blew up",
      },
    ],
  });
  assert.deepEqual(logs, [
    {
      message: "Threadlight Canvas refresh failed: watch blew up",
      options: { level: "error" },
    },
  ]);
});

test("watcher setup failure closes the server before propagating", async () => {
  const { canvas, servers } = createHarness({
    watchWorkspace: async () => {
      throw new Error("watch unavailable");
    },
  });

  await assert.rejects(
    canvas.open({
      instanceId: "threadlight-spike",
      session: { workingDirectory: "/tmp/pilot" },
    }),
    /watch unavailable/,
  );
  assert.equal(servers.length, 1);
  assert.equal(servers[0].closed, true);
});

test("server intent handler sends exactly one visible chat prompt", async () => {
  const { canvas, sent, servers } = createHarness();

  await canvas.open({
    instanceId: "threadlight-spike",
    session: { workingDirectory: "/tmp/pilot" },
  });
  await servers[0].options.onIntent({ type: "prepare_handoff" });

  assert.equal(sent.length, 1);
  assert.deepEqual(Object.keys(sent[0]), ["prompt"]);
  assert.match(sent[0].prompt, /^\[Threadlight Canvas intent\]/);
});

test("refresh action reprojects the workspace model and publishes it", async () => {
  const { canvas, projected, servers } = createHarness();

  await canvas.open({
    instanceId: "threadlight-spike",
    session: { workingDirectory: "/tmp/pilot" },
  });
  const result = await canvas.actions[0].handler({
    instanceId: "threadlight-spike",
  });

  assert.deepEqual(result, { status: "Projected /tmp/pilot" });
  assert.deepEqual(projected, ["/tmp/pilot", "/tmp/pilot"]);
  assert.equal(servers[0].publishCount, 1);
});

test("prepare_intent action validates and submits through chat", async () => {
  const { canvas, sent } = createHarness();

  await canvas.open({
    instanceId: "threadlight-spike",
    session: { workingDirectory: "/tmp/pilot" },
  });
  const result = await canvas.actions[1].handler({
    instanceId: "threadlight-spike",
    input: { intent: { type: "prepare_handoff" } },
  });

  assert.deepEqual(result, {
    accepted: true,
    intent: { type: "prepare_handoff" },
  });
  assert.equal(sent.length, 1);
});
