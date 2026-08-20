import assert from "node:assert/strict";
import {
  chmod,
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

test("reader allowlists the new live-leg + qualification manifests", async () => {
  const NEW_PATHS = [
    "qualification/sizing-manifest.json",
    "specs/connect-manifest.json",
    "specs/ground-manifest.json",
    "specs/load-manifest.json",
    "specs/upgrade-manifest.json",
  ];
  await withWorkspace("new-leg-paths", async ({ workspacePath }) => {
    const reader = await createArtifactReader(workspacePath);
    for (const artifactPath of NEW_PATHS) {
      // Absent-but-allowlisted: no throw, and exists() is false until written.
      assert.equal(await reader.exists(artifactPath), false, artifactPath);
    }
  });
  // And every new path is actually referenced by the registry (reader <-> registry parity).
  const registryPaths = new Set(
    SKILL_REGISTRY.flatMap((skill) =>
      skill.requiredArtifactGroups.flat(),
    ),
  );
  for (const artifactPath of NEW_PATHS) {
    assert.ok(
      registryPaths.has(artifactPath),
      `${artifactPath} is allowlisted but not referenced by any registry skill`,
    );
  }
});

test("reader still rejects a non-allowlisted specs manifest", async () => {
  await withWorkspace("denied-specs", async ({ workspacePath }) => {
    const reader = await createArtifactReader(workspacePath);
    await assert.rejects(
      reader.exists("specs/not-a-real-manifest.json"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === "specs/not-a-real-manifest.json",
    );
  });
});

test("reader allows cost evidence artifacts and still rejects unlisted siblings", async () => {
  await withWorkspace("cost-evidence", async ({ workspace, workspacePath }) => {
    await mkdir(new URL("specs/", workspace), { recursive: true });
    await mkdir(new URL("docs/", workspace), { recursive: true });
    await writeFile(
    new URL("specs/cost-actuals-manifest.json", workspace),
    '{"schema":"threadlight-cost-actuals/v1"}',
    "utf8",
    );
    await writeFile(
    new URL("specs/cost-reconciliation-manifest.json", workspace),
    '{"schema":"threadlight-cost-reconciliation/v1"}',
    "utf8",
    );
    await writeFile(
    new URL("docs/cost-reconciliation-report.md", workspace),
    "# Cost reconciliation\n",
    "utf8",
    );

    const reader = await createArtifactReader(workspacePath);

    assert.deepEqual(
    await reader.readJson("specs/cost-actuals-manifest.json"),
    { schema: "threadlight-cost-actuals/v1" },
    );
    assert.deepEqual(
    await reader.readJson("specs/cost-reconciliation-manifest.json"),
    { schema: "threadlight-cost-reconciliation/v1" },
    );
    assert.equal(
    await reader.readText("docs/cost-reconciliation-report.md"),
    "# Cost reconciliation\n",
    );
    await assert.rejects(
    reader.exists("docs/cost-reconciliation-notes.md"),
    (error) =>
      error instanceof ArtifactAccessError &&
      error.relativePath === "docs/cost-reconciliation-notes.md",
    );
  });
});

test("reader allows azd env directories without allowlisting arbitrary root secrets", async () => {
  await withWorkspace("azd-env", async ({ workspace, workspacePath }) => {
    await mkdir(new URL(".azure/dev/", workspace), { recursive: true });
    await writeFile(new URL(".azure/README.md", workspace), "notes", "utf8");
    await writeFile(
      new URL(".azure/dev/.env", workspace),
      "AGENT_FQDN=threadlight-dev.example.com\n",
      "utf8",
    );

    const reader = await createArtifactReader(workspacePath);

    assert.deepEqual(await reader.readDir(".azure"), [".azure/dev"]);
    assert.equal(
      await reader.readAzdEnvValue(".azure/dev", "AGENT_FQDN"),
      "threadlight-dev.example.com",
    );
    await assert.rejects(
      reader.readText(".azure/dev/.env"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === ".azure/dev/.env",
    );
    await assert.rejects(
      reader.readAzdEnvValue(".azure/dev", "AZURE_SUBSCRIPTION_ID"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === "AZURE_SUBSCRIPTION_ID",
    );
    await assert.rejects(
      reader.readText(".env"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === ".env",
    );
  });
});

test("reader includes symlinked azd env directories in discovery so callers can treat them as untrusted", async () => {
  await withWorkspace("azd-symlink-env", async ({ workspace, workspacePath }) => {
    await mkdir(new URL(".azure/dev/", workspace), { recursive: true });
    await mkdir(new URL("shadow-env/", workspace), { recursive: true });
    await symlink("../shadow-env", fileURLToPath(new URL(".azure/prod", workspace)));

    const reader = await createArtifactReader(workspacePath);

    assert.deepEqual(
      await reader.readDir(".azure"),
      [".azure/dev", ".azure/prod"],
    );
    await assert.rejects(
      reader.readAzdEnvValue(".azure/prod", "AGENT_FQDN"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === ".azure/prod",
    );
  });
});

test("reader rejects symlinked azd env files that escape the workspace", async () => {
  await withWorkspace("azd-env-symlink", async ({ base, workspace, workspacePath }) => {
    await mkdir(new URL(".azure/dev/", workspace), { recursive: true });
    await writeFile(new URL("outside.env", base), "AGENT_FQDN=leaked.example.com\n", "utf8");
    await symlink(
      fileURLToPath(new URL("outside.env", base)),
      fileURLToPath(new URL(".azure/dev/.env", workspace)),
    );

    const reader = await createArtifactReader(workspacePath);

    await assert.rejects(
      reader.readAzdEnvValue(".azure/dev", "AGENT_FQDN"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === ".azure/dev/.env",
    );
  });
});

test("reader rejects symlinked azd roots", async () => {
  await withWorkspace("azd-root-symlink", async ({ base, workspace, workspacePath }) => {
    await mkdir(new URL("secrets/dev/", workspace), { recursive: true });
    await writeFile(
      new URL("secrets/dev/.env", workspace),
      "AGENT_FQDN=secret.example.com\n",
      "utf8",
    );
    await symlink(
      fileURLToPath(new URL("secrets/", workspace)),
      fileURLToPath(new URL(".azure", workspace)),
    );

    const reader = await createArtifactReader(workspacePath);

    await assert.rejects(
      reader.readDir(".azure"),
      (error) =>
        error instanceof ArtifactAccessError &&
        error.relativePath === ".azure",
    );
  });
});

test("reader treats unreadable .azure roots as absent evidence", async () => {
  await withWorkspace("azd-root-unreadable", async ({ workspace, workspacePath }) => {
    await mkdir(new URL(".azure/dev/", workspace), { recursive: true });
    await writeFile(
      new URL(".azure/dev/.env", workspace),
      "AGENT_FQDN=threadlight-dev.example.com\n",
      "utf8",
    );
    await chmod(fileURLToPath(new URL(".azure/", workspace)), 0o000);

    try {
      const reader = await createArtifactReader(workspacePath);

      assert.equal(await reader.readDir(".azure"), null);
      assert.equal(await reader.readAzdEnvValue(".azure/dev", "AGENT_FQDN"), null);
    } finally {
      await chmod(fileURLToPath(new URL(".azure/", workspace)), 0o755).catch(() => {});
    }
  });
});

test("reader treats unreadable azd env directories as absent evidence", async () => {
  await withWorkspace("azd-env-unreadable", async ({ workspace, workspacePath }) => {
    await mkdir(new URL(".azure/dev/", workspace), { recursive: true });
    await writeFile(
      new URL(".azure/dev/.env", workspace),
      "AGENT_FQDN=threadlight-dev.example.com\n",
      "utf8",
    );
    await chmod(fileURLToPath(new URL(".azure/dev/", workspace)), 0o000);

    try {
      const reader = await createArtifactReader(workspacePath);

      assert.equal(await reader.readAzdEnvValue(".azure/dev", "AGENT_FQDN"), null);
    } finally {
      await chmod(fileURLToPath(new URL(".azure/dev/", workspace)), 0o755).catch(() => {});
    }
  });
});

test("reader excludes unreadable azd env directories from discovery", async () => {
  await withWorkspace("azd-env-filter", async ({ workspace, workspacePath }) => {
    await mkdir(new URL(".azure/dev/", workspace), { recursive: true });
    await mkdir(new URL(".azure/prod/", workspace), { recursive: true });
    await writeFile(
      new URL(".azure/dev/.env", workspace),
      "AGENT_FQDN=threadlight-dev.example.com\n",
      "utf8",
    );
    await chmod(fileURLToPath(new URL(".azure/prod/", workspace)), 0o000);

    try {
      const reader = await createArtifactReader(workspacePath);

      assert.deepEqual(await reader.readDir(".azure"), [".azure/dev"]);
    } finally {
      await chmod(fileURLToPath(new URL(".azure/prod/", workspace)), 0o755).catch(() => {});
    }
  });
});

test("reader treats a directory-valued azd env file as absent evidence", async () => {
  await withWorkspace("azd-env-dir-env", async ({ workspace, workspacePath }) => {
    await mkdir(new URL(".azure/dev/.env/", workspace), { recursive: true });

    const reader = await createArtifactReader(workspacePath);

    assert.equal(await reader.readAzdEnvValue(".azure/dev", "AGENT_FQDN"), null);
  });
});
