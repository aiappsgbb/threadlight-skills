import { createCanvas, joinSession } from "@github/copilot-sdk/extension";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createLifecycleCanvas } from "./lib/canvas-provider.mjs";
import { projectWorkspace } from "./lib/projector.mjs";
import { watchWorkspace } from "./lib/workspace-watcher.mjs";

const extensionRoot = path.dirname(fileURLToPath(import.meta.url));
let session;
const canvas = createLifecycleCanvas({
  createCanvas,
  webRoot: path.join(extensionRoot, "web"),
  getSession: () => session,
  projectWorkspace,
  watchWorkspace,
});
session = await joinSession({ canvases: [canvas] });
