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
_MCP_CONFIG_NAMES = {"mcp.json", ".mcp.json"}
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
        except (ValueError, OSError) as e:
            if p.name.lower() in _MCP_CONFIG_NAMES:
                rel = p.relative_to(root).as_posix()
                out.append(McpServer(
                    id=rel, kind="unknown", ref="", registry="unknown",
                    pinned=False, version=None, digest=None, declared_in=rel,
                    tools_declared=False, tools=[], creds_inline=False,
                    parse_error=f"{type(e).__name__}: {e}",
                ))
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


# --------------------------------------------------------------------------
# SBOM serialization
# --------------------------------------------------------------------------

def _server_to_dict(s: McpServer) -> dict:
    return {
        "id": s.id, "kind": s.kind, "ref": s.ref, "registry": s.registry,
        "pinned": s.pinned, "version": s.version, "digest": s.digest,
        "declared_in": s.declared_in, "tools_declared": s.tools_declared,
        "creds_inline": s.creds_inline,
        "parse_error": s.parse_error,
        "tools": [
            {"name": t.name, "description_sha256": t.description_sha256,
             "input_schema_sha256": t.input_schema_sha256}
            for t in s.tools
        ],
    }


def build_sbom(servers: list[McpServer]) -> dict:
    pinnable = [s for s in servers if s.kind in PINNABLE]
    return {
        "schema_version": "1.0",
        "generator": "threadlight-production-ready/mcp_sbom",
        "generator_version": SBOM_VERSION,
        "servers": [_server_to_dict(s) for s in servers],
        "summary": {
            "server_count": len(servers),
            "pinned": sum(1 for s in pinnable if s.pinned),
            "unpinned": sum(1 for s in pinnable if not s.pinned),
            "remote": sum(1 for s in servers if s.kind == "remote"),
            "inline_creds": sum(1 for s in servers if s.creds_inline),
            "must_fix": 0,
            "should_fix": 0,
        },
    }


# --------------------------------------------------------------------------
# Lock loading + drift diff
# --------------------------------------------------------------------------

def load_lock(path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if isinstance(data, dict) and isinstance(data.get("servers"), dict):
        return data
    return None


def diff_lock(server: McpServer, lock_entry: dict) -> list[str]:
    reasons: list[str] = []
    if lock_entry.get("kind") is not None and server.kind != lock_entry.get("kind"):
        reasons.append(
            f"kind changed ({lock_entry.get('kind')} -> {server.kind})")
    if lock_entry.get("ref") is not None and server.ref != lock_entry.get("ref"):
        reasons.append(
            f"ref changed ({lock_entry.get('ref')} -> {server.ref})")
    if server.version != lock_entry.get("version"):
        reasons.append(
            f"version changed ({lock_entry.get('version')} -> {server.version})")
    if server.digest != lock_entry.get("digest"):
        reasons.append(
            f"digest changed ({lock_entry.get('digest')} -> {server.digest})")
    locked_tools = lock_entry.get("tools") or {}
    cur = {t.name: t for t in server.tools}
    for name in cur:
        if name not in locked_tools:
            reasons.append(f"tool added: {name}")
    for name in locked_tools:
        if name not in cur:
            reasons.append(f"tool removed: {name}")
    for name, t in cur.items():
        lt = locked_tools.get(name)
        if not lt:
            continue
        if t.description_sha256 != lt.get("description_sha256"):
            reasons.append(f"tool description changed: {name}")
        if t.input_schema_sha256 != lt.get("input_schema_sha256"):
            reasons.append(f"tool input schema changed: {name}")
    return reasons


# --------------------------------------------------------------------------
# Per-server status + aggregate findings
# --------------------------------------------------------------------------

_STATUS_RANK = {
    "not-applicable": 0, "pass": 1, "should-fix": 2, "must-fix": 3,
}
SUP_MCP_IDS = ("SUP-010", "SUP-011", "SUP-012", "SUP-013")
_SUP_MCP_TITLES = {
    "SUP-010": "MCP servers are pinned",
    "SUP-011": "MCP servers resolve from a known registry",
    "SUP-012": "mcp-lock.json committed and drift-free",
    "SUP-013": "MCP credentials are not committed inline",
}


def _worst(statuses: list[str]) -> str:
    if not statuses:
        return "not-applicable"
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 0))


def _per_server_status(server: McpServer, lock: dict | None,
                       lock_exists: bool) -> dict:
    # spec §6 — an unparseable MCP config can't be graded for
    # registry/lock/creds; surface it as a SUP-010 should-fix only.
    if server.parse_error:
        return {"SUP-010": "should-fix", "SUP-011": "not-applicable",
                "SUP-012": "not-applicable", "SUP-013": "not-applicable"}
    st: dict[str, str] = {}
    # SUP-010 — pinning
    if server.kind == "remote":
        st["SUP-010"] = "not-applicable"
    elif server.kind in PINNABLE:
        st["SUP-010"] = "pass" if server.pinned else "must-fix"
    else:
        st["SUP-010"] = "should-fix"
    # SUP-011 — registry known
    st["SUP-011"] = "pass" if server.registry != "unknown" else "should-fix"
    # SUP-012 — lock present + drift-free
    if not lock_exists:
        st["SUP-012"] = "should-fix"
    else:
        entry = (lock or {}).get("servers", {}).get(server.id)
        if entry is None:
            st["SUP-012"] = "should-fix"
        else:
            drift = diff_lock(server, entry)
            if not drift:
                st["SUP-012"] = "pass"
            else:
                st["SUP-012"] = "must-fix" if server.pinned else "should-fix"
    # SUP-013 — no inline creds
    st["SUP-013"] = "must-fix" if server.creds_inline else "pass"
    return st


def _detail_for(fid: str, offenders: list[str], lock_exists: bool) -> str:
    if fid == "SUP-010":
        return ("Pin every MCP server to an exact version or image digest: "
                + ", ".join(offenders)) if offenders else \
            "All MCP servers are pinned to an exact version or digest."
    if fid == "SUP-011":
        return ("Resolve these MCP servers from a known registry/source: "
                + ", ".join(offenders)) if offenders else \
            "All MCP servers resolve from a known registry or source."
    if fid == "SUP-012":
        if not lock_exists:
            return ("Commit an mcp-lock.json (run mcp_sbom.py --update-lock) so "
                    "server/tool drift is reviewable.")
        return ("Reconcile mcp-lock.json — undocumented drift in: "
                + ", ".join(offenders)) if offenders else \
            "mcp-lock.json is committed and matches the current MCP surface."
    if fid == "SUP-013":
        return ("Move inline credentials to injected secrets (foundry-toolbox / "
                "Key Vault) for: " + ", ".join(offenders)) if offenders else \
            "No MCP server commits credentials inline."
    return ""


def check(servers: list[McpServer], lock: dict | None,
          lock_exists: bool) -> list[dict]:
    if not servers:
        return [{"id": fid, "title": _SUP_MCP_TITLES[fid],
                 "status": "not-applicable",
                 "detail": "No MCP servers declared in this repo.",
                 "offenders": []} for fid in SUP_MCP_IDS]
    per = [(s, _per_server_status(s, lock, lock_exists)) for s in servers]
    findings = []
    for fid in SUP_MCP_IDS:
        statuses = [st[fid] for _s, st in per]
        offenders = sorted({s.id for s, st in per
                            if st[fid] in ("must-fix", "should-fix")})
        findings.append({
            "id": fid, "title": _SUP_MCP_TITLES[fid],
            "status": _worst(statuses),
            "detail": _detail_for(fid, offenders, lock_exists),
            "offenders": offenders,
        })
    return findings


# --------------------------------------------------------------------------
# Lock authoring + top-level assess
# --------------------------------------------------------------------------

def update_lock(sbom: dict) -> dict:
    servers = {}
    for s in sbom.get("servers", []):
        servers[s["id"]] = {
            "kind": s["kind"], "ref": s["ref"], "registry": s["registry"],
            "version": s.get("version"), "digest": s.get("digest"),
            "tools": {t["name"]: {
                "description_sha256": t["description_sha256"],
                "input_schema_sha256": t["input_schema_sha256"],
            } for t in s.get("tools", [])},
        }
    return {"schema_version": "1.0", "servers": servers}


def assess(root, lock_path=None) -> tuple[dict, list[dict]]:
    root = Path(root)
    servers = discover(root)
    sbom = build_sbom(servers)
    lp = Path(lock_path) if lock_path else (root / "mcp-lock.json")
    lock = load_lock(lp)
    lock_exists = lock is not None
    findings = check(servers, lock, lock_exists)
    must = sum(1 for f in findings if f["status"] == "must-fix")
    should = sum(1 for f in findings if f["status"] == "should-fix")
    sbom["summary"]["must_fix"] = must
    sbom["summary"]["should_fix"] = should
    per = [_per_server_status(s, lock, lock_exists) for s in servers]
    for sd, st in zip(sbom["servers"], per):
        sd["findings"] = st
    return sbom, findings
