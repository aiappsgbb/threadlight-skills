import { mkdir, rm, utimes, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const FIXTURE_TIME = new Date("2026-08-06T08:00:00Z");

function fixtureUrl(name) {
  return new URL(
    `./.tmp-projector-${name}-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}/`,
    import.meta.url,
  );
}

async function writeFixtureFile(workspace, relativePath, contents) {
  const target = new URL(relativePath, workspace);
  await mkdir(new URL("./", target), { recursive: true });
  await writeFile(target, contents, "utf8");
  await utimes(target, FIXTURE_TIME, FIXTURE_TIME);
}

export async function createWorkspaceFixture(name) {
  const root = fixtureUrl(name);
  const workspace = new URL("workspace/", root);
  await mkdir(workspace, { recursive: true });

  const fixture = {
    root: fileURLToPath(root),
    workspace: fileURLToPath(workspace),
    async writeString(relativePath, contents) {
      await writeFixtureFile(workspace, relativePath, contents);
    },
    async writeJson(relativePath, value) {
      await writeFixtureFile(
        workspace,
        relativePath,
        `${JSON.stringify(value, null, 2)}\n`,
      );
    },
    async cleanup() {
      await rm(root, { recursive: true, force: true });
    },
  };

  if (name === "empty") {
    return fixture;
  }

  await fixture.writeString("specs/SPEC.md", "# Pilot spec\n");
  await fixture.writeJson("specs/manifest.json", {
    traits: ["human-approval"],
    mock_systems: ["orders"],
    selectors: {
      "workspace-ui": "yes",
      "aca-job": "no",
      "event-grid": "no",
      "service-bus": "no",
    },
    scheduled_jobs: [],
  });

  if (name === "design-only") {
    return fixture;
  }

  await fixture.writeString("azure.yaml", "name: threadlight-pilot\n");
  await fixture.writeString("infra/main.bicep", "param location string\n");
  await fixture.writeJson(".threadlight/auto-state.json", {
    phase: "design",
    artifact_hash: "abc123",
  });

  if (name === "deploy-blocked") {
    return fixture;
  }

  await fixture.writeString("docs/safe-check-post.md", "PASS\n");
  await fixture.writeJson("specs/cost-manifest.json", {
    generated_at: "2026-08-06T08:00:00Z",
    verdict: "complete",
  });
  await fixture.writeJson("specs/evals-manifest.json", {
    captured_at:
      name === "stale-assurance"
        ? "2026-07-01T00:00:00Z"
        : "2026-08-06T08:00:00Z",
    verdict: "comprehensive",
    must_fix: [],
  });

  if (name === "partial-assurance" || name === "stale-assurance") {
    return fixture;
  }

  await fixture.writeJson("specs/sample-data/orders.json", [{ id: "order-1" }]);
  await fixture.writeString("src/bot/index.js", "export default {};\n");
  await fixture.writeString("src/workspace/index.html", "<main>Orders</main>\n");
  await fixture.writeJson("specs/redteam-manifest.json", {
    verdict: "pass",
    must_fix: [],
  });
  await fixture.writeJson("specs/govern-manifest.json", {
    verdict: "comprehensive",
    must_fix: [],
  });
  await fixture.writeJson("tests/production-readiness-manifest.json", {
    checked_at: "2026-08-06T08:00:00Z",
    go_live: "ready",
    hard_gate: false,
  });
  await fixture.writeString("router-bench-out/learnings-1.md", "# Learnings\n");
  await fixture.writeString(
    ".github/workflows/azd-deploy-prod.yml",
    "name: Deploy production\n",
  );
  await fixture.writeString(
    "docs/threadlight-customize/customer-profile.md",
    "# Customer profile\n",
  );

  return fixture;
}
