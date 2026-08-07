import { fileURLToPath } from "node:url";

import { createLoopbackServer } from "../../.github/extensions/threadlight-lifecycle/lib/http-server.mjs";
import { projectWorkspace } from "../../.github/extensions/threadlight-lifecycle/lib/projector.mjs";

const root = fileURLToPath(new URL("../../examples/returns-triage-governed/", import.meta.url));
const webRoot = new URL("../../.github/extensions/threadlight-lifecycle/web/", import.meta.url);

const server = await createLoopbackServer({
  webRoot,
  token: "canvas-test",
  port: 4187,
  getModel: () => projectWorkspace(root),
  onIntent: async () => {},
});

console.log(server.origin);

async function shutdown() {
  await server.close();
  process.exit(0);
}

process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
