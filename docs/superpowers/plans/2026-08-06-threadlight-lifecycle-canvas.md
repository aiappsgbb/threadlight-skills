# Threadlight Lifecycle Canvas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a plugin-bundled GitHub Copilot App Canvas that lets a Solution Engineer start and steer a Threadlight pilot by outcome, without knowing the 17 skill names.

**Architecture:** A thin SDK adapter registers one `threadlight-lifecycle` canvas and serves a loopback-only web client. A host-independent registry and projector derive lifecycle state from an explicit artifact allowlist; UI actions are validated typed intents sent back to chat, never direct file, process, or Azure operations. Tasks 1-4 are the technical spike and end in a mandatory go/no-go checkpoint before the version 1 tasks continue.

**Tech Stack:** JavaScript ES modules, bundled `@github/copilot-sdk`, Node.js built-ins (`node:http`, `node:fs`, `node:test`), vanilla HTML/CSS/JS, Playwright, axe-core, GitHub Actions.

---

## File structure

Create or modify these files:

```text
plugin.json
.github/plugin/marketplace.json
.github/workflows/threadlight-canvas.yml
.github/extensions/threadlight-lifecycle/
  extension.mjs                 # SDK-only entry point
  README.md                     # support boundary and local troubleshooting
  lib/
    canvas-provider.mjs         # createCanvas wiring and instance lifecycle
    http-server.mjs             # tokenized loopback server and SSE
    intents.mjs                 # intent schemas, validation, chat prompt
    lifecycle-registry.mjs      # six phases and exact 17-skill mapping
    artifact-reader.mjs         # allowlist, symlink defense, typed read errors
    projector.mjs               # normalized lifecycle model
    workspace-watcher.mjs       # debounced watch of allowlisted roots
  web/
    index.html                  # accessible cockpit shell
    app.js                      # rendering, refresh, navigation, intent POSTs
    styles.css                  # panel-sized responsive design
tests/canvas/
  plugin-contract.test.mjs
  intents.test.mjs
  http-server.test.mjs
  canvas-provider.test.mjs
  lifecycle-registry.test.mjs
  artifact-reader.test.mjs
  projector.test.mjs
  workspace-watcher.test.mjs
  fixtures.mjs
  serve-canvas-fixture.mjs
tests/playwright/
  package.json
  canvas.config.mjs
  canvas-tests/lifecycle-canvas.spec.mjs
README.md
```

Keep `extension.mjs` limited to SDK imports and composition. All logic that can
run without the bundled SDK belongs in `lib/` so `node:test` can exercise it.

---

## Part A - Technical spike

### Task 1: Scaffold and package the extension

**Files:**
- Create: `.github/extensions/threadlight-lifecycle/extension.mjs`
- Create: `tests/canvas/plugin-contract.test.mjs`
- Modify: `plugin.json:108-110`

- [ ] **Step 1: Write the failing plugin contract test**

```javascript
// tests/canvas/plugin-contract.test.mjs
import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const rootUrl = new URL("../../", import.meta.url);

test("plugin exports the Threadlight lifecycle extension", async () => {
  const plugin = JSON.parse(
    await readFile(new URL("plugin.json", rootUrl), "utf8"),
  );

  assert.equal(plugin.extensions, ".github/extensions/");
  await access(
    new URL(
      ".github/extensions/threadlight-lifecycle/extension.mjs",
      rootUrl,
    ),
  );
});
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run:

```bash
node --test tests/canvas/plugin-contract.test.mjs
```

Expected: FAIL because `plugin.extensions` is undefined and the extension entry
does not exist.

- [ ] **Step 3: Scaffold the Canvas extension through the supported tool**

Invoke:

```text
extensions_manage({
  operation: "scaffold",
  kind: "canvas",
  name: "threadlight-lifecycle",
  location: "project"
})
```

Expected: `.github/extensions/threadlight-lifecycle/extension.mjs` exists and
contains a working `joinSession({ canvases: [createCanvas(...)] })` skeleton.
Do not hand-write a replacement skeleton; later tasks extract its handlers into
focused modules.

- [ ] **Step 4: Export the project extension from the plugin manifest**

Add the component path next to the existing skills path:

```json
  "skills": "skills/",
  "extensions": ".github/extensions/"
```

Do not change the plugin version yet; the release metadata changes only after
the full v1 is complete.

- [ ] **Step 5: Run the contract test**

Run:

```bash
node --test tests/canvas/plugin-contract.test.mjs
```

Expected: PASS.

- [ ] **Step 6: Commit the packaging contract**

```bash
git add plugin.json .github/extensions/threadlight-lifecycle/extension.mjs tests/canvas/plugin-contract.test.mjs
git commit -m "feat: scaffold Threadlight lifecycle canvas" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 2: Add typed, chat-only intents

**Files:**
- Create: `.github/extensions/threadlight-lifecycle/lib/intents.mjs`
- Create: `tests/canvas/intents.test.mjs`

- [ ] **Step 1: Write failing intent validation tests**

```javascript
// tests/canvas/intents.test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import {
  IntentValidationError,
  createIntentBroker,
  validateIntent,
} from "../../.github/extensions/threadlight-lifecycle/lib/intents.mjs";

test("start_pilot accepts a bounded brief and no skill name", () => {
  const intent = validateIntent({
    type: "start_pilot",
    brief: "Triage retail returns with supervisor approval.",
  });

  assert.deepEqual(intent, {
    type: "start_pilot",
    brief: "Triage retail returns with supervisor approval.",
  });
  assert.equal(JSON.stringify(intent).includes("threadlight-"), false);
});

test("unknown fields and intent types are rejected", () => {
  assert.throws(
    () => validateIntent({ type: "start_pilot", brief: "x", command: "rm -rf" }),
    IntentValidationError,
  );
  assert.throws(
    () => validateIntent({ type: "deploy_now" }),
    IntentValidationError,
  );
});

test("broker sends a visible structured intent to chat", async () => {
  const sent = [];
  const broker = createIntentBroker({
    send: async (message) => sent.push(message),
  });

  await broker.submit({
    type: "resume_phase",
    phase: "build-deploy",
  });

  assert.equal(sent.length, 1);
  assert.match(sent[0].prompt, /^\[Threadlight Canvas intent\]/);
  assert.match(sent[0].prompt, /"type": "resume_phase"/);
  assert.match(sent[0].prompt, /explain the proposed next action/i);
});
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run:

```bash
node --test tests/canvas/intents.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 3: Implement the closed intent schema and broker**

```javascript
// .github/extensions/threadlight-lifecycle/lib/intents.mjs
const PHASES = new Set([
  "design",
  "build-deploy",
  "discover",
  "protect-govern",
  "improve",
  "handoff",
]);

const TYPES = new Set([
  "start_pilot",
  "resume_phase",
  "inspect_evidence",
  "prepare_handoff",
]);

export class IntentValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "IntentValidationError";
  }
}

function requireExactKeys(value, allowed) {
  const unknown = Object.keys(value).filter((key) => !allowed.has(key));
  if (unknown.length > 0) {
    throw new IntentValidationError(
      `Unknown intent field(s): ${unknown.join(", ")}`,
    );
  }
}

function requireText(value, field, maxLength) {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new IntentValidationError(`${field} must be a non-empty string`);
  }
  if (value.length > maxLength) {
    throw new IntentValidationError(
      `${field} must be at most ${maxLength} characters`,
    );
  }
  return value.trim();
}

export function validateIntent(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new IntentValidationError("Intent must be an object");
  }
  if (!TYPES.has(value.type)) {
    throw new IntentValidationError(`Unsupported intent type: ${value.type}`);
  }

  if (value.type === "start_pilot") {
    requireExactKeys(value, new Set(["type", "brief"]));
    return {
      type: value.type,
      brief: requireText(value.brief, "brief", 4000),
    };
  }

  if (value.type === "resume_phase") {
    requireExactKeys(value, new Set(["type", "phase"]));
    if (!PHASES.has(value.phase)) {
      throw new IntentValidationError(`Unsupported phase: ${value.phase}`);
    }
    return { type: value.type, phase: value.phase };
  }

  if (value.type === "inspect_evidence") {
    requireExactKeys(value, new Set(["type", "phase", "evidenceId"]));
    if (!PHASES.has(value.phase)) {
      throw new IntentValidationError(`Unsupported phase: ${value.phase}`);
    }
    return {
      type: value.type,
      phase: value.phase,
      evidenceId: requireText(value.evidenceId, "evidenceId", 160),
    };
  }

  requireExactKeys(value, new Set(["type"]));
  return { type: value.type };
}

export function createIntentBroker({ send }) {
  if (typeof send !== "function") {
    throw new TypeError("send must be a function");
  }

  return {
    async submit(rawIntent) {
      const intent = validateIntent(rawIntent);
      const prompt = [
        "[Threadlight Canvas intent]",
        JSON.stringify(intent, null, 2),
        "",
        "Treat this as a user-visible request from the active Canvas.",
        "Explain the proposed next action in chat, resolve it to the correct",
        "Threadlight skill or command, and preserve all normal permission and",
        "confirmation gates before any file, process, or Azure side effect.",
      ].join("\n");
      await send({ prompt });
      return { accepted: true, intent };
    },
  };
}
```

- [ ] **Step 4: Run the intent tests**

Run:

```bash
node --test tests/canvas/intents.test.mjs
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit the intent boundary**

```bash
git add .github/extensions/threadlight-lifecycle/lib/intents.mjs tests/canvas/intents.test.mjs
git commit -m "feat: add safe Canvas intent broker" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: Build the tokenized loopback server

**Files:**
- Create: `.github/extensions/threadlight-lifecycle/lib/http-server.mjs`
- Create: `.github/extensions/threadlight-lifecycle/web/index.html`
- Create: `.github/extensions/threadlight-lifecycle/web/app.js`
- Create: `.github/extensions/threadlight-lifecycle/web/styles.css`
- Create: `tests/canvas/http-server.test.mjs`

- [ ] **Step 1: Write failing server security and round-trip tests**

```javascript
// tests/canvas/http-server.test.mjs
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createLoopbackServer } from "../../.github/extensions/threadlight-lifecycle/lib/http-server.mjs";

test("server is loopback-only, tokenized, CSP protected, and chat-only", async (t) => {
  const webRoot = await mkdtemp(path.join(os.tmpdir(), "tl-canvas-web-"));
  await writeFile(path.join(webRoot, "index.html"), "<h1>Threadlight</h1>");
  const intents = [];
  const server = await createLoopbackServer({
    webRoot,
    token: "test-token",
    getModel: async () => ({ phases: [] }),
    onIntent: async (intent) => intents.push(intent),
  });
  t.after(async () => {
    await server.close();
    await rm(webRoot, { recursive: true, force: true });
  });

  assert.match(server.url, /^http:\/\/127\.0\.0\.1:/);

  const denied = await fetch(`${server.origin}/api/model`);
  assert.equal(denied.status, 401);

  const page = await fetch(`${server.url}`);
  assert.equal(page.status, 200);
  assert.match(
    page.headers.get("content-security-policy"),
    /default-src 'self'/,
  );

  const accepted = await fetch(
    `${server.origin}/api/intent?token=test-token`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ type: "prepare_handoff" }),
    },
  );
  assert.equal(accepted.status, 202);
  assert.deepEqual(intents, [{ type: "prepare_handoff" }]);

  const traversal = await fetch(
    `${server.origin}/..%2F..%2Fplugin.json?token=test-token`,
  );
  assert.equal(traversal.status, 404);
});
```

- [ ] **Step 2: Run the server test and verify the expected failure**

Run:

```bash
node --test tests/canvas/http-server.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 3: Implement the loopback server**

Create `http-server.mjs` with this public contract:

```javascript
// .github/extensions/threadlight-lifecycle/lib/http-server.mjs
import { randomBytes } from "node:crypto";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import http from "node:http";
import path from "node:path";

const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'self'",
].join("; ");

const CONTENT_TYPES = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
]);

function sendJson(response, status, value) {
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
  });
  response.end(JSON.stringify(value));
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 64 * 1024) {
      throw new RangeError("Request body exceeds 64 KiB");
    }
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function resolveStaticPath(webRoot, pathname) {
  const relative = pathname === "/" ? "index.html" : pathname.slice(1);
  if (!/^[a-zA-Z0-9._/-]+$/.test(relative)) {
    return null;
  }
  const resolved = path.resolve(webRoot, relative);
  const root = `${path.resolve(webRoot)}${path.sep}`;
  return resolved.startsWith(root) ? resolved : null;
}

export async function createLoopbackServer({
  webRoot,
  getModel,
  onIntent,
  token = randomBytes(24).toString("base64url"),
}) {
  if (typeof getModel !== "function" || typeof onIntent !== "function") {
    throw new TypeError("getModel and onIntent must be functions");
  }

  const clients = new Set();
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    const authorized = url.searchParams.get("token") === token;
    if (url.pathname.startsWith("/api/") && !authorized) {
      sendJson(response, 401, { error: "unauthorized" });
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/model") {
      sendJson(response, 200, await getModel());
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/events") {
      response.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-store",
        connection: "keep-alive",
        "content-security-policy": CSP,
      });
      response.write("event: ready\ndata: {}\n\n");
      clients.add(response);
      request.on("close", () => clients.delete(response));
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/intent") {
      try {
        const intent = await readJsonBody(request);
        await onIntent(intent);
        sendJson(response, 202, { accepted: true });
      } catch (error) {
        sendJson(response, 400, { error: error.message });
      }
      return;
    }

    if (request.method !== "GET") {
      sendJson(response, 405, { error: "method_not_allowed" });
      return;
    }

    const filePath = resolveStaticPath(webRoot, url.pathname);
    if (!filePath) {
      sendJson(response, 404, { error: "not_found" });
      return;
    }
    try {
      const info = await stat(filePath);
      if (!info.isFile()) {
        sendJson(response, 404, { error: "not_found" });
        return;
      }
      response.writeHead(200, {
        "content-type":
          CONTENT_TYPES.get(path.extname(filePath)) ??
          "application/octet-stream",
        "cache-control": "no-store",
        "content-security-policy": CSP,
        "x-content-type-options": "nosniff",
      });
      createReadStream(filePath).pipe(response);
    } catch (error) {
      if (error.code === "ENOENT") {
        sendJson(response, 404, { error: "not_found" });
        return;
      }
      sendJson(response, 500, { error: "static_read_failed" });
    }
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("Loopback server did not expose a TCP address");
  }
  const origin = `http://127.0.0.1:${address.port}`;

  return {
    origin,
    token,
    url: `${origin}/?token=${encodeURIComponent(token)}`,
    publish(event = "workspace-changed") {
      const frame = `event: ${event}\ndata: {}\n\n`;
      for (const client of clients) {
        client.write(frame);
      }
    },
    async close() {
      for (const client of clients) {
        client.end();
      }
      clients.clear();
      await new Promise((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    },
  };
}
```

- [ ] **Step 4: Add the minimal spike web client**

Use a dependency-free page whose button performs only the typed intent POST:

```html
<!-- .github/extensions/threadlight-lifecycle/web/index.html -->
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Threadlight Lifecycle</title>
    <link rel="stylesheet" href="/styles.css">
  </head>
  <body>
    <main>
      <p class="eyebrow">THREADLIGHT LIFECYCLE</p>
      <h1>Start and steer a pilot</h1>
      <p id="status" role="status">Loading workspace...</p>
      <button id="prepare-handoff" type="button">Prepare handoff</button>
    </main>
    <script type="module" src="/app.js"></script>
  </body>
</html>
```

```javascript
// .github/extensions/threadlight-lifecycle/web/app.js
const params = new URLSearchParams(location.search);
const token = params.get("token");
const endpoint = (path) => `${path}?token=${encodeURIComponent(token ?? "")}`;

async function refresh() {
  const response = await fetch(endpoint("/api/model"));
  if (!response.ok) {
    throw new Error(`Model request failed: ${response.status}`);
  }
  const model = await response.json();
  document.querySelector("#status").textContent =
    model.summary ?? "Workspace ready";
}

document.querySelector("#prepare-handoff").addEventListener("click", async () => {
  const response = await fetch(endpoint("/api/intent"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ type: "prepare_handoff" }),
  });
  if (!response.ok) {
    throw new Error(`Intent request failed: ${response.status}`);
  }
  document.querySelector("#status").textContent =
    "Intent sent to chat for confirmation.";
});

const events = new EventSource(endpoint("/api/events"));
events.addEventListener("workspace-changed", refresh);
refresh().catch((error) => {
  document.querySelector("#status").textContent = error.message;
});
```

```css
/* .github/extensions/threadlight-lifecycle/web/styles.css */
:root {
  color-scheme: light dark;
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
  background: Canvas;
  color: CanvasText;
}

body {
  margin: 0;
  min-width: 320px;
}

main {
  display: grid;
  gap: 1rem;
  padding: 1.5rem;
}

.eyebrow {
  color: #8250df;
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.12em;
}

button {
  width: fit-content;
  border: 0;
  border-radius: 0.5rem;
  padding: 0.7rem 1rem;
  background: #8250df;
  color: #fff;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
```

- [ ] **Step 5: Run the server test**

Run:

```bash
node --test tests/canvas/http-server.test.mjs
```

Expected: PASS.

- [ ] **Step 6: Commit the secure server**

```bash
git add .github/extensions/threadlight-lifecycle/lib/http-server.mjs .github/extensions/threadlight-lifecycle/web tests/canvas/http-server.test.mjs
git commit -m "feat: serve Canvas over a secure loopback boundary" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: Wire the Canvas provider and complete the spike

**Files:**
- Create: `.github/extensions/threadlight-lifecycle/lib/canvas-provider.mjs`
- Modify: `.github/extensions/threadlight-lifecycle/extension.mjs`
- Create: `tests/canvas/canvas-provider.test.mjs`

- [ ] **Step 1: Write failing provider lifecycle tests**

```javascript
// tests/canvas/canvas-provider.test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import { createLifecycleCanvas } from "../../.github/extensions/threadlight-lifecycle/lib/canvas-provider.mjs";

function createHarness() {
  const servers = [];
  const sent = [];
  const canvas = createLifecycleCanvas({
    createCanvas: (options) => options,
    webRoot: "/tmp/threadlight-web",
    getSession: () => ({
      send: async (message) => sent.push(message),
    }),
    projectWorkspace: async (workspace) => ({
      summary: `Projected ${workspace}`,
      phases: [],
    }),
    createServer: async (options) => {
      const server = {
        url: "http://127.0.0.1:4321/?token=test",
        published: 0,
        closed: false,
        publish() {
          this.published += 1;
        },
        async close() {
          this.closed = true;
        },
        options,
      };
      servers.push(server);
      return server;
    },
  });
  return { canvas, servers, sent };
}

test("open returns no URL when the host cannot render canvases", async () => {
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

test("provider exposes only refresh and chat-intent actions", () => {
  const { canvas } = createHarness();
  assert.deepEqual(
    canvas.actions.map((action) => action.name),
    ["refresh", "prepare_intent"],
  );
});

test("open projects the workspace and close releases the server", async () => {
  const { canvas, servers } = createHarness();
  const result = await canvas.open({
    instanceId: "pilot-1",
    host: { capabilities: { canvases: true } },
    session: { workingDirectory: "/tmp/pilot" },
  });

  assert.equal(result.url, "http://127.0.0.1:4321/?token=test");
  assert.equal(result.title, "Threadlight Lifecycle");
  assert.equal(servers[0].options.getModel instanceof Function, true);

  await canvas.onClose({ instanceId: "pilot-1" });
  assert.equal(servers[0].closed, true);
});

test("intent requests send a visible chat message", async () => {
  const { canvas, servers, sent } = createHarness();
  await canvas.open({
    instanceId: "pilot-2",
    host: { capabilities: { canvases: true } },
    session: { workingDirectory: "/tmp/pilot" },
  });

  await servers[0].options.onIntent({ type: "prepare_handoff" });
  assert.equal(sent.length, 1);
  assert.match(sent[0].prompt, /^\[Threadlight Canvas intent\]/);
});
```

- [ ] **Step 2: Run the provider test and verify the expected failure**

Run:

```bash
node --test tests/canvas/canvas-provider.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 3: Implement provider lifecycle without operational side effects**

```javascript
// .github/extensions/threadlight-lifecycle/lib/canvas-provider.mjs
import { createIntentBroker } from "./intents.mjs";
import { createLoopbackServer } from "./http-server.mjs";

const OPEN_INPUT_SCHEMA = {
  type: "object",
  properties: {
    phase: {
      type: "string",
      enum: [
        "design",
        "build-deploy",
        "discover",
        "protect-govern",
        "improve",
        "handoff",
      ],
    },
  },
  additionalProperties: false,
};

export function createLifecycleCanvas({
  createCanvas,
  webRoot,
  getSession,
  projectWorkspace,
  createServer = createLoopbackServer,
}) {
  const instances = new Map();

  return createCanvas({
    id: "threadlight-lifecycle",
    displayName: "Threadlight Lifecycle",
    description:
      "Start and inspect a Threadlight pilot by outcome without needing skill names.",
    inputSchema: OPEN_INPUT_SCHEMA,
    actions: [
      {
        name: "refresh",
        description: "Refresh lifecycle state from canonical workspace artifacts.",
        inputSchema: {
          type: "object",
          properties: {},
          additionalProperties: false,
        },
        handler: async ({ instanceId }) => {
          const instance = instances.get(instanceId);
          if (!instance) {
            throw new Error(`Unknown Canvas instance: ${instanceId}`);
          }
          instance.model = await projectWorkspace(instance.workspace);
          instance.server.publish();
          return { status: instance.model.summary };
        },
      },
      {
        name: "prepare_intent",
        description:
          "Send a validated, side-effect-free lifecycle intent to the active chat.",
        inputSchema: {
          type: "object",
          required: ["intent"],
          properties: { intent: { type: "object" } },
          additionalProperties: false,
        },
        handler: async ({ instanceId, input }) => {
          const instance = instances.get(instanceId);
          if (!instance) {
            throw new Error(`Unknown Canvas instance: ${instanceId}`);
          }
          return instance.broker.submit(input?.intent);
        },
      },
    ],
    open: async (context) => {
      if (context.host?.capabilities?.canvases === false) {
        return {
          title: "Threadlight Lifecycle",
          status: "Canvas rendering unavailable",
        };
      }
      const workspace = context.session?.workingDirectory;
      if (!workspace) {
        throw new Error("Canvas session has no working directory");
      }

      const session = getSession();
      if (!session) {
        throw new Error("Extension session is not attached");
      }
      const broker = createIntentBroker({
        send: (message) => session.send(message),
      });
      const instance = {
        workspace,
        broker,
        model: await projectWorkspace(workspace),
        server: null,
      };
      instance.server = await createServer({
        webRoot,
        getModel: async () => instance.model,
        onIntent: (intent) => broker.submit(intent),
      });
      instances.set(context.instanceId, instance);

      return {
        url: instance.server.url,
        title: "Threadlight Lifecycle",
        status: instance.model.summary,
      };
    },
    onClose: async ({ instanceId }) => {
      const instance = instances.get(instanceId);
      if (!instance) {
        return;
      }
      instances.delete(instanceId);
      await instance.server.close();
    },
  });
}
```

- [ ] **Step 4: Reduce `extension.mjs` to SDK composition**

Replace scaffold demo behavior with this composition. Keep the scaffold's
shutdown handling if it contains additional SDK-required cleanup:

```javascript
// .github/extensions/threadlight-lifecycle/extension.mjs
import {
  createCanvas,
  joinSession,
} from "@github/copilot-sdk/extension";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createLifecycleCanvas } from "./lib/canvas-provider.mjs";

const extensionRoot = path.dirname(fileURLToPath(import.meta.url));
let session;

const canvas = createLifecycleCanvas({
  createCanvas,
  webRoot: path.join(extensionRoot, "web"),
  getSession: () => session,
  projectWorkspace: async (workspace) => ({
    summary: `Ready: ${path.basename(workspace)}`,
    phases: [],
  }),
});

session = await joinSession({ canvases: [canvas] });
```

- [ ] **Step 5: Run the complete spike test set**

Run:

```bash
node --test \
  tests/canvas/plugin-contract.test.mjs \
  tests/canvas/intents.test.mjs \
  tests/canvas/http-server.test.mjs \
  tests/canvas/canvas-provider.test.mjs
```

Expected: all tests PASS.

- [ ] **Step 6: Reload and inspect the extension in the GitHub Copilot App**

First verify the project-scoped development copy:

```text
extensions_reload({})
extensions_manage({ operation: "inspect", name: "threadlight-lifecycle" })
```

Then open the canvas with:

```text
list_canvas_capabilities({ canvasId: "threadlight-lifecycle" })
open_canvas({
  canvasId: "threadlight-lifecycle",
  instanceId: "threadlight-spike",
  input: {}
})
```

Then verify actual plugin packaging rather than relying on project discovery:

```bash
copilot plugin install "$(pwd)"
```

Open a fresh GitHub Copilot App session in a clean repository that does not
contain `.github/extensions/threadlight-lifecycle`, enable experimental
extensions, and run:

```text
extensions_manage({ operation: "list" })
extensions_manage({ operation: "inspect", name: "threadlight-lifecycle" })
```

Expected:

- the clean-repository extension source is
  `plugin:threadlight-skills:threadlight-lifecycle`, proving the installed
  plugin contributed it;
- extension status is `running`;
- the Canvas opens in the right panel;
- its URL uses `127.0.0.1`;
- clicking **Prepare handoff** adds a visible intent to chat;
- re-opening `threadlight-spike` focuses the existing panel;
- closing the panel releases the server.

- [ ] **Step 7: Commit the provider spike**

```bash
git add .github/extensions/threadlight-lifecycle tests/canvas/canvas-provider.test.mjs
git commit -m "feat: prove Threadlight Canvas provider round trip" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

- [ ] **Step 8: Apply the mandatory go/no-go gate**

Proceed to Part B only when all Task 4 Step 6 expectations hold in the minimum
supported GitHub Copilot App version. If plugin discovery, right-panel rendering,
or click-to-chat is unreliable, stop and record the exact failed condition in
the design spec rather than building the full cockpit on an unstable surface.

---

## Part B - Version 1 Lifecycle Cockpit

### Task 5: Define the exact lifecycle registry

**Files:**
- Create: `.github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs`
- Create: `tests/canvas/lifecycle-registry.test.mjs`

- [ ] **Step 1: Write the failing 17-skill contract test**

```javascript
// tests/canvas/lifecycle-registry.test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import {
  LIFECYCLE_PHASES,
  SKILL_REGISTRY,
} from "../../.github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs";

const expected = new Set([
  "threadlight-design",
  "threadlight-demo-data-factory",
  "threadlight-event-triggers",
  "threadlight-hitl-patterns",
  "threadlight-workspace-ui",
  "threadlight-auto",
  "threadlight-local-test",
  "threadlight-deploy",
  "threadlight-safe-check",
  "threadlight-consumption-iq",
  "threadlight-evals",
  "threadlight-redteam",
  "threadlight-govern",
  "threadlight-router-bench",
  "threadlight-production-ready",
  "threadlight-cicd",
  "threadlight-customize",
]);

test("registry maps all 17 skills exactly once", () => {
  const ids = SKILL_REGISTRY.map((skill) => skill.id);
  assert.equal(ids.length, 17);
  assert.equal(new Set(ids).size, 17);
  assert.deepEqual(new Set(ids), expected);
});

test("registry uses the six approved outcome phases", () => {
  assert.deepEqual(
    LIFECYCLE_PHASES.map((phase) => phase.id),
    [
      "design",
      "build-deploy",
      "discover",
      "protect-govern",
      "improve",
      "handoff",
    ],
  );
  const phaseIds = new Set(LIFECYCLE_PHASES.map((phase) => phase.id));
  for (const skill of SKILL_REGISTRY) {
    assert.equal(phaseIds.has(skill.phase), true, skill.id);
  }
});

test("operational actions are intents, not commands", () => {
  for (const skill of SKILL_REGISTRY) {
    assert.equal("command" in skill, false, skill.id);
    assert.equal("handler" in skill, false, skill.id);
  }
});
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run:

```bash
node --test tests/canvas/lifecycle-registry.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 3: Implement the phase and skill registry**

```javascript
// .github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs
export const LIFECYCLE_PHASES = Object.freeze([
  { id: "design", label: "Design", view: "design" },
  { id: "build-deploy", label: "Build / Deploy", view: "run" },
  { id: "discover", label: "Discover", view: "assurance" },
  { id: "protect-govern", label: "Protect / Govern", view: "assurance" },
  { id: "improve", label: "Improve", view: "generic" },
  { id: "handoff", label: "Handoff", view: "handoff" },
]);

const artifactGroup = (...paths) => Object.freeze(paths);

const skill = (id, phase, label, requiredArtifactGroups, options = {}) =>
  Object.freeze({
    id,
    phase,
    label,
    requiredArtifactGroups: Object.freeze(requiredArtifactGroups),
    optionalArtifacts: Object.freeze(options.optionalArtifacts ?? []),
    prerequisiteSkills: Object.freeze(options.prerequisiteSkills ?? []),
    role: options.role ?? "stage",
    applicability: options.applicability ?? "always",
    completionMode: options.completionMode ?? "artifact-complete",
    completionArtifact: options.completionArtifact ?? null,
    freshnessHours: options.freshnessHours ?? null,
    affectsPhaseStatus: options.affectsPhaseStatus ?? true,
    nextIntent: Object.freeze(
      options.nextIntent ?? { type: "resume_phase", phase },
    ),
  });

export const SKILL_REGISTRY = Object.freeze([
  skill("threadlight-design", "design", "Define the pilot", [
    artifactGroup("specs/SPEC.md"),
    artifactGroup("specs/manifest.json"),
  ]),
  skill("threadlight-demo-data-factory", "design", "Create credible demo data", [
    artifactGroup("specs/sample-data"),
  ], {
    prerequisiteSkills: ["threadlight-design"],
    applicability: "mock-systems",
  }),
  skill("threadlight-event-triggers", "design", "Define event entry points", [
    artifactGroup("src/triggers"),
  ], {
    prerequisiteSkills: ["threadlight-design"],
    applicability: "event-trigger",
  }),
  skill("threadlight-hitl-patterns", "design", "Design human decisions", [
    artifactGroup("src/bot"),
  ], {
    prerequisiteSkills: ["threadlight-design"],
    applicability: "human-approval",
  }),
  skill("threadlight-workspace-ui", "design", "Shape the operator workspace", [
    artifactGroup("src/workspace"),
  ], {
    prerequisiteSkills: ["threadlight-design"],
    applicability: "workspace-ui",
  }),
  skill("threadlight-auto", "build-deploy", "Drive the pilot lifecycle", [
    artifactGroup(".threadlight/auto-state.json"),
  ], {
    role: "orchestrator",
    completionMode: "artifact-running",
    affectsPhaseStatus: false,
    prerequisiteSkills: ["threadlight-design"],
  }),
  skill("threadlight-local-test", "build-deploy", "Run the local inner loop", [], {
    completionMode: "manual",
    affectsPhaseStatus: false,
    prerequisiteSkills: ["threadlight-design"],
  }),
  skill("threadlight-deploy", "build-deploy", "Deploy to the sandbox", [
    artifactGroup("azure.yaml"),
    artifactGroup("infra/main.bicep"),
  ], {
    completionArtifact: "docs/safe-check-post.md",
    prerequisiteSkills: ["threadlight-design"],
  }),
  skill("threadlight-safe-check", "build-deploy", "Verify the deployment", [
    artifactGroup("docs/safe-check-post.md"),
  ], {
    freshnessHours: 24,
    prerequisiteSkills: ["threadlight-deploy"],
  }),
  skill("threadlight-consumption-iq", "discover", "Project consumption", [
    artifactGroup("specs/cost-manifest.json"),
  ], {
    optionalArtifacts: ["docs/cost-projection.md"],
    prerequisiteSkills: ["threadlight-safe-check"],
  }),
  skill("threadlight-evals", "discover", "Measure quality", [
    artifactGroup("specs/evals-manifest.json"),
  ], {
    freshnessHours: 24,
    prerequisiteSkills: ["threadlight-safe-check"],
  }),
  skill("threadlight-redteam", "discover", "Probe adversarial safety", [
    artifactGroup("specs/redteam-manifest.json"),
  ], {
    optionalArtifacts: ["docs/redteam-report.md"],
    freshnessHours: 24,
    prerequisiteSkills: ["threadlight-safe-check"],
  }),
  skill("threadlight-govern", "protect-govern", "Verify runtime governance", [
    artifactGroup("specs/govern-manifest.json"),
  ], {
    freshnessHours: 24,
    prerequisiteSkills: ["threadlight-safe-check"],
  }),
  skill("threadlight-router-bench", "improve", "Learn from completed runs", [
    artifactGroup("router-bench-out"),
  ], {
    prerequisiteSkills: ["threadlight-evals"],
  }),
  skill("threadlight-production-ready", "handoff", "Assess production readiness", [
    artifactGroup("tests/production-readiness-manifest.json"),
  ], {
    optionalArtifacts: ["docs/production-readiness-report.md"],
    prerequisiteSkills: ["threadlight-safe-check"],
  }),
  skill("threadlight-cicd", "handoff", "Prepare the production pipeline", [
    artifactGroup(".github/workflows/azd-deploy-prod.yml", "azure-pipelines.yml"),
  ], {
    prerequisiteSkills: ["threadlight-production-ready"],
  }),
  skill("threadlight-customize", "handoff", "Prepare customer onboarding", [
    artifactGroup("docs/threadlight-customize/customer-profile.md"),
  ], {
    prerequisiteSkills: ["threadlight-production-ready"],
  }),
]);
```

- [ ] **Step 4: Run the registry tests**

Run:

```bash
node --test tests/canvas/lifecycle-registry.test.mjs
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit the lifecycle contract**

```bash
git add .github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs tests/canvas/lifecycle-registry.test.mjs
git commit -m "feat: define the Threadlight lifecycle registry" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 6: Enforce the artifact read allowlist

**Files:**
- Create: `.github/extensions/threadlight-lifecycle/lib/artifact-reader.mjs`
- Create: `tests/canvas/artifact-reader.test.mjs`

- [ ] **Step 1: Write failing allowlist and parser tests**

```javascript
// tests/canvas/artifact-reader.test.mjs
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  ArtifactAccessError,
  ArtifactParseError,
  createArtifactReader,
} from "../../.github/extensions/threadlight-lifecycle/lib/artifact-reader.mjs";
import { SKILL_REGISTRY } from "../../.github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs";

test("reader allows canonical artifacts and rejects secrets and traversal", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "tl-artifacts-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "specs"), { recursive: true });
  await writeFile(path.join(root, "specs", "manifest.json"), '{"name":"pilot"}');
  await writeFile(path.join(root, ".env"), "SECRET=value");

  const reader = await createArtifactReader(root);
  assert.deepEqual(await reader.readJson("specs/manifest.json"), {
    name: "pilot",
  });
  await assert.rejects(() => reader.readText(".env"), ArtifactAccessError);
  await assert.rejects(
    () => reader.readText("../outside.txt"),
    ArtifactAccessError,
  );
});

test("reader rejects allowlisted symlinks that escape the workspace", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "tl-artifacts-"));
  const outside = await mkdtemp(path.join(os.tmpdir(), "tl-outside-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  t.after(() => rm(outside, { recursive: true, force: true }));
  await writeFile(path.join(outside, "manifest.json"), '{"secret":true}');
  await mkdir(path.join(root, "specs"), { recursive: true });
  await symlink(
    path.join(outside, "manifest.json"),
    path.join(root, "specs", "manifest.json"),
  );

  const reader = await createArtifactReader(root);
  await assert.rejects(
    () => reader.readJson("specs/manifest.json"),
    ArtifactAccessError,
  );
});

test("malformed JSON remains an explicit parse error", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "tl-artifacts-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "specs"), { recursive: true });
  await writeFile(path.join(root, "specs", "manifest.json"), "{");

  const reader = await createArtifactReader(root);
  await assert.rejects(
    () => reader.readJson("specs/manifest.json"),
    ArtifactParseError,
  );
});

test("every registry probe is inside the artifact allowlist", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "tl-artifacts-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const reader = await createArtifactReader(root);
  const paths = SKILL_REGISTRY.flatMap((skill) => [
    ...skill.requiredArtifactGroups.flat(),
    ...skill.optionalArtifacts,
    ...(skill.completionArtifact ? [skill.completionArtifact] : []),
  ]);

  for (const relativePath of paths) {
    await assert.doesNotReject(() => reader.exists(relativePath));
  }
});
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run:

```bash
node --test tests/canvas/artifact-reader.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 3: Implement exact-file and exact-root allowlisting**

```javascript
// .github/extensions/threadlight-lifecycle/lib/artifact-reader.mjs
import {
  lstat,
  readFile,
  realpath,
  stat,
} from "node:fs/promises";
import path from "node:path";

const ALLOWED_FILES = new Set([
  "AGENTS.md",
  "azure.yaml",
  "infra/main.bicep",
  "specs/SPEC.md",
  "specs/foundation.md",
  "specs/manifest.json",
  "specs/cost-manifest.json",
  "specs/evals-manifest.json",
  "specs/redteam-manifest.json",
  "specs/govern-manifest.json",
  "tests/production-readiness-manifest.json",
  ".threadlight/auto-state.json",
  ".threadlight/preflight-passed.json",
  "azure-pipelines.yml",
  "docs/safe-check-post.md",
  "docs/cost-projection.md",
  "docs/redteam-report.md",
  "docs/production-readiness-report.md",
]);

const ALLOWED_ROOTS = new Set([
  "specs/sample-data",
  "src/agent",
  "src/bot",
  "src/triggers",
  "src/workspace",
  ".github/workflows",
  "docs/threadlight-customize",
  "router-bench-out",
]);

export class ArtifactAccessError extends Error {
  constructor(relativePath, message) {
    super(`${relativePath}: ${message}`);
    this.name = "ArtifactAccessError";
    this.relativePath = relativePath;
  }
}

export class ArtifactParseError extends Error {
  constructor(relativePath, cause) {
    super(`${relativePath}: invalid JSON: ${cause.message}`);
    this.name = "ArtifactParseError";
    this.relativePath = relativePath;
    this.cause = cause;
  }
}

function normalize(relativePath) {
  if (typeof relativePath !== "string" || path.isAbsolute(relativePath)) {
    throw new ArtifactAccessError(String(relativePath), "path must be relative");
  }
  const normalized = path.posix.normalize(relativePath.replaceAll("\\", "/"));
  if (normalized === ".." || normalized.startsWith("../")) {
    throw new ArtifactAccessError(relativePath, "path escapes workspace");
  }
  const allowed =
    ALLOWED_FILES.has(normalized) ||
    [...ALLOWED_ROOTS].some(
      (root) => normalized === root || normalized.startsWith(`${root}/`),
    );
  if (!allowed) {
    throw new ArtifactAccessError(relativePath, "path is not allowlisted");
  }
  return normalized;
}

export async function createArtifactReader(workspace) {
  const workspaceReal = await realpath(workspace);
  const rootPrefix = `${workspaceReal}${path.sep}`;

  async function resolveAllowed(relativePath) {
    const normalized = normalize(relativePath);
    const candidate = path.resolve(workspaceReal, normalized);
    let candidateReal;
    try {
      candidateReal = await realpath(candidate);
    } catch (error) {
      if (error.code === "ENOENT") {
        return { normalized, candidate, exists: false };
      }
      throw error;
    }
    if (
      candidateReal !== workspaceReal &&
      !candidateReal.startsWith(rootPrefix)
    ) {
      throw new ArtifactAccessError(
        relativePath,
        "resolved path escapes workspace",
      );
    }
    return { normalized, candidate: candidateReal, exists: true };
  }

  return Object.freeze({
    async exists(relativePath) {
      const resolved = await resolveAllowed(relativePath);
      if (!resolved.exists) {
        return false;
      }
      const info = await stat(resolved.candidate);
      return info.isFile() || info.isDirectory();
    },
    async metadata(relativePath) {
      const resolved = await resolveAllowed(relativePath);
      if (!resolved.exists) {
        return null;
      }
      const info = await lstat(resolved.candidate);
      return {
        relativePath: resolved.normalized,
        kind: info.isDirectory() ? "directory" : "file",
        modifiedAt: info.mtime.toISOString(),
        size: info.size,
      };
    },
    async readText(relativePath) {
      const resolved = await resolveAllowed(relativePath);
      if (!resolved.exists) {
        return null;
      }
      return readFile(resolved.candidate, "utf8");
    },
    async readJson(relativePath) {
      const text = await this.readText(relativePath);
      if (text === null) {
        return null;
      }
      try {
        return JSON.parse(text);
      } catch (error) {
        throw new ArtifactParseError(relativePath, error);
      }
    },
  });
}
```

- [ ] **Step 4: Run the artifact reader tests**

Run:

```bash
node --test tests/canvas/artifact-reader.test.mjs
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit the artifact boundary**

```bash
git add .github/extensions/threadlight-lifecycle/lib/artifact-reader.mjs tests/canvas/artifact-reader.test.mjs
git commit -m "feat: enforce Canvas artifact allowlist" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 7: Project canonical artifacts into lifecycle state

**Files:**
- Create: `.github/extensions/threadlight-lifecycle/lib/projector.mjs`
- Create: `tests/canvas/fixtures.mjs`
- Create: `tests/canvas/projector.test.mjs`

- [ ] **Step 1: Add reusable workspace fixture builders**

```javascript
// tests/canvas/fixtures.mjs
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

async function write(root, relativePath, value) {
  const target = path.join(root, relativePath);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(
    target,
    typeof value === "string" ? value : JSON.stringify(value, null, 2),
  );
}

export async function createWorkspaceFixture(name) {
  const root = await mkdtemp(path.join(os.tmpdir(), `tl-${name}-`));
  if (name === "empty") {
    return root;
  }

  await write(root, "specs/SPEC.md", "# Pilot\n\nNo unresolved markers.");
  await write(root, "specs/manifest.json", {
    name: "fixture-pilot",
    traits: ["human-approval"],
    mock_systems: ["orders"],
    deployment_manifest: {
      module_selectors: {
        "workspace-ui": "yes",
        "aca-job": "no",
        "event-grid": "no",
        "service-bus": "no",
      },
      scheduled_jobs: [],
    },
  });
  if (name === "design-only") {
    return root;
  }

  await write(root, "azure.yaml", "name: fixture-pilot\n");
  await write(root, "infra/main.bicep", "targetScope = 'subscription'\n");
  await write(root, ".threadlight/auto-state.json", {
    design: { artifact_hash: "fixture" },
  });
  if (name === "deploy-blocked") {
    return root;
  }

  await write(root, "docs/safe-check-post.md", "# Safe check\n\nPASS");
  await write(root, "specs/cost-manifest.json", {
    generated_at: "2026-08-06T08:00:00Z",
    verdict: "complete",
  });
  await write(root, "specs/evals-manifest.json", {
    captured_at:
      name === "stale-assurance"
        ? "2026-07-01T08:00:00Z"
        : "2026-08-06T08:00:00Z",
    verdict: "comprehensive",
    must_fix: [],
  });
  if (["partial-assurance", "stale-assurance"].includes(name)) {
    return root;
  }

  await write(root, "specs/sample-data/orders.json", []);
  await write(root, "src/bot/index.js", "export {};\n");
  await write(root, "src/workspace/index.html", "<main>Workspace</main>\n");
  await write(root, "specs/redteam-manifest.json", {
    captured_at: "2026-08-06T08:00:00Z",
    verdict: "pass",
    must_fix: [],
  });
  await write(root, "specs/govern-manifest.json", {
    captured_at: "2026-08-06T08:00:00Z",
    verdict: "comprehensive",
    must_fix: [],
  });
  await write(root, "tests/production-readiness-manifest.json", {
    checked_at: "2026-08-06T08:00:00Z",
    go_live_recommendation: "ready",
    would_fail_hard_gate: false,
  });
  await write(root, "router-bench-out/learnings-1.md", "# Learnings");
  await write(
    root,
    ".github/workflows/azd-deploy-prod.yml",
    "name: deploy\n",
  );
  await write(
    root,
    "docs/threadlight-customize/customer-profile.md",
    "# Customer profile\n",
  );
  return root;
}
```

- [ ] **Step 2: Write failing projection tests for honest states**

```javascript
// tests/canvas/projector.test.mjs
import assert from "node:assert/strict";
import { rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { projectWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/projector.mjs";
import { createWorkspaceFixture } from "./fixtures.mjs";

async function withFixture(t, name) {
  const root = await createWorkspaceFixture(name);
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

const NOW = new Date("2026-08-06T09:00:00Z");

test("empty workspace is ready to start design", async (t) => {
  const model = await projectWorkspace(
    await withFixture(t, "empty"),
    { now: NOW },
  );
  assert.equal(model.phases.find((p) => p.id === "design").status, "ready");
  assert.equal(model.primaryAction.type, "start_pilot");
});

test("design-only workspace marks conditional surfaces honestly", async (t) => {
  const model = await projectWorkspace(
    await withFixture(t, "design-only"),
    { now: NOW },
  );
  const design = model.phases.find((phase) => phase.id === "design");
  assert.equal(design.status, "running");
  assert.equal(
    design.skills.find((skill) => skill.id === "threadlight-event-triggers")
      .status,
    "not-applicable",
  );
  assert.equal(
    design.skills.find((skill) => skill.id === "threadlight-workspace-ui")
      .status,
    "ready",
  );
});

test("structural deploy output is not reported as deployed success", async (t) => {
  const model = await projectWorkspace(
    await withFixture(t, "deploy-blocked"),
    { now: NOW },
  );
  const run = model.phases.find((phase) => phase.id === "build-deploy");
  assert.notEqual(run.status, "complete");
  assert.match(run.blockers.join(" "), /verify the deployment/i);
});

test("partial assurance identifies missing red-team and governance evidence", async (t) => {
  const model = await projectWorkspace(
    await withFixture(t, "partial-assurance"),
    { now: NOW },
  );
  assert.equal(
    model.phases.find((phase) => phase.id === "discover").status,
    "running",
  );
  assert.equal(
    model.phases.find((phase) => phase.id === "protect-govern").status,
    "ready",
  );
});

test("freshness windows produce stale rather than success", async (t) => {
  const model = await projectWorkspace(
    await withFixture(t, "stale-assurance"),
    { now: NOW },
  );
  const evals = model.phases
    .find((phase) => phase.id === "discover")
    .skills.find((skill) => skill.id === "threadlight-evals");
  assert.equal(evals.status, "stale");
});

test("partial governance evidence is not reported as complete", async (t) => {
  const root = await withFixture(t, "complete-pilot");
  await writeFile(
    path.join(root, "specs/govern-manifest.json"),
    JSON.stringify({
      captured_at: "2026-08-06T08:00:00Z",
      verdict: "partial",
      must_fix: [],
      not_verified: ["middleware_wired_at_boundary"],
    }),
  );
  const model = await projectWorkspace(root, { now: NOW });
  const govern = model.phases
    .find((phase) => phase.id === "protect-govern")
    .skills.find((skill) => skill.id === "threadlight-govern");
  assert.equal(govern.status, "running");
});

test("complete fixture reaches handoff without false blockers", async (t) => {
  const model = await projectWorkspace(
    await withFixture(t, "complete-pilot"),
    { now: NOW },
  );
  const handoff = model.phases.find((phase) => phase.id === "handoff");
  assert.equal(
    handoff.skills.find(
      (skill) => skill.id === "threadlight-production-ready",
    ).status,
    "complete",
  );
  assert.equal(
    model.phases.every((phase) => phase.status === "complete"),
    true,
  );
  assert.equal(model.errors.length, 0);
});
```

- [ ] **Step 3: Run projection tests and verify the expected failure**

Run:

```bash
node --test tests/canvas/projector.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 4: Implement applicability, evidence, and phase aggregation**

Create `projector.mjs` with these exact public functions and status rules:

```javascript
// .github/extensions/threadlight-lifecycle/lib/projector.mjs
import { createArtifactReader } from "./artifact-reader.mjs";
import {
  LIFECYCLE_PHASES,
  SKILL_REGISTRY,
} from "./lifecycle-registry.mjs";

function applicability(skill, manifest, manifestInvalid) {
  if (skill.applicability === "always") {
    return true;
  }
  if (manifestInvalid) {
    return true;
  }
  const traits = new Set(manifest?.traits ?? []);
  const selectors =
    manifest?.deployment_manifest?.module_selectors ?? {};
  if (skill.applicability === "mock-systems") {
    return (manifest?.mock_systems ?? []).length > 0;
  }
  if (skill.applicability === "human-approval") {
    return traits.has("human-approval") || selectors["aca-bot"] === "yes";
  }
  if (skill.applicability === "workspace-ui") {
    return selectors["workspace-ui"] === "yes";
  }
  if (skill.applicability === "event-trigger") {
    return (
      selectors["aca-job"] === "yes" ||
      selectors["event-grid"] === "yes" ||
      selectors["service-bus"] === "yes" ||
      (manifest?.deployment_manifest?.scheduled_jobs ?? []).length > 0
    );
  }
  return true;
}

function phaseStatus(skills) {
  const applicable = skills.filter(
    (skill) =>
      skill.affectsPhaseStatus && skill.status !== "not-applicable",
  );
  if (applicable.length === 0) return "not-applicable";
  if (applicable.some((skill) => skill.status === "failed")) return "failed";
  if (applicable.some((skill) => skill.status === "blocked")) return "blocked";
  if (applicable.some((skill) => skill.status === "stale")) return "stale";
  if (applicable.every((skill) => skill.status === "complete")) return "complete";
  if (
    applicable.some((skill) => skill.status === "complete") &&
    applicable.some((skill) => skill.status !== "complete")
  ) return "running";
  if (applicable.some((skill) => skill.status === "running")) return "running";
  if (applicable.some((skill) => skill.status === "ready")) return "ready";
  return "not-started";
}

function evidenceStatus(document) {
  if (!document || typeof document !== "object") return null;
  if ((document.must_fix ?? []).length > 0) return "failed";
  if (document.would_fail_hard_gate === true) return "failed";
  if (document.go_live_recommendation === "not_ready") return "failed";
  if (
    document.verdict === "partial" ||
    (document.not_verified ?? []).length > 0
  ) return "running";
  return null;
}

function evidenceTimestamp(document, metadata) {
  const value =
    document?.captured_at ??
    document?.generated_at ??
    document?.checked_at ??
    metadata?.modifiedAt;
  const parsed = value ? new Date(value) : null;
  return parsed && !Number.isNaN(parsed.valueOf()) ? parsed : null;
}

function isStale(definition, document, metadata, now) {
  if (definition.freshnessHours === null) return false;
  const timestamp = evidenceTimestamp(document, metadata);
  if (!timestamp) return true;
  return now.valueOf() - timestamp.valueOf() >
    definition.freshnessHours * 60 * 60 * 1000;
}

export async function projectWorkspace(workspace, options = {}) {
  const reader =
    options.reader ?? (await createArtifactReader(workspace));
  const generatedAt = (options.now ?? new Date()).toISOString();
  const errors = [];
  let manifest = null;
  let manifestInvalid = false;
  try {
    manifest = await reader.readJson("specs/manifest.json");
  } catch (error) {
    manifestInvalid = true;
    errors.push({
      code: "artifact-parse-failed",
      path: error.relativePath,
      message: error.message,
    });
  }

  const projected = new Map();
  for (const definition of SKILL_REGISTRY) {
    if (!applicability(definition, manifest, manifestInvalid)) {
      projected.set(definition.id, {
        ...definition,
        status: "not-applicable",
        evidence: [],
        blockers: [],
      });
      continue;
    }

    const evidence = [];
    for (const group of definition.requiredArtifactGroups) {
      let match = null;
      for (const relativePath of group) {
        match = await reader.metadata(relativePath);
        if (match) break;
      }
      if (match) evidence.push(match);
    }
    const prerequisites = definition.prerequisiteSkills.map((id) =>
      projected.get(id),
    );
    const incompletePrerequisite = prerequisites.find(
      (item) =>
        item &&
        !["complete", "not-applicable"].includes(item.status),
    );

    let status = "not-started";
    if (definition.completionMode === "manual") {
      status = incompletePrerequisite ? "blocked" : "ready";
    } else if (
      evidence.length === definition.requiredArtifactGroups.length &&
      definition.requiredArtifactGroups.length > 0
    ) {
      status = "complete";
    } else if (evidence.length > 0) {
      status = "running";
    } else if (incompletePrerequisite) {
      status = "blocked";
    } else {
      status = "ready";
    }

    if (
      status === "complete" &&
      definition.completionMode === "artifact-running"
    ) {
      status = "running";
    }
    if (status === "complete" && definition.completionArtifact) {
      status = (await reader.exists(definition.completionArtifact))
        ? "complete"
        : "running";
    }

    const jsonArtifact = definition.requiredArtifactGroups
      .flat()
      .find((artifact) => artifact.endsWith(".json"));
    let jsonDocument = null;
    if (jsonArtifact && evidence.length > 0) {
      try {
        jsonDocument = await reader.readJson(jsonArtifact);
        status = evidenceStatus(jsonDocument) ?? status;
      } catch (error) {
        status = "failed";
        errors.push({
          code: "artifact-parse-failed",
          path: error.relativePath,
          message: error.message,
        });
      }
    }
    if (
      ["complete", "running"].includes(status) &&
      isStale(definition, jsonDocument, evidence[0], options.now ?? new Date())
    ) {
      status = "stale";
    }

    projected.set(definition.id, {
      ...definition,
      status,
      evidence,
      blockers: incompletePrerequisite
        ? [
            `${definition.label} is waiting for ${incompletePrerequisite.label}`,
          ]
        : [],
    });
  }

  const phases = LIFECYCLE_PHASES.map((phase) => {
    const skills = SKILL_REGISTRY.filter(
      (definition) => definition.phase === phase.id,
    ).map((definition) => projected.get(definition.id));
    const status = phaseStatus(skills);
    const blockers = skills
      .filter((skill) => skill.affectsPhaseStatus)
      .flatMap((skill) => [
        ...skill.blockers,
        ...(skill.status === "ready"
          ? [`Awaiting evidence for ${skill.label}`]
          : []),
      ]);
    return {
      ...phase,
      status,
      skills,
      blockers,
      evidence: skills.flatMap((skill) => skill.evidence),
      nextActions: skills
        .filter((skill) => ["ready", "blocked", "failed", "stale"].includes(skill.status))
        .map((skill) => skill.nextIntent),
    };
  });

  const design = phases.find((phase) => phase.id === "design");
  const firstActionable = phases.find((phase) =>
    ["ready", "blocked", "failed", "stale", "running"].includes(phase.status),
  );
  const primaryAction =
    design.status === "ready"
      ? { type: "start_pilot" }
      : firstActionable?.nextActions[0] ?? { type: "prepare_handoff" };

  return {
    workspace,
    generatedAt,
    summary:
      errors.length > 0
        ? `${errors.length} projection error(s)`
        : `${phases.filter((phase) => phase.status === "complete").length}/6 phases complete`,
    phases,
    primaryAction,
    errors,
  };
}
```

Do not call `threadlight-auto/references/orchestrator.py` in v1. Its current
deploy and cost probes read `.azure/**/.env`, which violates the approved Canvas
allowlist. The Canvas may consume a future redacted checker contract, but must
not bypass the security boundary now.

- [ ] **Step 5: Run projection and upstream contract tests**

Run:

```bash
node --test \
  tests/canvas/lifecycle-registry.test.mjs \
  tests/canvas/artifact-reader.test.mjs \
  tests/canvas/projector.test.mjs
```

Expected: all tests PASS.

- [ ] **Step 6: Commit the projector**

```bash
git add .github/extensions/threadlight-lifecycle/lib/projector.mjs tests/canvas/fixtures.mjs tests/canvas/projector.test.mjs
git commit -m "feat: project Threadlight lifecycle state" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 8: Refresh the Canvas from workspace changes

**Files:**
- Create: `.github/extensions/threadlight-lifecycle/lib/workspace-watcher.mjs`
- Create: `tests/canvas/workspace-watcher.test.mjs`
- Modify: `.github/extensions/threadlight-lifecycle/lib/canvas-provider.mjs`
- Modify: `.github/extensions/threadlight-lifecycle/extension.mjs`
- Modify: `tests/canvas/canvas-provider.test.mjs`

- [ ] **Step 1: Write the failing debounced watcher test**

```javascript
// tests/canvas/workspace-watcher.test.mjs
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { watchWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/workspace-watcher.mjs";

test("allowlisted workspace changes are debounced into one refresh", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "tl-watch-"));
  t.after(() => rm(root, { recursive: true, force: true }));

  let refreshes = 0;
  const watcher = await watchWorkspace(root, () => {
    refreshes += 1;
  }, { debounceMs: 40 });
  t.after(() => watcher.close());

  await mkdir(path.join(root, "specs"), { recursive: true });
  await writeFile(path.join(root, "specs", "SPEC.md"), "# One");
  await writeFile(path.join(root, "specs", "manifest.json"), "{}");
  await new Promise((resolve) => setTimeout(resolve, 120));

  assert.equal(refreshes, 1);

  await writeFile(path.join(root, "specs", "SPEC.md"), "# Two");
  await new Promise((resolve) => setTimeout(resolve, 120));
  assert.equal(refreshes, 2);
});
```

- [ ] **Step 2: Run the watcher test and verify the expected failure**

Run:

```bash
node --test tests/canvas/workspace-watcher.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 3: Implement focused, debounced directory watches**

```javascript
// .github/extensions/threadlight-lifecycle/lib/workspace-watcher.mjs
import { watch } from "node:fs";
import { access } from "node:fs/promises";
import path from "node:path";

const WATCH_ROOTS = [
  ".",
  "specs",
  ".threadlight",
  "docs",
  "infra",
  "src",
  ".github/workflows",
];

export async function watchWorkspace(
  workspace,
  onChange,
  {
    debounceMs = 150,
    onError = (error) => queueMicrotask(() => {
      throw error;
    }),
  } = {},
) {
  const watchers = new Map();
  let timer = null;

  async function attachRoots() {
    for (const relativePath of WATCH_ROOTS) {
      if (watchers.has(relativePath)) continue;
      const target = path.join(workspace, relativePath);
      try {
        await access(target);
      } catch (error) {
        if (error.code === "ENOENT") continue;
        throw error;
      }
      watchers.set(
        relativePath,
        watch(target, { persistent: false }, schedule),
      );
    }
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        await attachRoots();
        await onChange();
      } catch (error) {
        await onError(error);
      }
    }, debounceMs);
  }

  await attachRoots();

  return {
    close() {
      clearTimeout(timer);
      for (const watcher of watchers.values()) watcher.close();
      watchers.clear();
    },
  };
}
```

Treat these allowlisted file-change notifications as the v1 freshness signal.
Do not run `git`, traverse `.git`, or follow a worktree gitdir outside the
workspace. If the host later exposes a non-sensitive Git summary directly, add
it through a separate injected adapter and tests rather than weakening the
artifact reader.

- [ ] **Step 4: Integrate the real projector and watcher**

Update `canvas-provider.mjs` dependency parameters:

```javascript
export function createLifecycleCanvas({
  createCanvas,
  webRoot,
  getSession,
  projectWorkspace,
  watchWorkspace,
  createServer = createLoopbackServer,
}) {
```

After the server starts, attach the watcher and keep it on the instance:

```javascript
      instance.watcher = await watchWorkspace(
        workspace,
        async () => {
          instance.model = await projectWorkspace(workspace);
          instance.server.publish();
        },
        {
          onError: async (error) => {
            instance.model = {
              ...instance.model,
              summary: "Workspace refresh failed",
              errors: [
                ...instance.model.errors,
                {
                  code: "workspace-refresh-failed",
                  path: null,
                  message: error.message,
                },
              ],
            };
            instance.server.publish();
            await session.log(
              `Threadlight Canvas refresh failed: ${error.message}`,
              { level: "error" },
            );
          },
        },
      );
```

Close it before the server:

```javascript
      instance.watcher.close();
      await instance.server.close();
```

Update `extension.mjs` imports and composition:

```javascript
import { projectWorkspace } from "./lib/projector.mjs";
import { watchWorkspace } from "./lib/workspace-watcher.mjs";

const canvas = createLifecycleCanvas({
  createCanvas,
  webRoot: path.join(extensionRoot, "web"),
  getSession: () => session,
  projectWorkspace,
  watchWorkspace,
});
```

Extend the provider harness with a fake watcher and assert it closes. Do not
use a real filesystem watcher in provider unit tests.

- [ ] **Step 5: Run watcher, provider, and projector tests**

Run:

```bash
node --test \
  tests/canvas/workspace-watcher.test.mjs \
  tests/canvas/canvas-provider.test.mjs \
  tests/canvas/projector.test.mjs
```

Expected: all tests PASS and no open-handle warning.

- [ ] **Step 6: Commit live refresh**

```bash
git add .github/extensions/threadlight-lifecycle/lib .github/extensions/threadlight-lifecycle/extension.mjs tests/canvas
git commit -m "feat: refresh Canvas from workspace artifacts" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 9: Build the Lifecycle Cockpit UI

**Files:**
- Modify: `.github/extensions/threadlight-lifecycle/web/index.html`
- Modify: `.github/extensions/threadlight-lifecycle/web/app.js`
- Modify: `.github/extensions/threadlight-lifecycle/web/styles.css`
- Create: `tests/canvas/serve-canvas-fixture.mjs`
- Create: `tests/playwright/canvas.config.mjs`
- Create: `tests/playwright/canvas-tests/lifecycle-canvas.spec.mjs`
- Modify: `tests/playwright/package.json:7-10`

- [ ] **Step 1: Add the dedicated Playwright script and fixture server**

Add this package script:

```json
    "test:canvas": "playwright test --config canvas.config.mjs"
```

Create a fixed-token server for browser tests:

```javascript
// tests/canvas/serve-canvas-fixture.mjs
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createLoopbackServer } from "../../.github/extensions/threadlight-lifecycle/lib/http-server.mjs";
import { projectWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/projector.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../examples/returns-triage-governed",
);
const webRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../.github/extensions/threadlight-lifecycle/web",
);

const server = await createLoopbackServer({
  webRoot,
  token: "canvas-test",
  getModel: () => projectWorkspace(root),
  onIntent: async () => {},
});

console.log(server.origin);
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, async () => {
    await server.close();
    process.exit(0);
  });
}
```

Use a fixed test port by adding an optional `port = 0` argument to
`createLoopbackServer` and passing it to `server.listen(port, "127.0.0.1", ...)`.
Then set `port: 4187` in `serve-canvas-fixture.mjs`.

- [ ] **Step 2: Add the Canvas Playwright config**

```javascript
// tests/playwright/canvas.config.mjs
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./canvas-tests",
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:4187",
    viewport: { width: 430, height: 860 },
    trace: "on-first-retry",
  },
  webServer: {
    command: "node ../canvas/serve-canvas-fixture.mjs",
    url: "http://127.0.0.1:4187/?token=canvas-test",
    timeout: 15_000,
    reuseExistingServer: !process.env.CI,
  },
});
```

- [ ] **Step 3: Write failing outcome-oriented UI and accessibility tests**

```javascript
// tests/playwright/canvas-tests/lifecycle-canvas.spec.mjs
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/?token=canvas-test");
});

test("shows six outcome phases without requiring skill names", async ({ page }) => {
  for (const label of [
    "Design",
    "Build / Deploy",
    "Discover",
    "Protect / Govern",
    "Improve",
    "Handoff",
  ]) {
    await expect(page.getByRole("button", { name: label })).toBeVisible();
  }
  await expect(page.getByText("threadlight-design")).toHaveCount(0);
});

test("starts a pilot from a brief", async ({ page }) => {
  await page.getByRole("button", { name: "Start a pilot" }).click();
  await page.getByLabel("Pilot brief").fill(
    "Triage retail returns with supervisor approval.",
  );
  const response = page.waitForResponse(
    (item) => item.url().includes("/api/intent") && item.status() === 202,
  );
  await page.getByRole("button", { name: "Send to chat" }).click();
  await response;
  await expect(page.getByRole("status")).toContainText("sent to chat");
});

test("reveals skill names only in technical details", async ({ page }) => {
  await page.getByRole("button", { name: "Design" }).click();
  await page.getByRole("button", { name: "Show technical details" }).click();
  await expect(page.getByText("threadlight-design")).toBeVisible();
});

test("has no serious accessibility violations", async ({ page }) => {
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter((item) =>
      ["critical", "serious"].includes(item.impact),
    ),
  ).toEqual([]);
});
```

- [ ] **Step 4: Run the UI tests and verify the expected failure**

Run:

```bash
npm --prefix tests/playwright run test:canvas
```

Expected: FAIL because the spike page does not expose phases, a start form, or
technical details.

- [ ] **Step 5: Replace the spike page with the cockpit shell**

The final `index.html` must contain:

```html
<main class="app-shell">
  <header class="app-header">
    <div>
      <p class="eyebrow">THREADLIGHT LIFECYCLE</p>
      <h1>Start and steer a pilot</h1>
    </div>
    <button id="refresh" class="secondary" type="button">Refresh</button>
  </header>
  <p id="status" role="status">Loading workspace...</p>
  <nav id="phase-nav" aria-label="Pilot lifecycle"></nav>
  <section id="overview" aria-labelledby="overview-title">
    <div class="section-heading">
      <h2 id="overview-title">Current outcome</h2>
      <button id="start-pilot" type="button">Start a pilot</button>
    </div>
    <div id="phase-summary"></div>
  </section>
  <section id="phase-detail" aria-live="polite"></section>
  <dialog id="start-dialog">
    <form method="dialog" id="start-form">
      <h2>Start a pilot</h2>
      <label for="brief">Pilot brief</label>
      <textarea id="brief" name="brief" maxlength="4000" required></textarea>
      <div class="dialog-actions">
        <button value="cancel" class="secondary">Cancel</button>
        <button value="default" id="send-brief">Send to chat</button>
      </div>
    </form>
  </dialog>
</main>
```

Implement `app.js` around a single state object:

```javascript
const state = {
  model: null,
  selectedPhase: "design",
  showTechnical: false,
};

function phaseButton(phase) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `phase-tab status-${phase.status}`;
  button.textContent = phase.label;
  button.setAttribute(
    "aria-current",
    phase.id === state.selectedPhase ? "step" : "false",
  );
  button.addEventListener("click", () => {
    state.selectedPhase = phase.id;
    state.showTechnical = false;
    render();
  });
  return button;
}

function renderSkillDetails(phase) {
  if (!state.showTechnical) return "";
  return `<ul class="skill-list">${phase.skills.map((skill) => `
    <li>
      <code>${escapeHtml(skill.id)}</code>
      <span class="status-badge status-${skill.status}">${escapeHtml(skill.status)}</span>
    </li>`).join("")}</ul>`;
}

function renderPhase(phase) {
  const evidence = phase.evidence.length === 0
    ? "<p>No canonical evidence yet.</p>"
    : `<ul>${phase.evidence.map((item) =>
        `<li class="evidence-row">
          <code>${escapeHtml(item.relativePath)}</code>
          <button type="button" class="secondary"
            data-intent='${escapeAttribute(JSON.stringify({
              type: "inspect_evidence",
              phase: phase.id,
              evidenceId: item.relativePath,
            }))}'>Inspect in chat</button>
        </li>`).join("")}</ul>`;
  return `
    <div class="phase-card">
      <div class="phase-card__header">
        <div>
          <p class="eyebrow">${escapeHtml(phase.label)}</p>
          <h2>${viewTitle(phase.view)}</h2>
        </div>
        <span class="status-badge status-${phase.status}">${escapeHtml(phase.status)}</span>
      </div>
      <h3>Evidence</h3>
      ${evidence}
      ${phase.blockers.length > 0
        ? `<h3>Blockers</h3><ul>${phase.blockers.map((item) =>
            `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
        : ""}
      <div class="actions">
        ${phase.nextActions.length > 0
          ? `<button type="button" data-intent='${escapeAttribute(
              JSON.stringify(phase.nextActions[0]),
            )}'>Continue this phase</button>`
          : ""}
        <button type="button" class="secondary" id="technical-toggle">
          ${state.showTechnical ? "Hide" : "Show"} technical details
        </button>
      </div>
      ${renderSkillDetails(phase)}
    </div>`;
}
```

Implement the complete browser-side boundary in the same file, with no external
dependency:

```javascript
const params = new URLSearchParams(location.search);
const token = params.get("token") ?? "";
const endpoint = (pathname) =>
  `${pathname}?token=${encodeURIComponent(token)}`;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

const escapeAttribute = escapeHtml;

function viewTitle(view) {
  return VIEW_TITLES[view] ?? VIEW_TITLES.generic;
}

async function postIntent(intent) {
  const response = await fetch(endpoint("/api/intent"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(intent),
  });
  if (!response.ok) {
    const body = await response.json();
    throw new Error(body.error ?? `Intent request failed: ${response.status}`);
  }
  document.querySelector("#status").textContent =
    "Intent sent to chat for confirmation.";
}

async function loadModel() {
  const response = await fetch(endpoint("/api/model"));
  if (!response.ok) {
    throw new Error(`Model request failed: ${response.status}`);
  }
  const model = await response.json();
  if (!Array.isArray(model.phases)) {
    throw new Error("Canvas model has no phases array");
  }
  state.model = model;
  if (!model.phases.some((phase) => phase.id === state.selectedPhase)) {
    state.selectedPhase = model.phases[0]?.id ?? "design";
  }
  document.querySelector("#status").textContent = model.summary;
  render();
}

function render() {
  if (!state.model) return;
  const nav = document.querySelector("#phase-nav");
  nav.replaceChildren(...state.model.phases.map(phaseButton));
  document.querySelector("#phase-summary").innerHTML =
    state.model.errors.length === 0
      ? "<p>No projection errors.</p>"
      : `<section class="error-panel" aria-labelledby="projection-errors">
          <h3 id="projection-errors">Projection errors</h3>
          <ul>${state.model.errors.map((error) =>
            `<li><code>${escapeHtml(error.path ?? error.code)}</code>: ${escapeHtml(error.message)}</li>`
          ).join("")}</ul>
        </section>`;
  const selected = state.model.phases.find(
    (phase) => phase.id === state.selectedPhase,
  );
  document.querySelector("#phase-detail").innerHTML = selected
    ? renderPhase(selected)
    : "<p>No lifecycle phase is available.</p>";

  document
    .querySelector("#technical-toggle")
    ?.addEventListener("click", () => {
      state.showTechnical = !state.showTechnical;
      render();
    });
  for (const button of document.querySelectorAll("[data-intent]")) {
    button.addEventListener("click", () => {
      postIntent(JSON.parse(button.dataset.intent)).catch(showError);
    });
  }
}

function showError(error) {
  document.querySelector("#status").textContent = error.message;
}

const dialog = document.querySelector("#start-dialog");
document.querySelector("#start-pilot").addEventListener("click", () => {
  dialog.showModal();
});
document.querySelector("#start-form").addEventListener("submit", (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  const brief = document.querySelector("#brief").value;
  postIntent({ type: "start_pilot", brief })
    .then(() => dialog.close())
    .catch(showError);
});
document.querySelector("#refresh").addEventListener("click", () => {
  loadModel().catch(showError);
});

const events = new EventSource(endpoint("/api/events"));
events.addEventListener("workspace-changed", () => {
  loadModel().catch(showError);
});
events.addEventListener("error", () => {
  document.querySelector("#status").textContent =
    "Live refresh disconnected; use Refresh to retry.";
});

loadModel().catch(showError);
```

Replace the spike stylesheet with the complete panel layout:

```css
:root {
  color-scheme: light dark;
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
  --bg: #ffffff;
  --surface: #f6f8fa;
  --border: #d0d7de;
  --text: #1f2328;
  --muted: #59636e;
  --accent: #8250df;
  --complete: #1a7f37;
  --attention: #9a6700;
  --failed: #cf222e;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117;
    --surface: #161b22;
    --border: #30363d;
    --text: #f0f6fc;
    --muted: #8b949e;
    --accent: #a371f7;
    --complete: #3fb950;
    --attention: #d29922;
    --failed: #f85149;
  }
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-width: 320px;
  background: var(--bg);
  color: var(--text);
}

button,
textarea {
  font: inherit;
}

button {
  border: 1px solid transparent;
  border-radius: 0.5rem;
  padding: 0.65rem 0.9rem;
  background: var(--accent);
  color: #fff;
  font-weight: 700;
  cursor: pointer;
}

button.secondary {
  border-color: var(--border);
  background: var(--surface);
  color: var(--text);
}

button:focus-visible,
textarea:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--accent), transparent 45%);
  outline-offset: 2px;
}

.app-shell {
  display: grid;
  gap: 1rem;
  padding: 1rem;
}

.app-header,
.section-heading,
.phase-card__header,
.actions,
.dialog-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.eyebrow {
  margin: 0 0 0.25rem;
  color: var(--accent);
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.12em;
}

h1,
h2,
h3,
p {
  margin-top: 0;
}

#status {
  color: var(--muted);
}

#phase-nav {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.5rem;
}

.phase-tab {
  min-height: 3rem;
  border-color: var(--border);
  background: var(--surface);
  color: var(--text);
  text-align: left;
}

.phase-tab[aria-current="step"] {
  border-color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent);
}

.phase-card {
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 1rem;
  background: var(--surface);
}

.status-badge {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.2rem 0.55rem;
  color: var(--muted);
  font-size: 0.75rem;
  font-weight: 700;
}

.status-complete {
  color: var(--complete);
}

.status-ready,
.status-running,
.status-stale,
.status-blocked {
  color: var(--attention);
}

.status-failed {
  color: var(--failed);
}

.error-panel {
  border-left: 4px solid var(--failed);
  padding: 0.75rem 1rem;
  background: color-mix(in srgb, var(--failed), transparent 92%);
}

.skill-list {
  display: grid;
  gap: 0.5rem;
  padding: 0;
  list-style: none;
}

.skill-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.evidence-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-block: 0.5rem;
}

dialog {
  width: min(34rem, calc(100% - 2rem));
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 1rem;
  background: var(--bg);
  color: var(--text);
}

dialog::backdrop {
  background: rgb(0 0 0 / 45%);
}

dialog form {
  display: grid;
  gap: 0.75rem;
}

textarea {
  min-height: 9rem;
  resize: vertical;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.75rem;
  background: var(--surface);
  color: var(--text);
}

@media (max-width: 420px) {
  #phase-nav {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .app-header,
  .section-heading,
  .phase-card__header,
  .actions {
    align-items: stretch;
    flex-direction: column;
  }
}
```

- [ ] **Step 6: Implement the four specialized view summaries**

In `app.js`, make `viewTitle` and the summary block outcome-specific:

```javascript
const VIEW_TITLES = Object.freeze({
  design: "Pilot definition",
  run: "Pipeline run",
  assurance: "Evidence and assurance",
  handoff: "Production handoff",
  generic: "Lifecycle stage",
});

function specializedSummary(phase) {
  if (phase.view === "design") {
    return `${phase.skills.filter((skill) => skill.status === "complete").length}/${phase.skills.length} design outputs ready`;
  }
  if (phase.view === "run") {
    return phase.blockers[0] ?? "Build and deployment evidence is progressing.";
  }
  if (phase.view === "assurance") {
    const failed = phase.skills.filter((skill) => skill.status === "failed").length;
    return failed > 0
      ? `${failed} assurance leg(s) require attention`
      : "Quality, safety, cost, and governance evidence";
  }
  if (phase.view === "handoff") {
    return "Readiness, delivery pipeline, and customer onboarding";
  }
  return "Inspect evidence and continue safely.";
}
```

Place `VIEW_TITLES` immediately after the `state` object, and render
`<p class="phase-summary">${escapeHtml(specializedSummary(phase))}</p>`
directly below the phase title. This produces specialized views without
creating four parallel component frameworks.

- [ ] **Step 7: Run browser and Node tests**

Run:

```bash
node --test tests/canvas/*.test.mjs
npm --prefix tests/playwright run test:canvas
```

Expected: all tests PASS.

- [ ] **Step 8: Commit the v1 cockpit**

```bash
git add .github/extensions/threadlight-lifecycle/web tests/canvas/serve-canvas-fixture.mjs tests/playwright
git commit -m "feat: add the Threadlight Lifecycle Cockpit" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 10: Add integration coverage, CI, release metadata, and docs

**Files:**
- Modify: `tests/canvas/projector.test.mjs`
- Create: `.github/workflows/threadlight-canvas.yml`
- Create: `.github/extensions/threadlight-lifecycle/README.md`
- Modify: `README.md:11-30`
- Modify: `plugin.json:3-4,108-110`
- Modify: `.github/plugin/marketplace.json:8-16`

- [ ] **Step 1: Add the real sample integration test**

Append:

```javascript
// tests/canvas/projector.test.mjs
import path from "node:path";
import { fileURLToPath } from "node:url";

test("projects the governed returns-triage sample without secret reads", async () => {
  const repoRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../..",
  );
  const workspace = path.join(
    repoRoot,
    "examples",
    "returns-triage-governed",
  );
  const model = await projectWorkspace(workspace);

  assert.equal(model.phases.length, 6);
  assert.equal(model.errors.length, 0);
  assert.equal(
    model.phases
      .flatMap((phase) => phase.evidence)
      .some((item) => item.relativePath.includes(".env")),
    false,
  );
  assert.equal(
    model.phases
      .flatMap((phase) => phase.skills)
      .some((skill) => skill.id === "threadlight-govern"),
    true,
  );
});
```

- [ ] **Step 2: Run the integration test**

Run:

```bash
node --test tests/canvas/projector.test.mjs
```

Expected: PASS against the committed sample.

- [ ] **Step 3: Add focused CI**

```yaml
# .github/workflows/threadlight-canvas.yml
name: threadlight-canvas

on:
  pull_request:
    branches: [main]
    paths:
      - ".github/extensions/threadlight-lifecycle/**"
      - "tests/canvas/**"
      - "tests/playwright/canvas-tests/**"
      - "tests/playwright/canvas.config.mjs"
      - "tests/playwright/package*.json"
      - "plugin.json"
      - ".github/plugin/marketplace.json"
  push:
    branches: [main]
    paths:
      - ".github/extensions/threadlight-lifecycle/**"
      - "tests/canvas/**"
      - "tests/playwright/canvas-tests/**"
      - "tests/playwright/canvas.config.mjs"
      - "tests/playwright/package*.json"
      - "plugin.json"
      - ".github/plugin/marketplace.json"
  workflow_dispatch: {}

jobs:
  node-contracts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: node --test tests/canvas/*.test.mjs

  canvas-browser:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: tests/playwright/package-lock.json
      - run: npm ci
        working-directory: tests/playwright
      - run: npx playwright install --with-deps chromium
        working-directory: tests/playwright
      - run: npm run test:canvas
        working-directory: tests/playwright
```

- [ ] **Step 4: Document the support and security boundary**

Create `.github/extensions/threadlight-lifecycle/README.md` with these explicit
facts:

- minimum tested GitHub Copilot App version: `1.0.78-2`;
- Canvas and extensions are experimental and must be enabled;
- plugin installation makes the extension available wherever the plugin is
  enabled;
- the server binds only to `127.0.0.1` and uses a per-instance token;
- the artifact allowlist excludes `.env` and `.azure/**/.env`;
- Canvas actions send intents to chat and never run Threadlight stages;
- use `/extensions manage` or `extensions_manage inspect` for startup failures;
- non-Canvas hosts continue to use the normal skills and artifacts.

Add a concise README row after `threadlight-auto`:

```markdown
| **Threadlight Lifecycle Canvas** | **GitHub Copilot App enhancement** - an outcome-oriented cockpit for all 17 skills. Starts a pilot from a brief, projects progress from canonical artifacts, and sends safe next-action intents back to chat. Optional; existing CLI/Cowork/Coding Agent flows are unchanged. |
```

- [ ] **Step 5: Bump plugin and marketplace versions**

Set all release metadata to `1.11.0`:

```json
// plugin.json
"version": "1.11.0"
```

```json
// .github/plugin/marketplace.json
"metadata": {
  "description": "...",
  "version": "1.11.0"
}
```

```json
// .github/plugin/marketplace.json plugin entry
"version": "1.11.0"
```

Extend the plugin description with one sentence stating that the plugin bundles
the optional GitHub Copilot App Lifecycle Canvas. Do not remove or weaken the
existing skill trigger text.

- [ ] **Step 6: Run the complete targeted validation**

Run:

```bash
node --test tests/canvas/*.test.mjs
npm --prefix tests/playwright run test:canvas
python3 scripts/ci/check-skill-description-length.py
git diff --check
```

Expected:

- all Canvas Node tests PASS;
- all Canvas Playwright tests PASS;
- plugin description length check PASS;
- `git diff --check` emits no output.

- [ ] **Step 7: Reload and exercise the packaged extension**

Run or invoke:

```text
extensions_reload({})
extensions_manage({ operation: "inspect", name: "threadlight-lifecycle" })
list_canvas_capabilities({ canvasId: "threadlight-lifecycle" })
open_canvas({
  canvasId: "threadlight-lifecycle",
  instanceId: "threadlight-v1",
  input: {}
})
```

Verify in `examples/returns-triage-governed`:

- six outcome phases render;
- technical details across the six phase views expose all 17 skill names only
  after expansion;
- Design, Run, Assurance, and Handoff show distinct summaries;
- **Start a pilot** sends a typed intent to chat;
- editing an allowlisted artifact refreshes the Canvas;
- no `.env` or `.azure` path appears in the model or UI;
- closing the Canvas stops its server.

- [ ] **Step 8: Commit the release-ready integration**

```bash
git add .github/workflows/threadlight-canvas.yml .github/extensions/threadlight-lifecycle/README.md README.md plugin.json .github/plugin/marketplace.json tests/canvas/projector.test.mjs
git commit -m "feat: ship the Threadlight Lifecycle Canvas" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

## Implementation stop conditions

Stop and return to the design rather than weakening safety when any of these is
true:

- the installed plugin cannot contribute the extension without copying files
  into the target repository;
- the Canvas renderer cannot reliably open a loopback URL from the extension;
- a UI click cannot be surfaced as a visible chat intent;
- the projector needs `.env`, `.azure/**/.env`, credentials, or arbitrary file
  access to claim success;
- a status can be green only by duplicating or guessing an owning skill's gate
  semantics.

In those cases, retain the existing chat/file UX and record the failed
acceptance criterion before proposing a narrower replacement.
