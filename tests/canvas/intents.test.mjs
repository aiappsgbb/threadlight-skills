import assert from "node:assert/strict";
import test from "node:test";

import {
  IntentValidationError,
  createIntentBroker,
  validateIntent,
} from "../../.github/extensions/threadlight-lifecycle/lib/intents.mjs";

test("start_pilot accepts bounded brief and stays chat-only", () => {
  const intent = validateIntent({
    type: "start_pilot",
    brief: "  Explore the pilot path  ",
  });

  assert.deepEqual(intent, {
    type: "start_pilot",
    brief: "Explore the pilot path",
  });
  assert.ok(!JSON.stringify(intent).includes("threadlight-"));
});

test("invalid intent shapes are rejected", () => {
  assert.throws(
    () => validateIntent({ type: "start_pilot", brief: "ok", extra: true }),
    IntentValidationError,
  );
  assert.throws(
    () => validateIntent({ type: "not_supported" }),
    IntentValidationError,
  );
});

test("broker submits visible chat intent prompt", async () => {
  const sent = [];
  const broker = createIntentBroker({
    send: async (payload) => {
      sent.push(payload);
    },
  });

  const result = await broker.submit({
    type: "resume_phase",
    phase: "build-deploy",
  });

  assert.deepEqual(result, {
    accepted: true,
    intent: { type: "resume_phase", phase: "build-deploy" },
  });
  assert.equal(sent.length, 1);
  assert.deepEqual(Object.keys(sent[0]), ["prompt"]);
  assert.match(sent[0].prompt, /^\[Threadlight Canvas intent\]\n/);
  assert.match(sent[0].prompt, /"type": "resume_phase"/);
  assert.match(sent[0].prompt, /Explain the proposed next action in chat/);
});
