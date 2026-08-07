import assert from "node:assert/strict";
import {
  mkdir,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  ArtifactAccessError,
  ArtifactParseError,
  createArtifactReader,
} from "../../.github/extensions/threadlight-lifecycle/lib/artifact-reader.mjs";
import { SKILL_REGISTRY } from "../../.github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs";

function fixtureUrl(name) {
  return new URL(
    `./.tmp-artifact-reader-${name}-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}/`,
    import.meta.url,
  );
}

async function withWorkspace(name, callback) {
  const base = fixtureUrl(name);
  const workspace = new URL("workspace/", base);
  await mkdir(workspace, { recursive: true });

  try {
    return await callback({
      base,
      workspace,
      workspacePath: fileURLToPath(workspace),
    });
  } finally {
    await rm(base, { recursive: true, force: true });
  }
}

test("reader accepts allowlisted JSON and rejects denied paths", async () => {
  await withWorkspace("allowed-json", async ({ workspace, workspacePath }) => {
    await mkdir(new URL("specs/", workspace), { recursive: true });
    await writeFile(
      new URL("specs/manifest.json", workspace),
      '{"name":"pilot"}',
      "utf8",
    );
    await writeFile(new URL(".env", workspace), "SECRET=value", "utf8");

    const reader = await createArtifactReader(workspacePath);

    assert.deepEqual(await reader.readJson("specs/manifest.json"), {
      name: "pilot",
    });
    await assert.rejects(
      reader.readText(".env"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === ".env",
    );
    await assert.rejects(
      reader.exists("../outside.txt"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === "../outside.txt",
    );
  });
});

test("reader rejects allowlisted symlinks escaping the workspace", async () => {
  await withWorkspace("escaped-symlink", async ({ base, workspace, workspacePath }) => {
    await mkdir(new URL("specs/", workspace), { recursive: true });
    await writeFile(new URL("outside-manifest.json", base), "{}", "utf8");
    await symlink(
      fileURLToPath(new URL("outside-manifest.json", base)),
      fileURLToPath(new URL("specs/manifest.json", workspace)),
    );

    const reader = await createArtifactReader(workspacePath);

    await assert.rejects(
      reader.exists("specs/manifest.json"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === "specs/manifest.json",
    );
  });
});

test("reader rejects allowlisted symlinks targeting denied workspace files", async () => {
  await withWorkspace("denied-symlink-target", async ({ workspace, workspacePath }) => {
    await mkdir(new URL("specs/", workspace), { recursive: true });
    await writeFile(new URL(".env", workspace), "SECRET=leaked", "utf8");
    await symlink(
      "../.env",
      fileURLToPath(new URL("specs/manifest.json", workspace)),
    );

    const reader = await createArtifactReader(workspacePath);

    await assert.rejects(
      reader.readText("specs/manifest.json"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === "specs/manifest.json",
    );
  });
});

test("reader wraps malformed JSON parse failures", async () => {
  await withWorkspace("malformed-json", async ({ workspace, workspacePath }) => {
    await mkdir(new URL("specs/", workspace), { recursive: true });
    await writeFile(new URL("specs/manifest.json", workspace), "{", "utf8");

    const reader = await createArtifactReader(workspacePath);

    await assert.rejects(
      reader.readJson("specs/manifest.json"),
      (error) =>
        error instanceof ArtifactParseError &&
        error.relativePath === "specs/manifest.json" &&
        error.cause instanceof SyntaxError,
    );
  });
});

test("reader accepts every registry artifact path even when absent", async () => {
  await withWorkspace("registry-paths", async ({ workspacePath }) => {
    const reader = await createArtifactReader(workspacePath);
    const registryPaths = new Set();

    for (const skill of SKILL_REGISTRY) {
      for (const group of skill.requiredArtifactGroups) {
        for (const artifactPath of group) {
          registryPaths.add(artifactPath);
        }
      }
      for (const artifactPath of skill.optionalArtifacts) {
        registryPaths.add(artifactPath);
      }
      if (skill.completionArtifact) {
        registryPaths.add(skill.completionArtifact);
      }
    }

    for (const artifactPath of registryPaths) {
      await assert.doesNotReject(reader.exists(artifactPath), artifactPath);
    }
  });
});
