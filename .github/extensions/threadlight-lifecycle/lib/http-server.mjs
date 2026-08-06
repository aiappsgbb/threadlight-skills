import { randomBytes } from "node:crypto";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";

const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'";
const MAX_JSON_BYTES = 64 * 1024;
const SAFE_STATIC_PATH = /^[a-zA-Z0-9._/-]+$/;

const CONTENT_TYPES = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml; charset=utf-8"],
]);

function normalizeRoot(webRoot) {
  const root =
    webRoot instanceof URL
      ? decodeURIComponent(webRoot.pathname)
      : String(webRoot);
  return path.resolve(root);
}

function setSecurityHeaders(res) {
  res.setHeader("Content-Security-Policy", CSP);
  res.setHeader("X-Content-Type-Options", "nosniff");
}

function sendJson(res, statusCode, value) {
  setSecurityHeaders(res);
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(value));
}

function sendText(res, statusCode, value) {
  setSecurityHeaders(res);
  res.writeHead(statusCode, {
    "Content-Type": "text/plain; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(value);
}

function tokenMatches(url, token) {
  const values = url.searchParams.getAll("token");
  return values.length === 1 && values[0] === token;
}

function resolveStaticPath(webRoot, pathname) {
  const staticPath = pathname === "/" ? "/index.html" : pathname;
  if (!SAFE_STATIC_PATH.test(staticPath)) {
    return null;
  }

  const resolved = path.resolve(webRoot, staticPath.slice(1));
  const relative = path.relative(webRoot, resolved);
  if (relative === "" || relative.startsWith("..") || path.isAbsolute(relative)) {
    return null;
  }
  return resolved;
}

async function readJsonBody(req) {
  const chunks = [];
  let size = 0;

  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_JSON_BYTES) {
      throw new RangeError("Request body exceeds 65536 bytes");
    }
    chunks.push(chunk);
  }

  return JSON.parse(Buffer.concat(chunks, size).toString("utf8"));
}

async function serveStatic(req, res, webRoot, pathname) {
  if (req.method !== "GET") {
    sendText(res, 405, "method_not_allowed");
    return;
  }

  const filePath = resolveStaticPath(webRoot, pathname);
  if (!filePath) {
    sendText(res, 404, "not_found");
    return;
  }

  let fileStat;
  try {
    fileStat = await stat(filePath);
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") {
      sendText(res, 404, "not_found");
      return;
    }
    sendText(res, 500, "static_read_failed");
    return;
  }

  if (!fileStat.isFile()) {
    sendText(res, 404, "not_found");
    return;
  }

  const stream = createReadStream(filePath);
  stream.once("open", () => {
    setSecurityHeaders(res);
    res.writeHead(200, {
      "Content-Type":
        CONTENT_TYPES.get(path.extname(filePath).toLowerCase()) ??
        "application/octet-stream",
      "Cache-Control": "no-store",
    });
    stream.pipe(res);
  });
  stream.once("error", () => {
    if (!res.headersSent) {
      sendText(res, 500, "static_read_failed");
    } else {
      res.destroy();
    }
  });
}

async function handleApi(req, res, url, { getModel, onIntent, token, clients }) {
  if (!tokenMatches(url, token)) {
    sendJson(res, 401, { error: "unauthorized" });
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/model") {
    sendJson(res, 200, await getModel());
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/events") {
    setSecurityHeaders(res);
    res.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store",
      Connection: "keep-alive",
    });
    clients.add(res);
    res.write("event: ready\ndata: {}\n\n");
    req.on("close", () => {
      clients.delete(res);
    });
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/intent") {
    try {
      const intent = await readJsonBody(req);
      await onIntent(intent);
      sendJson(res, 202, { accepted: true });
    } catch (error) {
      sendJson(res, 400, { error: error.message });
    }
    return;
  }

  if (req.method !== "GET") {
    sendText(res, 405, "method_not_allowed");
    return;
  }
  sendJson(res, 404, { error: "not_found" });
}

export async function createLoopbackServer({
  webRoot,
  getModel,
  onIntent,
  token = randomBytes(24).toString("base64url"),
} = {}) {
  if (typeof getModel !== "function") {
    throw new TypeError("createLoopbackServer requires getModel function");
  }
  if (typeof onIntent !== "function") {
    throw new TypeError("createLoopbackServer requires onIntent function");
  }

  const root = normalizeRoot(webRoot);
  const clients = new Set();
  const httpServer = createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    Promise.resolve(
      url.pathname.startsWith("/api/")
        ? handleApi(req, res, url, { getModel, onIntent, token, clients })
        : serveStatic(req, res, root, url.pathname),
    ).catch((error) => {
      if (!res.headersSent) {
        sendJson(res, 500, { error: error.message });
      } else {
        res.destroy(error);
      }
    });
  });

  await new Promise((resolve, reject) => {
    httpServer.once("error", reject);
    httpServer.listen(0, "127.0.0.1", () => {
      httpServer.off("error", reject);
      resolve();
    });
  });

  const address = httpServer.address();
  if (!address || typeof address !== "object" || typeof address.port !== "number") {
    await new Promise((resolve) => httpServer.close(resolve));
    throw new Error("Loopback server did not expose an address");
  }

  const origin = `http://127.0.0.1:${address.port}`;
  return {
    origin,
    token,
    url: `${origin}/?token=${encodeURIComponent(token)}`,
    publish(event = "workspace-changed") {
      for (const client of clients) {
        if (client.destroyed) {
          clients.delete(client);
          continue;
        }
        client.write(`event: ${event}\ndata: {}\n\n`);
      }
    },
    async close() {
      for (const client of clients) {
        client.end();
      }
      clients.clear();
      await new Promise((resolve, reject) => {
        httpServer.close((error) => {
          if (error) {
            reject(error);
          } else {
            resolve();
          }
        });
      });
    },
  };
}
