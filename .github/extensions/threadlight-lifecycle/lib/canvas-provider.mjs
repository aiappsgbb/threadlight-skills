import { createIntentBroker } from "./intents.mjs";
import { createLoopbackServer } from "./http-server.mjs";

const PHASES = [
  "design",
  "build-deploy",
  "discover",
  "protect-govern",
  "improve",
  "handoff",
];

const OPEN_INPUT_SCHEMA = {
  type: "object",
  properties: {
    phase: { enum: PHASES },
  },
  additionalProperties: false,
};

const NO_INPUT_SCHEMA = {
  type: "object",
  properties: {},
  additionalProperties: false,
};

const PREPARE_INTENT_SCHEMA = {
  type: "object",
  required: ["intent"],
  properties: {
    intent: { type: "object" },
  },
  additionalProperties: false,
};

function requireInstance(instances, instanceId) {
  const instance = instances.get(instanceId);
  if (!instance) {
    throw new Error(`Unknown Canvas instance: ${instanceId}`);
  }
  return instance;
}

export function createLifecycleCanvas({
  createCanvas,
  webRoot,
  getSession,
  projectWorkspace,
  createServer = createLoopbackServer,
} = {}) {
  const instances = new Map();

  return createCanvas({
    id: "threadlight-lifecycle",
    displayName: "Threadlight Lifecycle",
    title: "Threadlight Lifecycle",
    description:
      "Start and inspect a Threadlight pilot by outcome without needing skill names.",
    inputSchema: OPEN_INPUT_SCHEMA,
    actions: [
      {
        name: "refresh",
        description: "Refresh the Threadlight lifecycle workspace projection.",
        inputSchema: NO_INPUT_SCHEMA,
        handler: async ({ instanceId }) => {
          const instance = requireInstance(instances, instanceId);
          instance.model = await projectWorkspace(instance.workspace);
          instance.server.publish();
          return { status: instance.model.summary };
        },
      },
      {
        name: "prepare_intent",
        description: "Validate a Canvas intent and prepare it in chat.",
        inputSchema: PREPARE_INTENT_SCHEMA,
        handler: async ({ instanceId, input }) => {
          const instance = requireInstance(instances, instanceId);
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
        send: (payload) => session.send(payload),
      });
      const instance = {
        workspace,
        broker,
        model: await projectWorkspace(workspace),
        server: undefined,
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
