#!/usr/bin/env python3
"""MCP supply-chain producer — discover Model Context Protocol servers/tools in
a repo, emit an ``mcp-sbom.json``, and diff against a committed ``mcp-lock.json``.

Pure standard library. No sibling-module imports (production_ready.py imports
THIS module lazily, never the reverse). Discovery is best-effort and defensive:
one malformed config never aborts a scan.

Findings produced (aggregated, one per id) by :func:`check`:
  * SUP-010 — MCP servers pinned (version or image digest).
  * SUP-011 — MCP servers resolve from a known registry / source.
  * SUP-012 — mcp-lock.json committed and free of undocumented drift.
  * SUP-013 — MCP server credentials are not committed inline.

Remediation always points at Microsoft platform primitives (foundry-toolbox for
secret injection, Key Vault, ACR) — this amplifies the platform, never replaces it.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Tracks the threadlight-production-ready skill version (see SKILL.md frontmatter).
SBOM_VERSION = "0.6.0"

PINNABLE = {"npx", "uvx", "docker", "pip"}

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|pwd|client[_-]?secret|"
    r"connection[_-]?string|\bsas\b|access[_-]?key)", re.I)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class ToolDescriptor:
    name: str
    description_sha256: str
    input_schema_sha256: str


@dataclass
class McpServer:
    id: str
    kind: str            # npx | uvx | docker | pip | remote | unknown
    ref: str
    registry: str
    pinned: bool
    version: str | None
    digest: str | None
    declared_in: str
    tools_declared: bool
    tools: list[ToolDescriptor] = field(default_factory=list)
    creds_inline: bool = False
    parse_error: str | None = None


# --------------------------------------------------------------------------
# Hashing helpers
# --------------------------------------------------------------------------

def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _canon(obj) -> str:
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(obj)


# --------------------------------------------------------------------------
# Ref parsing: kind, pin, registry
# --------------------------------------------------------------------------

def _is_exact_version(ver: str | None) -> bool:
    if not ver:
        return False
    if ver in ("latest", "next", "*"):
        return False
    if ver[0] in "^~><=*":
        return False
    if ver.lower().endswith(".x"):
        # a trailing ``.x`` (e.g. 1.x) is a range, not a pin
        return False
    return bool(re.match(r"^[0-9]", ver))


def _parse_pin(kind: str, ref: str) -> tuple[bool, str | None, str | None]:
    """Return ``(pinned, version, digest)`` for a server ref."""
    ref = (ref or "").strip()
    if kind == "docker":
        digest = None
        md = re.search(r"@(sha256:[0-9a-fA-F]{64})", ref)
        if md:
            digest = md.group(1)
        version = None
        tagpart = ref.split("@", 1)[0]
        last = tagpart.rsplit("/", 1)[-1]
        if ":" in last:
            version = last.rsplit(":", 1)[-1]
        return (digest is not None), version, digest
    if kind == "npx":
        lead = ref.startswith("@")
        body = ref[1:] if lead else ref
        if "@" in body:
            pkg, ver = body.rsplit("@", 1)
        else:
            ver = None
        version = ver or None
        return _is_exact_version(version), version, None
    if kind in ("uvx", "pip"):
        mm = re.search(r"==\s*([0-9][\w.\-]*)", ref)
        version = mm.group(1) if mm else None
        return (version is not None), version, None
    return False, None, None


def _derive_registry(kind: str, ref: str) -> str:
    if kind == "npx":
        return "npm"
    if kind in ("uvx", "pip"):
        return "pypi"
    if kind == "docker":
        head = ref.split("/", 1)[0] if "/" in ref else ""
        if "." in head or ":" in head:
            return head
        return "docker.io"
    if kind == "remote":
        mm = re.match(r"[a-z]+://([^/]+)", ref, re.I)
        return mm.group(1) if mm else "unknown"
    return "unknown"


# --------------------------------------------------------------------------
# Config -> McpServer
# --------------------------------------------------------------------------

def _is_env_ref(v: str) -> bool:
    v = (v or "").strip()
    if v.startswith("${") and v.endswith("}"):
        return True
    if v.startswith("$") and v[1:].replace("_", "").isalnum() and v[1:].isupper():
        return True
    return False


def _looks_like_secret(key: str, val: str) -> bool:
    if not isinstance(val, str) or not val:
        return False
    if _is_env_ref(val):
        return False
    return bool(_SECRET_KEY_RE.search(key or ""))


def _detect_inline_creds(cfg: dict) -> bool:
    for section in ("env", "headers"):
        blob = cfg.get(section)
        if isinstance(blob, dict):
            for k, v in blob.items():
                if _looks_like_secret(str(k), v if isinstance(v, str) else ""):
                    return True
    return False


def _extract_tools(cfg: dict) -> tuple[bool, list[ToolDescriptor]]:
    raw = cfg.get("tools")
    if not isinstance(raw, list):
        return False, []
    tools: list[ToolDescriptor] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        schema = t.get("inputSchema", t.get("input_schema", {}))
        tools.append(ToolDescriptor(
            name=str(t.get("name", "")),
            description_sha256=_sha256_text(_canon(t.get("description", ""))),
            input_schema_sha256=_sha256_text(_canon(schema)),
        ))
    tools.sort(key=lambda x: x.name)
    return True, tools


def _first_pkg_arg(args: list[str]) -> str:
    skip = {"-y", "--yes", "-q", "--quiet", "run", "-m", "--from", "tool", "install"}
    for a in args:
        if a in skip or a.startswith("-"):
            continue
        return a
    return ""


def _docker_image(args: list[str]) -> str:
    val_flags = {"-e", "--env", "-v", "--volume", "--name", "-p",
                 "--publish", "-w", "--workdir", "--mount", "--network",
                 "--platform", "-u", "--user", "--entrypoint",
                 "-l", "--label", "--add-host", "--pull"}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "run":
            i += 1
            continue
        if a in val_flags:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a
    return ""


def _kind_and_ref(cfg: dict) -> tuple[str, str]:
    url = cfg.get("url") or cfg.get("endpoint")
    if isinstance(url, str) and url.lower().startswith(("http://", "https://")):
        return "remote", url
    cmd = str(cfg.get("command", "")).strip()
    raw_args = cfg.get("args") or []
    args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
    if cmd in ("npx", "npm"):
        return "npx", _first_pkg_arg(args)
    if cmd == "uvx":
        return "uvx", _first_pkg_arg(args)
    if cmd in ("docker", "podman"):
        return "docker", _docker_image(args)
    if cmd in ("pip", "pipx", "python", "python3", "uv"):
        return "pip", _first_pkg_arg(args)
    if cmd:
        return "unknown", (cmd + (" " + " ".join(args) if args else "")).strip()
    return "unknown", ""


def _server_from_config(server_id: str, cfg: dict, declared_in: str) -> McpServer:
    try:
        kind, ref = _kind_and_ref(cfg)
        pinned, version, digest = _parse_pin(kind, ref)
        tools_declared, tools = _extract_tools(cfg)
        return McpServer(
            id=server_id, kind=kind, ref=ref,
            registry=_derive_registry(kind, ref),
            pinned=pinned, version=version, digest=digest,
            declared_in=declared_in, tools_declared=tools_declared,
            tools=tools, creds_inline=_detect_inline_creds(cfg),
        )
    except Exception as e:  # never let one bad server abort discovery
        return McpServer(
            id=server_id, kind="unknown", ref="", registry="unknown",
            pinned=False, version=None, digest=None, declared_in=declared_in,
            tools_declared=False, tools=[], creds_inline=False,
            parse_error=f"{type(e).__name__}: {e}",
        )


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

_EMITTED = {"mcp-sbom.json", "mcp-lock.json"}
_URL_MCP_RE = re.compile(r"""["']([a-z]+://[^"'\s]+?/(?:mcp|sse)(?:/[^"'\s]*)?)["']""", re.I)


def _servers_map(data) -> dict:
    if isinstance(data, dict):
        for key in ("mcpServers", "servers", "mcp_servers"):
            mp = data.get(key)
            if isinstance(mp, dict):
                return mp
    return {}


def _discover_json_configs(root: Path) -> list[McpServer]:
    out: list[McpServer] = []
    for p in sorted(root.rglob("*.json")):
        if not p.is_file() or p.name in _EMITTED:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        mp = _servers_map(data)
        if not mp:
            continue
        rel = p.relative_to(root).as_posix()
        for sid, cfg in mp.items():
            if isinstance(cfg, dict):
                out.append(_server_from_config(str(sid), cfg, rel))
    return out


def _discover_source_refs(root: Path) -> list[McpServer]:
    out: list[McpServer] = []
    seen: set[str] = set()
    for pat in ("**/*.py", "**/*.ts", "**/*.js", "**/*.tsx", "**/*.jsx", "**/*.cs"):
        for p in sorted(root.glob(pat)):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for mm in _URL_MCP_RE.finditer(text):
                url = mm.group(1)
                if url in seen:
                    continue
                seen.add(url)
                out.append(McpServer(
                    id=url, kind="remote", ref=url,
                    registry=_derive_registry("remote", url),
                    pinned=False, version=None, digest=None,
                    declared_in=p.relative_to(root).as_posix(),
                    tools_declared=False, tools=[], creds_inline=False,
                ))
    return out


def discover(root) -> list[McpServer]:
    root = Path(root)
    servers = _discover_json_configs(root)
    have = {(s.kind, s.ref) for s in servers}
    for s in _discover_source_refs(root):
        if (s.kind, s.ref) not in have:
            servers.append(s)
            have.add((s.kind, s.ref))
    servers.sort(key=lambda s: (s.declared_in, s.id))
    return servers
