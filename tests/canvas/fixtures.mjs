import { mkdir, rm, utimes, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const FIXTURE_TIME = new Date("2026-08-06T08:00:00Z");

// Exact finding-id set each live-leg producer emits (one each), keyed by the
// per-file schema. Mirrors LEG_ENVELOPE_CONTRACTS in the registry so a default
// envelope built here is VALID against the projector's strict trust boundary.
const LEG_FINDING_IDS = {
  "threadlight-connect-manifest/v1": ["INT-001", "INT-002", "INT-003", "INT-004"],
  "threadlight.ground/v1": ["GRD-001", "GRD-002", "GRD-003", "GRD-004"],
  "threadlight.load/v1": ["LOAD-001", "LOAD-002", "LOAD-003"],
  "threadlight.upgrade/v1": ["UPG-001", "UPG-002", "UPG-003"],
};

/**
 * Build a shared-envelope leg manifest (skills/_shared/manifest.py shape:
 * schema / tool_version / generated_at / freshness / status / findings). Used
 * by the projector tests for the connect / ground / load / upgrade legs.
 *
 * By default the full required finding set for `schema` is emitted with every
 * finding `pass`, producing a manifest that VALIDATES against the projector's
 * strict trust boundary. Pass `overrides` to flip individual finding statuses
 * (e.g. `{ "GRD-001": "must-fix" }`), or `findings` to supply a raw list
 * verbatim (used by the negative tests that deliberately forge the shape).
 */
export function legEnvelope({
  schema = "threadlight.ground/v1",
  status = "complete",
  overrides = {},
  findings,
  toolVersion = "0.1.0",
  generatedAt = FIXTURE_TIME.toISOString(),
  validForHours = 24,
  sourceOldestAt = null,
} = {}) {
  const resolvedFindings =
    findings ??
    (LEG_FINDING_IDS[schema] ?? []).map((id) => ({
      id,
      status: overrides[id] ?? "pass",
    }));
  return {
    schema,
    tool_version: toolVersion,
    generated_at: generatedAt,
    freshness: { valid_for_hours: validForHours, source_oldest_at: sourceOldestAt },
    status,
    findings: resolvedFindings,
  };
}

/**
 * Add the exact producer-specific top-level payload shape to a valid shared
 * envelope. Values are intentionally synthetic and payload-free; the projector
 * validates the common trust boundary and per-producer top-level allowlist, not
 * the producer's full nested schema.
 */
export function producerLegEnvelope(options = {}) {
  const envelope = legEnvelope(options);
  const payloadBySchema = {
    "threadlight-connect-manifest/v1": {
      tool_name: "orders",
      integration_state: "real-verified",
      target_state: "real-verified",
      contract: {
        schema: "threadlight-connect-data-contract/v1",
        tool_name: "orders",
        generated_at: envelope.generated_at,
        fields: [],
      },
      conformance: {
        passed: true,
        evaluated: true,
        item_count: 1,
        differences: [],
      },
      evidence_summary: {
        obo_present: true,
        obo_user_scoped: true,
        roles_revalidated: true,
        required_roles: ["Orders.Read"],
        endpoint_configured: true,
        endpoint_verified: true,
      },
      apply_plan: [],
      changed_paths: [],
      apply: false,
    },
    "threadlight.ground/v1": {
      sources: [],
      acl_evidence: [],
      citation_evidence: [],
      refusal_evidence: [],
      telemetry: { retrieval_count: 0, subqueries: 0, tokens: 0 },
      retrieval_quality_baseline: null,
    },
    "threadlight.load/v1": {
      profile_name: "canvas-fixture",
      endpoint_class: "non-production",
      endpoint_configured: false,
      allow_production: false,
      adapter_name: "k6",
      budget: {
        ceiling_usd: 1,
        projected_usd: 0,
        within_ceiling: true,
        projection_source: "declared",
      },
      diagnostics: {
        sample_count: 1,
        p50_latency_ms: 10,
        p95_latency_ms: 20,
        p99_latency_ms: 30,
        error_rate: 0,
        tokens_per_request: 1,
        throughput_rps: 1,
        cold_start_latency_ms: 0,
        time_to_scale_s: 0,
        adapter_error: null,
      },
      spec_update_plan: {
        action: "advisory",
        target: "specs/SPEC.md",
        section: "load profile",
        snippet: "p95_latency_ms: 20",
      },
    },
    "threadlight.upgrade/v1": {
      plan: [],
    },
  };
  return { ...envelope, ...payloadBySchema[envelope.schema] };
}

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
    deployment_manifest: {
      subscription_id: "sub-1",
      resource_group: "rg-pilot",
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
    verdict: "hardened",
    must_fix: [],
  });
  await fixture.writeJson("specs/govern-manifest.json", {
    verdict: "governed",
    must_fix: [],
  });
  await fixture.writeJson("tests/production-readiness-manifest.json", {
    checked_at: "2026-08-06T08:00:00Z",
    go_live_recommendation: "ready",
    would_fail_hard_gate: false,
    kpi_scorecard: {
      latency_declared: true,
      cost_per_interaction_declared: true,
      success_rate_declared: true,
      deviation_alert_present: true,
      traces_emit: true,
      eval_pass_rate: 0.99,
      cost_per_interaction_usd: 0.01,
    },
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
