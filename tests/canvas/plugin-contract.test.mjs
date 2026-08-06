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
