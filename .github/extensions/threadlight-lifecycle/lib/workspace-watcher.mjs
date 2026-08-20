import { watch } from "node:fs";
import { access, lstat, readdir } from "node:fs/promises";
import path from "node:path";

export const WATCH_ROOTS = [
  ".",
  "specs",
  "specs/sample-data",
  ".threadlight",
  ".azure",
  "docs",
  "docs/threadlight-customize",
  "infra",
  "src",
  "src/agent",
  "src/bot",
  "src/triggers",
  "src/workspace",
  ".github",
  ".github/workflows",
  "tests",
  "router-bench-out",
];

export async function watchWorkspace(
  workspace,
  onChange,
  {
    debounceMs = 150,
    onError = (error) =>
      queueMicrotask(() => {
        throw error;
      }),
  } = {},
) {
  const watchers = new Map();
  let timer = null;

  async function attachRoots() {
    for (const root of WATCH_ROOTS) {
      const target = path.join(workspace, root);
      const attached = await attachTarget(target, {
        rejectSymlinks: root === ".azure",
      });
      if (root === ".azure") {
        if (!attached) {
          continue;
        }
        await attachAzureEnvDirs(target);
      }
    }
  }

  async function attachTarget(target, { rejectSymlinks = false } = {}) {
    if (watchers.has(target)) {
      return true;
    }

    try {
      await access(target);
    } catch (error) {
      if (error?.code === "ENOENT") {
        return false;
      }
      throw error;
    }

    if (rejectSymlinks) {
      const details = await lstat(target);
      if (details.isSymbolicLink()) {
        return false;
      }
    }

    watchers.set(
      target,
      watch(target, { persistent: false }, (eventType, filename) => {
        void schedule(target, filename);
      }),
    );
    return true;
  }

  async function attachAzureEnvDirs(azureRoot) {
    let entries;
    try {
      entries = await readdir(azureRoot, { withFileTypes: true });
    } catch (error) {
      if (error?.code === "ENOENT") {
        return;
      }
      throw error;
    }

    await Promise.all(
      entries
        .filter((entry) => entry.isDirectory())
        .map((entry) =>
          attachTarget(path.join(azureRoot, entry.name), { rejectSymlinks: true })),
    );
  }

  async function shouldIgnoreEvent(target, filename) {
    const workspaceTarget = path.resolve(workspace);
    if (path.resolve(target) !== workspaceTarget) {
      return false;
    }
    try {
      const symlinkedAzdRoot = (await lstat(path.join(workspace, ".azure"))).isSymbolicLink();
      if (!symlinkedAzdRoot) {
        return false;
      }
      if (filename == null) {
        return true;
      }
      const relative = filename.toString().split(path.sep).join("/");
      return relative === ".azure" || relative.startsWith(".azure/");
    } catch (error) {
      if (error?.code === "ENOENT") {
        return false;
      }
      throw error;
    }
  }

  async function schedule(target, filename) {
    if (await shouldIgnoreEvent(target, filename)) {
      return;
    }
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        await attachRoots();
        await onChange();
      } catch (error) {
        await onError(error);
      }
    }, debounceMs);
  }

  await attachRoots();

  return {
    close() {
      clearTimeout(timer);
      for (const watcher of watchers.values()) {
        watcher.close();
      }
      watchers.clear();
    },
  };
}
