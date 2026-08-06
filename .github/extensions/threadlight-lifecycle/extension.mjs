import { createCanvas, joinSession } from "@github/copilot-sdk/extension";
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
