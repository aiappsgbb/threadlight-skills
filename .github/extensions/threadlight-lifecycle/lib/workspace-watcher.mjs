import { watch } from "node:fs";
import { access } from "node:fs/promises";
import path from "node:path";

export const WATCH_ROOTS = [
  ".",
  "specs",
  "specs/sample-data",
  ".threadlight",
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
      if (watchers.has(target)) {
        continue;
      }

      try {
        await access(target);
      } catch (error) {
        if (error?.code === "ENOENT") {
          continue;
        }
        throw error;
      }

      watchers.set(target, watch(target, { persistent: false }, schedule));
    }
  }

  function schedule() {
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
