import assert from "node:assert/strict";
import test from "node:test";

import { createLifecycleCanvas } from "../../.github/extensions/threadlight-lifecycle/lib/canvas-provider.mjs";

function createHarness() {
  const sent = [];
  const projected = [];
  const servers = [];
  const canvas = createLifecycleCanvas({
    createCanvas: (options) => options,
    webRoot: new URL("../../.github/extensions/threadlight-lifecycle/web/", import.meta.url),
    getSession: () => ({
      send: async (payload) => {
        sent.push(payload);
      },
    }),
    projectWorkspace: async (workspace) => {
      projected.push(workspace);
      return { summary: `Projected ${workspace}`, phases: [] };
    },
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
        },
      };
      servers.push(server);
      return server;
    },
  });

  return { canvas, projected, sent, servers };
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
});

test("supported open projects workspace and closes its loopback server", async () => {
  const { canvas, projected, servers } = createHarness();

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
  assert.equal(typeof servers[0].options.getModel, "function");
  assert.deepEqual(await servers[0].options.getModel(), {
    summary: "Projected /tmp/pilot",
    phases: [],
  });

  await canvas.onClose({ instanceId: "threadlight-spike" });
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
