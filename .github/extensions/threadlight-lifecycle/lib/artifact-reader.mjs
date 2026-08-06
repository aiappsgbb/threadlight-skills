import { lstat, readFile, realpath, stat } from "node:fs/promises";
import path from "node:path";

const ALLOWED_FILES = new Set([
  "AGENTS.md",
  "azure.yaml",
  "infra/main.bicep",
  "specs/SPEC.md",
  "specs/foundation.md",
  "specs/manifest.json",
  "specs/cost-manifest.json",
  "specs/evals-manifest.json",
  "specs/redteam-manifest.json",
  "specs/govern-manifest.json",
  "tests/production-readiness-manifest.json",
  ".threadlight/auto-state.json",
  ".threadlight/preflight-passed.json",
  "azure-pipelines.yml",
  "docs/safe-check-post.md",
  "docs/cost-projection.md",
  "docs/redteam-report.md",
  "docs/production-readiness-report.md",
]);

const ALLOWED_ROOTS = [
  "specs/sample-data",
  "src/agent",
  "src/bot",
  "src/triggers",
  "src/workspace",
  ".github/workflows",
  "docs/threadlight-customize",
  "router-bench-out",
];

export class ArtifactAccessError extends Error {
  constructor(relativePath, message) {
    super(`${relativePath}: ${message}`);
    this.name = "ArtifactAccessError";
    this.relativePath = relativePath;
  }
}

export class ArtifactParseError extends Error {
  constructor(relativePath, cause) {
    super(`${relativePath}: invalid JSON: ${cause.message}`, { cause });
    this.name = "ArtifactParseError";
    this.relativePath = relativePath;
    this.cause = cause;
  }
}

function isAllowed(normalized) {
  return (
    ALLOWED_FILES.has(normalized) ||
    ALLOWED_ROOTS.some(
      (root) => normalized === root || normalized.startsWith(`${root}/`),
    )
  );
}

function normalize(relativePath) {
  if (typeof relativePath !== "string") {
    throw new ArtifactAccessError(relativePath, "path must be a string");
  }
  if (path.isAbsolute(relativePath) || path.win32.isAbsolute(relativePath)) {
    throw new ArtifactAccessError(relativePath, "absolute paths are not allowed");
  }

  const normalized = path.posix.normalize(relativePath.replaceAll("\\", "/"));
  if (normalized === ".." || normalized.startsWith("../")) {
    throw new ArtifactAccessError(relativePath, "path traversal is not allowed");
  }
  if (!isAllowed(normalized)) {
    throw new ArtifactAccessError(relativePath, "path is not allowlisted");
  }

  return normalized;
}

function isMissing(error) {
  return error?.code === "ENOENT";
}

export async function createArtifactReader(workspace) {
  const workspaceReal = await realpath(workspace);
  const workspaceRoot = path.parse(workspaceReal).root;
  const rootPrefix =
    workspaceReal === workspaceRoot ? workspaceReal : `${workspaceReal}${path.sep}`;

  async function resolveAllowed(relativePath) {
    const normalized = normalize(relativePath);
    const candidate = path.resolve(workspaceReal, normalized);
    let realCandidate;

    try {
      realCandidate = await realpath(candidate);
    } catch (error) {
      if (isMissing(error)) {
        return { normalized, candidate, exists: false };
      }
      throw error;
    }

    if (realCandidate !== workspaceReal && !realCandidate.startsWith(rootPrefix)) {
      throw new ArtifactAccessError(
        normalized,
        "resolved path escapes workspace",
      );
    }

    return { normalized, candidate: realCandidate, exists: true };
  }

  return Object.freeze({
    async exists(relativePath) {
      const resolved = await resolveAllowed(relativePath);
      if (!resolved.exists) {
        return false;
      }

      const details = await stat(resolved.candidate);
      return details.isFile() || details.isDirectory();
    },

    async metadata(relativePath) {
      const resolved = await resolveAllowed(relativePath);
      if (!resolved.exists) {
        return null;
      }

      const details = await lstat(resolved.candidate);
      return {
        relativePath: resolved.normalized,
        kind: details.isDirectory() ? "directory" : "file",
        modifiedAt: details.mtime.toISOString(),
        size: details.size,
      };
    },

    async readText(relativePath) {
      const resolved = await resolveAllowed(relativePath);
      if (!resolved.exists) {
        return null;
      }

      return readFile(resolved.candidate, "utf8");
    },

    async readJson(relativePath) {
      const text = await this.readText(relativePath);
      if (text === null) {
        return null;
      }

      try {
        return JSON.parse(text);
      } catch (error) {
        throw new ArtifactParseError(normalize(relativePath), error);
      }
    },
  });
}
