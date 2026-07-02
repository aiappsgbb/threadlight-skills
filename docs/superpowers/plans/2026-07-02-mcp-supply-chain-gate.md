# MCP Supply-Chain Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an MCP (Model Context Protocol) supply-chain gate to Threadlight — a pure-stdlib producer that discovers MCP servers/tools in a repo, emits an `mcp-sbom.json`, detects drift against a committed `mcp-lock.json`, and surfaces four new supply-chain findings (SUP-010..013) that the production-readiness assessor reports and the CI/CD generator can enforce.

**Architecture:** One new standalone producer module (`mcp_sbom.py`, pure stdlib) does discovery + SBOM + lock-diff and exposes `assess(root) -> (sbom, findings)`. Two consumers wire it in without re-implementing it: (1) production-ready Pillar 09 lazily imports the producer, folds its four findings into the supply-chain scorecard, and writes the SBOM sidecar; (2) the cicd generator gains an optional `--mcp-gate soft|hard` knob that flips a template gate stage (mirroring the existing eval-gate). Everything points remediation at Microsoft platform primitives (foundry-toolbox, Key Vault, ACR) — it amplifies the platform, never replaces it.

**Tech Stack:** Python 3 standard library only (`json`, `re`, `hashlib`, `pathlib`, `argparse`, `dataclasses`, `importlib`). Tests are bare `test_*` functions with `assert` (sibling convention), run per-skill via `python3 -m pytest skills/<skill>/tests/ -v`.

**Source of truth:** `docs/superpowers/specs/2026-07-02-mcp-supply-chain-gate-design.md` (§1-§9). Read it before starting.

**Environment notes:**
- `python` is NOT on PATH — always use `python3` (3.14.x, pytest 9.x).
- Every commit carries the trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- Before every commit, review the staged diff so it reads as standalone product documentation; every byte ships on its own merit.

---

## File Structure

**New files:**
- `skills/threadlight-production-ready/scripts/mcp_sbom.py` — the producer (model + discovery + SBOM + lock-diff + CLI). Pure stdlib, no sibling imports.
- `skills/threadlight-production-ready/tests/test_mcp_sbom.py` — unit tests for the producer.
- `skills/threadlight-production-ready/tests/test_mcp_supply_chain_pillar.py` — integration tests for the Pillar-09 wiring.
- `skills/threadlight-cicd/tests/test_mcp_gate.py` — tests for the generated MCP gate.

**Modified files:**
- `skills/threadlight-production-ready/scripts/production_ready.py` — `RepoContext.mcp_sbom` field, `FINDING_CATALOG` SUP-010..013, `_check_mcp_supply`/`_load_mcp_sbom` helpers wired into `_check_supply_static`, SBOM sidecar write in `main()`, `VERSION` bump.
- `skills/threadlight-production-ready/tests/test_version.py` — 0.5.1 → 0.6.0.
- `skills/threadlight-production-ready/SKILL.md` — frontmatter version + MCP-gate note.
- `skills/threadlight-production-ready/references/pillars/09-supply-chain.md`, `references/skill-tool-supply-chain.md` — MCP docs.
- `skills/threadlight-cicd/scripts/generate_pipeline.py` — `--mcp-gate` knob + CONTEXT tokens + `VERSION` bump.
- `skills/threadlight-cicd/references/github-actions/azd-deploy-prod.yml.tmpl`, `references/azure-devops/azure-pipelines.yml.tmpl` — MCP gate stage.
- `skills/threadlight-cicd/SKILL.md` — frontmatter version + knob doc.
- `plugin.json`, `.github/plugin/marketplace.json`, `CHANGELOG.md` — repo metadata.

---

## Task 1: Producer — data model + discovery

**Files:**
- Create: `skills/threadlight-production-ready/scripts/mcp_sbom.py`
- Test: `skills/threadlight-production-ready/tests/test_mcp_sbom.py`

- [ ] **Step 1: Write the failing tests**

Create `skills/threadlight-production-ready/tests/test_mcp_sbom.py`:

```python
"""Unit tests for the MCP supply-chain producer (mcp_sbom.py).

stdlib only; bare ``test_`` functions + ``assert`` (matches sibling tests).
"""
import importlib.util
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "mcp_sbom", ROOT / "scripts" / "mcp_sbom.py"
)
m = importlib.util.module_from_spec(_spec)
sys.modules["mcp_sbom"] = m
_spec.loader.exec_module(m)


def _write_repo(**files: str) -> pathlib.Path:
    """Materialize a tiny repo on disk; keys are repo-relative paths."""
    root = pathlib.Path(tempfile.mkdtemp())
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _by_id(servers):
    return {s.id: s for s in servers}


# --- discovery across config shapes ---------------------------------------

def test_discovers_mcpservers_map_in_dot_mcp_json():
    root = _write_repo(**{".mcp.json": json.dumps({"mcpServers": {
        "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem@1.2.3"]},
    }})})
    servers = _by_id(m.discover(root))
    assert "fs" in servers
    assert servers["fs"].kind == "npx"
    assert servers["fs"].declared_in == ".mcp.json"


def test_discovers_servers_map_key_variant():
    root = _write_repo(**{"tools/mcp-config.json": json.dumps({"servers": {
        "git": {"command": "uvx", "args": ["mcp-server-git"]},
    }})})
    servers = _by_id(m.discover(root))
    assert servers["git"].kind == "uvx"


def test_ignores_json_without_a_servers_map():
    root = _write_repo(**{"package.json": json.dumps({"name": "x", "version": "1.0.0"})})
    assert m.discover(root) == []


# --- pin detection --------------------------------------------------------

def test_npx_unpinned_vs_pinned_vs_latest():
    assert m._parse_pin("npx", "@modelcontextprotocol/server-filesystem") == (False, None, None)
    assert m._parse_pin("npx", "@modelcontextprotocol/server-filesystem@1.2.3") == (True, "1.2.3", None)
    assert m._parse_pin("npx", "@scope/x@latest") == (False, "latest", None)


def test_docker_digest_is_pinned():
    pinned, version, digest = m._parse_pin(
        "docker", "mcp/filesystem@sha256:" + "a" * 64)
    assert pinned is True
    assert digest == "sha256:" + "a" * 64


def test_docker_tag_only_is_unpinned():
    pinned, version, digest = m._parse_pin("docker", "mcp/filesystem:latest")
    assert pinned is False
    assert digest is None
    assert version == "latest"


def test_pip_and_uvx_exact_pin():
    assert m._parse_pin("pip", "mcp-server-foo==2.0.0")[0] is True
    assert m._parse_pin("uvx", "mcp-server-foo")[0] is False


# --- registry derivation --------------------------------------------------

def test_registry_derivation():
    assert m._derive_registry("npx", "@scope/x") == "npm"
    assert m._derive_registry("uvx", "mcp-server-git") == "pypi"
    assert m._derive_registry("docker", "mcp/filesystem:1") == "docker.io"
    assert m._derive_registry("docker", "myacr.azurecr.io/mcp/x:1") == "myacr.azurecr.io"
    assert m._derive_registry("remote", "https://api.example.com/mcp") == "api.example.com"
    assert m._derive_registry("unknown", "") == "unknown"


# --- inline credential detection ------------------------------------------

def test_inline_cred_flagged_and_env_ref_is_clean():
    inline = {"command": "npx", "args": ["-y", "@x@1.0.0"],
              "env": {"API_KEY": "sk-livesecretvalue0123456789abcdef"}}
    clean = {"command": "npx", "args": ["-y", "@x@1.0.0"],
             "env": {"API_KEY": "${OPENAI_API_KEY}"}}
    assert m._detect_inline_creds(inline) is True
    assert m._detect_inline_creds(clean) is False


# --- tool descriptor hashing ----------------------------------------------

def test_declared_tools_are_hashed_and_sorted():
    cfg = {"command": "npx", "args": ["-y", "@x@1.0.0"], "tools": [
        {"name": "write", "description": "write a file", "inputSchema": {"type": "object"}},
        {"name": "read", "description": "read a file", "inputSchema": {"type": "object"}},
    ]}
    declared, tools = m._extract_tools(cfg)
    assert declared is True
    assert [t.name for t in tools] == ["read", "write"]  # sorted
    assert len(tools[0].description_sha256) == 64


def test_no_tools_key_means_not_declared():
    declared, tools = m._extract_tools({"command": "npx", "args": ["@x@1.0.0"]})
    assert declared is False
    assert tools == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_mcp_sbom.py -v`
Expected: collection error / all FAIL — `mcp_sbom.py` does not exist yet.

- [ ] **Step 3: Create the producer (part 1)**

Create `skills/threadlight-production-ready/scripts/mcp_sbom.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_mcp_sbom.py -v`
Expected: all PASS (11 tests). *(A 12th regression test —
`test_docker_platform_flag_does_not_swallow_image`, asserting `_docker_image`
skips `--platform`/other value flags and keeps the digest — was added during code
review; `val_flags` above already includes those flags.)*

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/scripts/mcp_sbom.py \
        skills/threadlight-production-ready/tests/test_mcp_sbom.py
git commit -m "feat(production-ready): MCP SBOM producer — model + discovery

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 2: Producer — SBOM build, lock diff, findings, assess

**Files:**
- Modify: `skills/threadlight-production-ready/scripts/mcp_sbom.py` (append)
- Test: `skills/threadlight-production-ready/tests/test_mcp_sbom.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `skills/threadlight-production-ready/tests/test_mcp_sbom.py`:

```python
# --- SBOM shape -----------------------------------------------------------

def test_build_sbom_summary_counts():
    root = _write_repo(**{".mcp.json": json.dumps({"mcpServers": {
        "fs": {"command": "npx", "args": ["-y", "@x/fs@1.0.0"]},
        "loose": {"command": "npx", "args": ["-y", "@x/loose"]},
        "api": {"url": "https://api.example.com/mcp"},
        "leaky": {"command": "npx", "args": ["-y", "@x/y"],
                  "env": {"TOKEN": "ghp_abcdef0123456789abcdef0123456789abcd"}},
    }})})
    sbom = m.build_sbom(m.discover(root))
    assert sbom["schema_version"] == "1.0"
    s = sbom["summary"]
    assert s["server_count"] == 4
    assert s["pinned"] == 1
    assert s["unpinned"] == 2  # loose + leaky (remote is neither)
    assert s["remote"] == 1
    assert s["inline_creds"] == 1
    assert s["must_fix"] == 0 and s["should_fix"] == 0  # filled by assess()


# --- lock diff ------------------------------------------------------------

def test_diff_lock_flags_version_change():
    srv = m.McpServer(id="fs", kind="npx", ref="@x/fs@2.0.0", registry="npm",
                      pinned=True, version="2.0.0", digest=None,
                      declared_in=".mcp.json", tools_declared=False)
    reasons = m.diff_lock(srv, {"version": "1.0.0", "digest": None, "tools": {}})
    assert any("version" in r for r in reasons)


def test_diff_lock_flags_tool_added_and_desc_changed():
    tools = [m.ToolDescriptor("read", "d" * 64, "s" * 64),
             m.ToolDescriptor("write", "e" * 64, "s" * 64)]
    srv = m.McpServer(id="fs", kind="npx", ref="@x/fs@1.0.0", registry="npm",
                      pinned=True, version="1.0.0", digest=None,
                      declared_in=".mcp.json", tools_declared=True, tools=tools)
    lock_entry = {"version": "1.0.0", "digest": None,
                  "tools": {"read": {"description_sha256": "X" * 64,
                                     "input_schema_sha256": "s" * 64}}}
    reasons = m.diff_lock(srv, lock_entry)
    assert any("added" in r and "write" in r for r in reasons)
    assert any("description" in r and "read" in r for r in reasons)


def test_diff_lock_flags_ref_swap_same_version():
    # A package swap at the same version string must be caught as drift.
    srv = m.McpServer(id="fs", kind="npx", ref="@evil/fs@1.0.0", registry="npm",
                      pinned=True, version="1.0.0", digest=None,
                      declared_in=".mcp.json", tools_declared=False)
    lock_entry = {"kind": "npx", "ref": "@trusted/fs@1.0.0", "registry": "npm",
                  "version": "1.0.0", "digest": None, "tools": {}}
    reasons = m.diff_lock(srv, lock_entry)
    assert any("ref changed" in r for r in reasons)


# --- per-server status + aggregate check ----------------------------------

def test_check_returns_four_aggregate_findings():
    root = _write_repo(**{".mcp.json": json.dumps({"mcpServers": {
        "fs": {"command": "npx", "args": ["-y", "@x/fs@1.0.0"]},
    }})})
    servers = m.discover(root)
    findings = m.check(servers, lock=None, lock_exists=False)
    ids = sorted(f["id"] for f in findings)
    assert ids == ["SUP-010", "SUP-011", "SUP-012", "SUP-013"]


def test_unpinned_server_makes_sup010_must_fix():
    root = _write_repo(**{".mcp.json": json.dumps({"mcpServers": {
        "loose": {"command": "npx", "args": ["-y", "@x/loose"]},
    }})})
    servers = m.discover(root)
    by = {f["id"]: f for f in m.check(servers, lock=None, lock_exists=False)}
    assert by["SUP-010"]["status"] == "must-fix"
    assert by["SUP-012"]["status"] == "should-fix"  # no lock committed


def test_duplicate_server_ids_do_not_mask_unpinned():
    # Same id in two files: unpinned twin (sorts first) must still drive
    # SUP-010 must-fix, not be masked by the pinned twin (sorts last).
    root = _write_repo(**{
        ".mcp.json": json.dumps({"mcpServers": {
            "fs": {"command": "npx", "args": ["-y", "@x/fs"]}}}),
        "sub/.mcp.json": json.dumps({"mcpServers": {
            "fs": {"command": "npx", "args": ["-y", "@x/fs@1.0.0"]}}}),
    })
    servers = m.discover(root)
    assert len(servers) == 2
    by = {f["id"]: f for f in m.check(servers, lock=None, lock_exists=False)}
    assert by["SUP-010"]["status"] == "must-fix"


def test_inline_cred_makes_sup013_must_fix():
    root = _write_repo(**{".mcp.json": json.dumps({"mcpServers": {
        "leaky": {"command": "npx", "args": ["-y", "@x/y@1.0.0"],
                  "env": {"API_KEY": "sk-livesecret0123456789abcdefabcd"}},
    }})})
    servers = m.discover(root)
    by = {f["id"]: f for f in m.check(servers, lock=None, lock_exists=False)}
    assert by["SUP-013"]["status"] == "must-fix"


def test_empty_repo_all_not_applicable():
    root = _write_repo(**{"README.md": "# nothing here"})
    findings = m.check(m.discover(root), lock=None, lock_exists=False)
    assert all(f["status"] == "not-applicable" for f in findings)


def test_assess_folds_counts_and_attaches_per_server_findings():
    root = _write_repo(**{".mcp.json": json.dumps({"mcpServers": {
        "loose": {"command": "npx", "args": ["-y", "@x/loose"]},
    }})})
    sbom, findings = m.assess(root)
    assert sbom["summary"]["must_fix"] >= 1
    assert "findings" in sbom["servers"][0]
    assert sbom["servers"][0]["findings"]["SUP-010"] == "must-fix"


def test_update_lock_roundtrips_through_diff():
    root = _write_repo(**{".mcp.json": json.dumps({"mcpServers": {
        "fs": {"command": "npx", "args": ["-y", "@x/fs@1.0.0"], "tools": [
            {"name": "read", "description": "r", "inputSchema": {}}]},
    }})})
    sbom = m.build_sbom(m.discover(root))
    lock = m.update_lock(sbom)
    # re-discovering + diffing against the fresh lock yields no drift
    srv = m.discover(root)[0]
    assert m.diff_lock(srv, lock["servers"]["fs"]) == []


# --- spec §6: malformed MCP config ---------------------------------------

def test_malformed_mcp_config_is_should_fix():
    # An unparseable .mcp.json must surface as a SUP-010 SHOULD-fix —
    # never a crash, never a must-fix.
    root = _write_repo(**{".mcp.json": "{ this is not valid json "})
    sbom, findings = m.assess(root)
    assert sbom["summary"]["must_fix"] == 0
    assert sbom["summary"]["should_fix"] >= 1
    broken = [s for s in sbom["servers"] if s.get("parse_error")]
    assert len(broken) == 1
    assert broken[0]["findings"]["SUP-010"] == "should-fix"
    by = {f["id"]: f for f in findings}
    assert by["SUP-010"]["status"] == "should-fix"


def test_malformed_non_mcp_json_is_ignored():
    # A broken package.json is NOT an MCP config → no diagnostic, no findings.
    root = _write_repo(**{"package.json": "{ broken "})
    assert m.discover(root) == []
    findings = m.check(m.discover(root), lock=None, lock_exists=False)
    assert all(f["status"] == "not-applicable" for f in findings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_mcp_sbom.py -v`
Expected: the new tests FAIL — `build_sbom`/`diff_lock`/`check`/`assess`/`update_lock` are not defined. (Task-1 tests still pass.)

- [ ] **Step 3: Append the implementation**

Append to `skills/threadlight-production-ready/scripts/mcp_sbom.py`:

```python
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
```

- [ ] **Step 3b: Complete the spec §6 malformed-config path**

Two small edits to `mcp_sbom.py` let an unparseable MCP config surface as a
`SUP-010` should-fix (spec §6) instead of being silently dropped, while a broken
non-MCP file (e.g. `package.json`) is still ignored.

First, add a module constant next to `_EMITTED` (top of the Discovery section):

```python
_MCP_CONFIG_NAMES = {"mcp.json", ".mcp.json"}
```

Then, in `_discover_json_configs`, replace the silent skip on bad JSON:

```python
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
```

with a name-gated diagnostic:

```python
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
```

The `_per_server_status` early-return added above turns any server carrying a
`parse_error` into a `SUP-010` should-fix (the other three findings
`not-applicable`), so `assess` counts it as `should_fix`, never `must_fix`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_mcp_sbom.py -v`
Expected: all PASS (Task-1 + Task-2 tests).

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/scripts/mcp_sbom.py \
        skills/threadlight-production-ready/tests/test_mcp_sbom.py
git commit -m "feat(production-ready): MCP SBOM build, lock diff, and findings

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 3: Producer — CLI entrypoint

**Files:**
- Modify: `skills/threadlight-production-ready/scripts/mcp_sbom.py` (append)
- Test: `skills/threadlight-production-ready/tests/test_mcp_sbom.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `skills/threadlight-production-ready/tests/test_mcp_sbom.py`:

```python
# --- CLI ------------------------------------------------------------------

def test_cli_writes_sbom_and_check_returns_1_on_must_fix(capsys):
    root = _write_repo(**{".mcp.json": json.dumps({"mcpServers": {
        "loose": {"command": "npx", "args": ["-y", "@x/loose"]},
    }})})
    out = root / "mcp-sbom.json"
    rc = m.main(["--root", str(root), "--out", str(out), "--check"])
    assert rc == 1  # unpinned -> SUP-010 must-fix
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["must_fix"] >= 1
    printed = capsys.readouterr().out
    assert "SUP-010" in printed


def test_cli_update_lock_then_check_is_clean(capsys):
    root = _write_repo(**{".mcp.json": json.dumps({"mcpServers": {
        "fs": {"command": "npx", "args": ["-y", "@x/fs@1.0.0"]},
    }})})
    out = root / "mcp-sbom.json"
    lock = root / "mcp-lock.json"
    m.main(["--root", str(root), "--out", str(out),
            "--lock", str(lock), "--update-lock"])
    assert lock.exists()
    rc = m.main(["--root", str(root), "--out", str(out),
                 "--lock", str(lock), "--check"])
    assert rc == 0  # pinned + lock present + no drift
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_mcp_sbom.py -k cli -v`
Expected: FAIL — `main` is not defined.

- [ ] **Step 3: Append the CLI**

Append to `skills/threadlight-production-ready/scripts/mcp_sbom.py`:

```python
# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Discover MCP servers/tools, emit an SBOM, and gate on drift.")
    ap.add_argument("--root", default=".", help="repo root to scan")
    ap.add_argument("--out", default="mcp-sbom.json", help="SBOM output path")
    ap.add_argument("--lock", default=None, help="path to mcp-lock.json")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any finding is must-fix")
    ap.add_argument("--update-lock", action="store_true",
                    help="(re)write the lock from the current MCP surface")
    args = ap.parse_args(argv)

    root = Path(args.root)
    sbom, findings = assess(root, lock_path=args.lock)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")

    if args.update_lock:
        lock_path = Path(args.lock) if args.lock else (root / "mcp-lock.json")
        lock_path.write_text(
            json.dumps(update_lock(sbom), indent=2) + "\n", encoding="utf-8")

    for f in findings:
        print(f"{f['id']}: {f['status']}")

    if args.check and any(f["status"] == "must-fix" for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_mcp_sbom.py -v`
Expected: all PASS (full producer suite).

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/scripts/mcp_sbom.py \
        skills/threadlight-production-ready/tests/test_mcp_sbom.py
git commit -m "feat(production-ready): MCP SBOM CLI (--check / --update-lock)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 4: Wire the producer into Pillar 09 (supply-chain)

**Files:**
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py`
  - `FINDING_CATALOG` — add SUP-010..013 after the `"SUP-009"` line (~807).
  - `RepoContext` — add `mcp_sbom` field after `resolved_posture: str = ""` (~1935).
  - Add module-level `_load_mcp_sbom()` + `_check_mcp_supply(ctx)` helpers (near `_check_supply_static`).
  - `_check_supply_static` — call `_check_mcp_supply` just before `return out` (~3451).
  - `main()` — write the `mcp-sbom.json` sidecar after the primary manifest write (~3393).
- Test: `skills/threadlight-production-ready/tests/test_mcp_supply_chain_pillar.py`

**Why a lazy import:** the assessor's own tests load scripts via `spec_from_file_location`, so `mcp_sbom` is NOT importable by name from `production_ready`. `_load_mcp_sbom` therefore inserts `scripts/` onto `sys.path` at call time and `importlib.import_module("mcp_sbom")`. If it fails for any reason, the four findings degrade to `not-verified` — the assessor never crashes on a producer error.

- [ ] **Step 1: Write the failing integration test**

Create `skills/threadlight-production-ready/tests/test_mcp_supply_chain_pillar.py`:

```python
"""Integration: MCP findings (SUP-010..013) surface through Pillar 09."""
import importlib.util
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py")
pr = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = pr
_spec.loader.exec_module(pr)


def _repo(**files: str) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp())
    (root / "specs").mkdir(parents=True, exist_ok=True)
    (root / "specs" / "SPEC.md").write_text("# spec\n", encoding="utf-8")
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _by_id(findings):
    return {f.id: f for f in findings}


def test_unpinned_mcp_server_surfaces_sup010_must_fix():
    root = _repo(**{".mcp.json": json.dumps({"mcpServers": {
        "loose": {"command": "npx", "args": ["-y", "@x/loose"]},
    }})})
    ctx = pr.RepoContext.from_repo(root, {})
    by = _by_id(pr._check_supply_static(ctx))
    assert by["SUP-010"].status == "must-fix"
    assert by["SUP-012"].status == "should-fix"   # no lock
    assert ctx.mcp_sbom is not None
    assert ctx.mcp_sbom["summary"]["server_count"] == 1


def test_inline_cred_surfaces_sup013_must_fix():
    root = _repo(**{".mcp.json": json.dumps({"mcpServers": {
        "leaky": {"command": "npx", "args": ["-y", "@x/y@1.0.0"],
                  "env": {"API_KEY": "sk-livesecret0123456789abcdefabcd"}},
    }})})
    ctx = pr.RepoContext.from_repo(root, {})
    by = _by_id(pr._check_supply_static(ctx))
    assert by["SUP-013"].status == "must-fix"


def test_no_mcp_servers_all_not_applicable():
    root = _repo(**{"README.md": "# nothing\n"})
    ctx = pr.RepoContext.from_repo(root, {})
    by = _by_id(pr._check_supply_static(ctx))
    for fid in ("SUP-010", "SUP-011", "SUP-012", "SUP-013"):
        assert by[fid].status == "not-applicable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_mcp_supply_chain_pillar.py -v`
Expected: FAIL — `SUP-010` not in findings / `ctx.mcp_sbom` attribute missing.

- [ ] **Step 3a: Add the four catalog entries**

In `production_ready.py`, find the `"SUP-009": {...}` line in `FINDING_CATALOG` and insert immediately AFTER it (before `"SUP-101"`):

```python
    "SUP-010": {"title": "MCP servers pinned to an exact version or image digest", "pillar": "supply-chain", "severity": "must-fix", "tier": 0},
    "SUP-011": {"title": "MCP servers resolve from a known registry or source", "pillar": "supply-chain", "severity": "should-fix", "tier": 0},
    "SUP-012": {"title": "mcp-lock.json committed and free of undocumented drift", "pillar": "supply-chain", "severity": "must-fix", "tier": 0},
    "SUP-013": {"title": "MCP server credentials are not committed inline", "pillar": "supply-chain", "severity": "must-fix", "tier": 0},
```

- [ ] **Step 3b: Add the RepoContext field**

Find `    resolved_posture: str = ""` inside the `RepoContext` dataclass and insert immediately after:

```python
    mcp_sbom: dict | None = None
```

- [ ] **Step 3c: Add the two helpers**

Insert these two module-level functions immediately ABOVE `def _check_supply_static(ctx: RepoContext) -> list[Finding]:`:

```python
def _load_mcp_sbom():
    """Import the sibling mcp_sbom producer lazily.

    Tests load these scripts via spec_from_file_location, so mcp_sbom is not on
    sys.path by name; insert the scripts dir at call time. Returns the module or
    None (never raises) so a producer problem degrades findings, not the run.
    """
    try:
        import importlib
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        return importlib.import_module("mcp_sbom")
    except Exception:
        return None


def _check_mcp_supply(ctx: RepoContext) -> list[Finding]:
    """Run the MCP producer and map its four findings into Pillar 09."""
    mod = _load_mcp_sbom()
    if mod is None:
        return [_not_verified(fid, "mcp_sbom producer unavailable")
                for fid in ("SUP-010", "SUP-011", "SUP-012", "SUP-013")]
    try:
        sbom, findings = mod.assess(ctx.root)
    except Exception as e:
        return [_not_verified(fid, f"mcp scan failed: {type(e).__name__}")
                for fid in ("SUP-010", "SUP-011", "SUP-012", "SUP-013")]
    ctx.mcp_sbom = sbom
    return [_mk_finding(f["id"], status=f["status"], detail=f["detail"])
            for f in findings]
```

- [ ] **Step 3d: Call the helper from `_check_supply_static`**

Find the `return out` at the end of `_check_supply_static` (right after the SUP-009 `else` block) and insert the call immediately before it:

```python
    out.extend(_check_mcp_supply(ctx))
    return out
```

- [ ] **Step 3e: Write the SBOM sidecar in `main()`**

Find the primary write block in `main()`:

```python
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out_manifest, indent=2) + "\n", encoding="utf-8")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_md = _render_report(out_manifest, posture, pillar_findings_waived, evidence_all, waivers, warnings)
        report_path.write_text(report_md, encoding="utf-8")
```

Insert the sidecar write immediately after `report_path.write_text(report_md, encoding="utf-8")` (still inside the `try`):

```python
        if getattr(ctx, "mcp_sbom", None) is not None:
            (out_path.parent / "mcp-sbom.json").write_text(
                json.dumps(ctx.mcp_sbom, indent=2) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_mcp_supply_chain_pillar.py -v`
Expected: all 3 PASS.

Also run the whole supply-chain surface to catch regressions:

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_skill_tool_supply_chain.py skills/threadlight-production-ready/tests/test_mcp_supply_chain_pillar.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py \
        skills/threadlight-production-ready/tests/test_mcp_supply_chain_pillar.py
git commit -m "feat(production-ready): surface MCP supply-chain findings in Pillar 09

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 5: Bump threadlight-production-ready 0.5.1 → 0.6.0

**Files:**
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py` (`VERSION`, ~488)
- Modify: `skills/threadlight-production-ready/SKILL.md` (frontmatter version ~19 + usage note)
- Test: `skills/threadlight-production-ready/tests/test_version.py` (rewrite for 0.6.0)

Note: `mcp_sbom.py` already ships `SBOM_VERSION = "0.6.0"` from Task 1 — no change there.

- [ ] **Step 1: Rewrite the version test to expect 0.6.0**

Replace the body of `skills/threadlight-production-ready/tests/test_version.py` with:

```python
"""Pin v0.6.0 version across script + SKILL.md frontmatter."""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


def test_version_is_060():
    assert mod.VERSION == "0.6.0", f"expected 0.6.0, got {mod.VERSION!r}"


def test_version_matches_skill_md():
    skill_md = (ROOT / "SKILL.md").read_text()
    assert 'version: "0.6.0"' in skill_md, "SKILL.md frontmatter must declare version: \"0.6.0\""


if __name__ == "__main__":
    test_version_is_060()
    test_version_matches_skill_md()
    print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_version.py -v`
Expected: FAIL — `VERSION` is still `0.5.1` and SKILL.md still declares `0.5.1`.

- [ ] **Step 3: Bump the code + frontmatter**

In `production_ready.py`, change:

```python
VERSION = "0.5.1"
```
to:
```python
VERSION = "0.6.0"
```

In `SKILL.md`, change the frontmatter line:

```yaml
  version: "0.5.1"
```
to:
```yaml
  version: "0.6.0"
```

Then add an MCP-gate usage note to `SKILL.md`. Find the section that documents the supply-chain pillar / SUP checks and add this paragraph (adjust the surrounding heading to match the file — keep the prose, it is standalone-merit):

```markdown
### MCP supply-chain gate (SUP-010..013)

When a repo declares MCP servers (a `.mcp.json`, an `mcpServers`/`servers` map in
any JSON config, or a remote MCP URL in source), the supply-chain pillar also
scores the MCP surface: servers must be **pinned** to an exact version or image
digest (SUP-010), resolve from a **known registry/source** (SUP-011), be tracked
in a committed **`mcp-lock.json`** so server/tool drift is reviewable (SUP-012),
and never commit **inline credentials** (SUP-013). The assessor writes an
`mcp-sbom.json` sidecar next to the manifest. Generate/refresh the lock with:

`python3 scripts/mcp_sbom.py --root . --update-lock`

Remediation points at `foundry-toolbox` (secret injection), Key Vault, and ACR —
this hardens how you consume MCP on the platform; it does not replace it.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest skills/threadlight-production-ready/tests/test_version.py -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py \
        skills/threadlight-production-ready/SKILL.md \
        skills/threadlight-production-ready/tests/test_version.py
git commit -m "chore(production-ready): bump to 0.6.0 and document MCP gate

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 6: Pillar 09 + skill-tool reference docs

**Files:**
- Modify: `skills/threadlight-production-ready/references/pillars/09-supply-chain.md`
- Modify: `skills/threadlight-production-ready/references/skill-tool-supply-chain.md`

This task is docs-only (no test). Content ships on standalone merit.

- [ ] **Step 1: Add the four rows to the static-tier table**

In `09-supply-chain.md`, find the `SUP-009` table row and insert these four rows immediately after it (before the `### Live (tier 1)` heading):

```markdown
| `SUP-010` | If the repo declares MCP servers, each is **pinned** to an exact version (npx/uvx/pip `==`) or image **digest** (`@sha256:...`) — no floating tags | `must-fix` if unpinned; `not-applicable` if no MCP servers |
| `SUP-011` | Each MCP server resolves from a **known registry or source** (npm, PyPI, a named container registry, or an explicit remote URL) | `should-fix` if unresolvable |
| `SUP-012` | An **`mcp-lock.json`** is committed and matches the current MCP server/tool surface (versions, digests, tool descriptor + input-schema hashes) | `must-fix` on pinned-server drift; `should-fix` if absent or unpinned drift |
| `SUP-013` | No MCP server config commits **inline credentials** (api keys / tokens / connection strings in `env` or `headers`) — use injected secrets | `must-fix` if found |
```

- [ ] **Step 2: Add the MCP section**

In `09-supply-chain.md`, immediately after the `## Skill & tool artifacts (SUP-008 / SUP-009)` section (after its closing paragraph, before `## Remediation`), insert:

```markdown
## MCP servers & tools (SUP-010 / SUP-011 / SUP-012 / SUP-013)

Model Context Protocol servers are executable supply chain that an agent calls
at runtime, and their **tool descriptors** (name, description, input schema) are
part of the prompt the model acts on — a silent change to a tool's description
is a supply-chain event, not a cosmetic one. Threadlight discovers MCP servers
from `.mcp.json`, any `mcpServers` / `servers` map, and remote MCP URLs in
source, then emits an **`mcp-sbom.json`**: kind, pinned version/digest, resolved
registry, and a SHA-256 of every tool's description and input schema.

Pin every server to an exact version or image digest (SUP-010) from a known
registry (SUP-011); commit an **`mcp-lock.json`** so any drift in a server
version, image digest, or **tool descriptor** is reviewed like a dependency bump
(SUP-012); and never inline credentials — inject them (SUP-013). Regenerate the
lock with `python3 scripts/mcp_sbom.py --root . --update-lock`. This is the same
"pin it, lock it, review the diff" discipline SUP-001..009 apply to images and
skills, extended to the MCP surface.
```

- [ ] **Step 3: Add remediation rows**

In `09-supply-chain.md`, find the `## Remediation` table and append these rows to it:

```markdown
| Pin MCP servers + commit `mcp-lock.json` | `foundry-toolbox`, `azd-patterns` |
| Inject MCP credentials (no inline secrets) | `foundry-toolbox`, Key Vault |
```

- [ ] **Step 4: Add an MCP paragraph to the skill-tool reference**

In `skill-tool-supply-chain.md`, append this section to the end of the file:

```markdown
## MCP servers & tools

MCP servers are supply chain too. Treat each server like a pinned dependency:
reference it by an exact version or image **digest** from a known registry, and
commit an **`mcp-lock.json`** that records each server's version/digest and a
hash of every tool's **description** and **input schema**. Because the model acts
on tool descriptors, an unreviewed change to a tool's description or schema is a
capability change — the lock makes it show up as a reviewable diff (finding
`SUP-012`) instead of a silent one.

Workflow:

1. Declare servers in `.mcp.json` (or any `mcpServers` / `servers` map).
2. Pin each server (`@1.2.3`, `==1.2.3`, or `@sha256:...`) — never `latest`.
3. Inject credentials via `foundry-toolbox` / Key Vault; never inline them.
4. Generate the lock: `python3 scripts/mcp_sbom.py --root . --update-lock`.
5. Commit `mcp-lock.json`. In CI, `--check` fails the build on undocumented
   drift of a pinned server.
```

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-production-ready/references/pillars/09-supply-chain.md \
        skills/threadlight-production-ready/references/skill-tool-supply-chain.md
git commit -m "docs(production-ready): document MCP supply-chain findings

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 7: cicd generator — add the `--mcp-gate` knob

**Files:**
- Modify: `skills/threadlight-cicd/scripts/generate_pipeline.py`
  - compute `mcp_gate_mode` / `mcp_gate_soft` (after the eval-gate compute, ~200)
  - add `MCP_GATE_MODE` / `MCP_GATE_SOFT` to the CONTEXT dict (after ~242)
  - add `--mcp-gate` argparse arg (after `--eval-gate`, ~423)
  - add `"mcp_gate": args.mcp_gate` to the `_framing_from_args` cli dict (after ~448)

This task adds the generator plumbing only; the template gate arrives in Task 8. The `test_mcp_gate.py` test is written in Task 8 (it needs the template too). No standalone test here — Task 8 verifies end-to-end.

- [ ] **Step 1: Compute the gate mode**

Find the eval-gate compute block and add the MCP compute immediately after `eval_gate_soft = "true" if eval_gate_mode == "soft" else "false"`:

```python
    mcp_gate_mode = str(framing.get("mcp_gate", "soft")).lower()
    if mcp_gate_mode not in ("soft", "hard"):
        mcp_gate_mode = "soft"
    mcp_gate_soft = "true" if mcp_gate_mode == "soft" else "false"
```

- [ ] **Step 2: Expose it in the render CONTEXT**

Find the CONTEXT dict entries `"EVAL_GATE_MODE": eval_gate_mode,` / `"EVAL_GATE_SOFT": eval_gate_soft,` and add immediately after them:

```python
        "MCP_GATE_MODE": mcp_gate_mode,
        "MCP_GATE_SOFT": mcp_gate_soft,
```

- [ ] **Step 3: Add the CLI arg**

Find the `--eval-gate` argparse block and add immediately after it (before `--out`):

```python
    p.add_argument("--mcp-gate", choices=["soft", "hard"], default=None,
                   help="CI/CD MCP supply-chain gate mode: soft (warn-only, "
                        "default) or hard (block the pipeline on any must-fix "
                        "MCP finding).")
```

- [ ] **Step 4: Thread it through `_framing_from_args`**

Find `"eval_gate": args.eval_gate,` in the `cli` dict and add immediately after it:

```python
        "mcp_gate": args.mcp_gate,
```

- [ ] **Step 5: Sanity-check the module still imports**

Run: `python3 -c "import importlib.util,pathlib; s=importlib.util.spec_from_file_location('g', pathlib.Path('skills/threadlight-cicd/scripts/generate_pipeline.py')); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('import ok')"`
Expected: `import ok`

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-cicd/scripts/generate_pipeline.py
git commit -m "feat(cicd): add --mcp-gate knob (soft|hard) to the generator

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 8: cicd templates — render the MCP gate + test it

**Files:**
- Modify: `skills/threadlight-cicd/references/github-actions/azd-deploy-prod.yml.tmpl` (append after line 148 / EOF)
- Modify: `skills/threadlight-cicd/references/azure-devops/azure-pipelines.yml.tmpl` (append after line 156 / EOF)
- Test: `skills/threadlight-cicd/tests/test_mcp_gate.py`

**Critical:** a token added to a template MUST be present in the CONTEXT dict in the SAME commit, or the render leaves `{{...}}` and `assert "{{" not in ...` fails. Task 7 already added `MCP_GATE_MODE` / `MCP_GATE_SOFT`, so only those two tokens may appear in the new template blocks.

- [ ] **Step 1: Write the failing test**

Create `skills/threadlight-cicd/tests/test_mcp_gate.py`:

```python
"""The generated production pipeline carries an MCP supply-chain gate after
deploy that enforces the mcp-sbom summary (soft = warn-only, hard = block on a
must-fix MCP finding). OIDC/WIF only; no secret; no unrendered tokens."""
import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "generate_pipeline", ROOT / "scripts" / "generate_pipeline.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["generate_pipeline"] = mod
_spec.loader.exec_module(mod)


def _gh_framing(**over):
    f = {
        "platform": "github-actions",
        "target_subscription_id": "11111111-1111-1111-1111-111111111111",
        "target_resource_group": "rg-pilot-prod",
        "target_location": "eastus2",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "repo_full_name": "aiappsgbb/contoso-pilot",
        "env_name": "prod",
    }
    f.update(over)
    return f


def _ado_framing(**over):
    f = {
        "platform": "azure-devops",
        "target_subscription_id": "11111111-1111-1111-1111-111111111111",
        "target_resource_group": "rg-pilot-prod",
        "target_location": "eastus2",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "ado_org": "contoso",
        "ado_project": "AI-Pilots",
        "ado_service_connection": "sc-contoso-pilot-prod",
        "env_name": "prod",
    }
    f.update(over)
    return f


def _gh_workflow(framing):
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(framing, out_root=tmp)
    return (tmp / ".github/workflows/azd-deploy-prod.yml").read_text()


def _ado_pipeline(framing):
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(framing, out_root=tmp)
    return (tmp / "azure-pipelines.yml").read_text()


# --- GitHub Actions -------------------------------------------------------

def test_github_has_mcp_gate_after_deploy():
    wf = _gh_workflow(_gh_framing())
    assert "mcp-supply-chain-gate:" in wf
    assert "needs: deploy" in wf
    assert wf.index("deploy:") < wf.index("mcp-supply-chain-gate:")


def test_github_mcp_gate_reads_the_sbom_sidecar():
    wf = _gh_workflow(_gh_framing())
    assert "tests/mcp-sbom.json" in wf
    assert "must_fix" in wf


def test_github_mcp_gate_oidc_and_no_tokens():
    wf = _gh_workflow(_gh_framing())
    assert "azure/login@" in wf
    assert "{{" not in wf


def test_github_mcp_soft_default_and_hard_block():
    soft = _gh_workflow(_gh_framing())
    assert "mode=soft" in soft
    hard = _gh_workflow(_gh_framing(mcp_gate="hard"))
    assert "mode=hard" in hard
    assert "continue-on-error: false" in hard


# --- Azure DevOps ---------------------------------------------------------

def test_ado_has_mcp_gate_stage_after_deploy():
    pipe = _ado_pipeline(_ado_framing())
    assert "mcp_supply_chain_gate" in pipe
    assert "dependsOn: deploy" in pipe
    assert pipe.index("stage: deploy") < pipe.index("mcp_supply_chain_gate")


def test_ado_mcp_gate_reads_the_sbom_sidecar():
    pipe = _ado_pipeline(_ado_framing())
    assert "tests/mcp-sbom.json" in pipe
    assert "must_fix" in pipe


def test_ado_mcp_soft_default_and_hard_block():
    soft = _ado_pipeline(_ado_framing())
    assert "mode=soft" in soft
    assert "{{" not in soft
    hard = _ado_pipeline(_ado_framing(mcp_gate="hard"))
    assert "mode=hard" in hard
    assert "continueOnError: false" in hard


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"OK   {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest skills/threadlight-cicd/tests/test_mcp_gate.py -v`
Expected: FAIL — no `mcp-supply-chain-gate:` / `mcp_supply_chain_gate` in the rendered output yet.

- [ ] **Step 3: Append the GitHub Actions gate job**

Append to the END of `skills/threadlight-cicd/references/github-actions/azd-deploy-prod.yml.tmpl` (after the final `          PY` at line 148). Note the leading blank line:

```yaml

  # MCP supply-chain gate — verify the deployed repo's MCP servers/tools are
  # pinned, registry-resolvable, lock-tracked, and credential-clean before it
  # ships. Gate mode: {{MCP_GATE_MODE}} (soft = warn-only, hard = block on any
  # must-fix MCP finding). Reads the mcp-sbom.json the production-ready assessor
  # writes next to its manifest (tests/mcp-sbom.json).
  mcp-supply-chain-gate:
    needs: deploy
    runs-on: {{RUNNER_RUNS_ON}}
    environment: {{ENV_NAME}}
    env:
      AZURE_ENV_NAME: {{ENV_NAME}}
    steps:
      - uses: actions/checkout@v4

      - name: Azure login (OIDC federated credentials)
        uses: azure/login@v2
        with:
          client-id: {{AZURE_CLIENT_ID}}
          tenant-id: {{TENANT_ID}}
          subscription-id: {{TARGET_SUBSCRIPTION_ID}}

      - name: Produce MCP SBOM (threadlight-production-ready)
        run: >
          echo "Run threadlight-production-ready against the repo to (re)produce
          tests/mcp-sbom.json — MCP server/tool discovery, pin + registry
          verification, mcp-lock.json drift, and inline-credential scan."

      - name: Enforce MCP supply-chain verdict (mode={{MCP_GATE_MODE}})
        continue-on-error: {{MCP_GATE_SOFT}}
        run: |
          python3 - <<'PY'
          import json, os, sys
          p = "tests/mcp-sbom.json"
          if not os.path.exists(p):
              print(f"::warning::{p} missing — MCP SBOM has not been produced.")
              sys.exit(1)
          summary = json.load(open(p)).get("summary", {})
          must_fix = summary.get("must_fix", 0)
          print(f"MCP must-fix findings: {must_fix}")
          sys.exit(1 if must_fix else 0)
          PY
```

- [ ] **Step 4: Append the Azure DevOps gate stage**

Append to the END of `skills/threadlight-cicd/references/azure-devops/azure-pipelines.yml.tmpl` (after the final `PY` line). Note the leading blank line:

```yaml

  # MCP supply-chain gate — see the GitHub template for rationale.
  # Gate mode: {{MCP_GATE_MODE}} (soft = warn-only, hard = block on must-fix).
  - stage: mcp_supply_chain_gate
    displayName: MCP supply-chain gate ({{MCP_GATE_MODE}})
    dependsOn: deploy
    jobs:
      - job: mcp_supply_chain
        steps:
          - checkout: self
          - task: AzureCLI@2
            displayName: Produce MCP SBOM (threadlight-production-ready)
            inputs:
              azureSubscription: {{ADO_SERVICE_CONNECTION}}
              scriptType: bash
              scriptLocation: inlineScript
              inlineScript: |
                set -euo pipefail
                echo "Run threadlight-production-ready against the repo to \
                  (re)produce tests/mcp-sbom.json — MCP server/tool discovery, \
                  pin + registry verification, mcp-lock.json drift, and \
                  inline-credential scan."
          - task: AzureCLI@2
            displayName: Enforce MCP supply-chain verdict (mode={{MCP_GATE_MODE}})
            continueOnError: {{MCP_GATE_SOFT}}
            inputs:
              azureSubscription: {{ADO_SERVICE_CONNECTION}}
              scriptType: bash
              scriptLocation: inlineScript
              inlineScript: |
                set -euo pipefail
                python3 - <<'PY'
                import json, os, sys
                p = "tests/mcp-sbom.json"
                if not os.path.exists(p):
                    print(f"##vso[task.logissue type=warning]{p} missing — MCP SBOM has not been produced.")
                    sys.exit(1)
                summary = json.load(open(p)).get("summary", {})
                must_fix = summary.get("must_fix", 0)
                print(f"MCP must-fix findings: {must_fix}")
                sys.exit(1 if must_fix else 0)
                PY
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest skills/threadlight-cicd/tests/test_mcp_gate.py -v`
Expected: all PASS.

Also re-run the existing gate test to confirm no regression:

Run: `python3 -m pytest skills/threadlight-cicd/tests/test_eval_gate.py skills/threadlight-cicd/tests/test_mcp_gate.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-cicd/references/github-actions/azd-deploy-prod.yml.tmpl \
        skills/threadlight-cicd/references/azure-devops/azure-pipelines.yml.tmpl \
        skills/threadlight-cicd/tests/test_mcp_gate.py
git commit -m "feat(cicd): render MCP supply-chain gate stage in prod pipelines

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 9: Bump threadlight-cicd 0.2.1 → 0.3.0

**Files:**
- Modify: `skills/threadlight-cicd/scripts/generate_pipeline.py` (`VERSION`, line 31)
- Modify: `skills/threadlight-cicd/SKILL.md` (frontmatter version + knob doc)

The cicd `test_version.py` asserts `VERSION == SKILL.md frontmatter` (equality, not a hardcoded literal) — so bump both and re-run; no test edit needed.

- [ ] **Step 1: Confirm the version test is an equality check**

Run: `python3 -m pytest skills/threadlight-cicd/tests/test_version.py -v`
Expected: PASS at the current 0.2.1 (baseline).

- [ ] **Step 2: Bump the code + frontmatter**

In `generate_pipeline.py`, change:

```python
VERSION = "0.2.1"
```
to:
```python
VERSION = "0.3.0"
```

In `skills/threadlight-cicd/SKILL.md`, change the frontmatter line:

```yaml
  version: "0.2.1"
```
to:
```yaml
  version: "0.3.0"
```

- [ ] **Step 3: Document the knob in SKILL.md**

Add this note to `SKILL.md` near where the `--eval-gate` knob / gate stages are described (keep the prose standalone-merit):

```markdown
### `--mcp-gate soft|hard`

Adds an **MCP supply-chain gate** to the generated production pipeline, after
deploy, alongside the eval and red-team gates. It enforces the `mcp-sbom.json`
that `threadlight-production-ready` writes (`tests/mcp-sbom.json`): `soft`
(default) warns only and keeps the pipeline green; `hard` blocks the pipeline on
any must-fix MCP finding (an unpinned server, undocumented lock drift, or an
inline credential). OIDC / WIF only — no secret.
```

- [ ] **Step 4: Re-run the version test**

Run: `python3 -m pytest skills/threadlight-cicd/tests/test_version.py -v`
Expected: PASS at 0.3.0 (equality holds).

- [ ] **Step 5: Commit**

```bash
git add skills/threadlight-cicd/scripts/generate_pipeline.py \
        skills/threadlight-cicd/SKILL.md
git commit -m "chore(cicd): bump to 0.3.0 and document --mcp-gate knob

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 10: Repo metadata — plugin manifests + CHANGELOG

**Files:**
- Modify: `plugin.json` (version line 3 + keywords)
- Modify: `.github/plugin/marketplace.json` (version lines 9 + 15)
- Modify: `CHANGELOG.md` (`[Unreleased]` section)

This is metadata-only. No dedicated test; the finalize task re-runs the plugin-manifest / description-length CI guards.

- [ ] **Step 1: Bump plugin.json + add MCP keywords**

In `plugin.json`, change `"version": "1.6.0",` (line 3) to `"version": "1.7.0",`.

Then add four MCP keywords into the `keywords` array (insert after `"production-readiness",` at ~line 27):

```json
    "mcp-supply-chain",
    "mcp-sbom",
    "tool-descriptor-drift",
    "mcp-pinning",
```

- [ ] **Step 2: Bump marketplace.json**

In `.github/plugin/marketplace.json`, change BOTH `"version": "1.6.0"` occurrences (line 9 in `metadata`, line 15 in `plugins[0]`) to `"version": "1.7.0"`.

- [ ] **Step 3: Add a CHANGELOG entry**

In `CHANGELOG.md`, under `## [Unreleased]` → `### Changed`, add this bullet as the first item:

```markdown
- **Added an MCP supply-chain gate** across `threadlight-production-ready`
  (0.6.0) and `threadlight-cicd` (0.3.0). The production-readiness assessor now
  discovers MCP servers/tools declared in a repo, writes an `mcp-sbom.json`
  sidecar, and scores four new supply-chain findings — servers pinned to a
  version/digest (`SUP-010`), resolvable from a known registry (`SUP-011`),
  tracked in a committed `mcp-lock.json` free of undocumented server/tool drift
  (`SUP-012`), and free of inline credentials (`SUP-013`). The CI/CD generator
  gains a `--mcp-gate soft|hard` knob that adds a post-deploy gate enforcing the
  SBOM. Remediation points at `foundry-toolbox`, Key Vault, and ACR. `plugin.json`
  and `.github/plugin/marketplace.json` bump to `1.7.0` with MCP keywords.
```

- [ ] **Step 4: Verify the manifests still parse**

Run: `python3 -c "import json; json.load(open('plugin.json')); json.load(open('.github/plugin/marketplace.json')); print('json ok')"`
Expected: `json ok`

- [ ] **Step 5: Commit**

```bash
git add plugin.json .github/plugin/marketplace.json CHANGELOG.md
git commit -m "chore: bump plugin to 1.7.0 and log the MCP supply-chain gate

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 11: Finalize — full verification, scrub, PR, follow-up issue

**Files:** none created; this is verification + publication.

- [ ] **Step 1: Run the full per-skill test suites**

The CI runs pytest per-skill (avoids duplicate-basename collisions). Run both touched skills in full:

Run:
```bash
python3 -m pytest skills/threadlight-production-ready/tests/ -v
python3 -m pytest skills/threadlight-cicd/tests/ -v
```
Expected: production-ready — all PASS except the 2 pre-existing stale-fixture e2e fails (baseline, `continue-on-error` in CI; NOT caused by this work). cicd — all PASS.

- [ ] **Step 2: Run the repo CI guards**

Run:
```bash
python3 scripts/ci/check-skill-description-length.py
```
Expected: all SKILL.md descriptions ≤ 1024 (this work does not touch frontmatter `description` fields, so the count is unchanged and green).

- [ ] **Step 3: Documentation-hygiene review of the whole branch**

Review the full branch diff and confirm every changed line reads as standalone
product documentation on its own merit:

```bash
git diff main...HEAD | grep '^+' | less
```

Confirm the diff contains only product/feature content (findings, code, docs,
version bumps). Product "vs" phrasing that is genuinely part of the product
(PAYG vs PTU, soft vs hard gate mode) is fine. If anything reads as out-of-place
for public product docs, remove it and amend the offending commit before
proceeding.

- [ ] **Step 4: Draft the awesome-gbb follow-up issue (scratch only — NOT committed)**

Write the issue body to the session scratch dir (never into the repo):

`/Users/ricchi/.copilot/session-state/4fb2c69e-dfa9-4656-9328-398a3f07983e/files/awesome-gbb-mcp-provenance-issue.md`

Body (standalone-merit; proposes a reusable MCP-provenance primitive for the broader catalog):

```markdown
**Title:** Add a reusable MCP provenance/SBOM primitive for Foundry agent repos

**Problem.** Agent repos increasingly wire in MCP servers (npx/uvx/docker/remote),
but there's no shared, offline way to answer "what MCP servers and tools does this
repo trust, are they pinned, and did a tool's description or input schema change
since we last reviewed it?" Tool descriptors are part of the prompt the model acts
on, so an unreviewed descriptor change is a supply-chain event.

**Proposal.** A small, pure-stdlib producer that discovers MCP servers from
`.mcp.json` / `mcpServers` maps / remote URLs, emits an `mcp-sbom.json` (kind,
pinned version/digest, resolved registry, per-tool description + input-schema
hashes), and diffs against a committed `mcp-lock.json` to make drift reviewable.
Remediation points at `foundry-toolbox` (secret injection), Key Vault, and ACR so
teams harden how they consume MCP on the platform.

**Why here.** Useful to any Foundry agent repo, not just one pipeline — a natural
companion to the existing skill/tool governance guidance.
```

- [ ] **Step 5: Open the PR (off merged main `8ed508f`)**

Push and open the PR:

```bash
git push -u origin part2-mcp-supply-chain-gate
gh pr create --repo aiappsgbb/threadlight-skills \
  --title "feat: MCP supply-chain gate (SBOM + lock drift + CI gate)" \
  --body "$(cat <<'BODY'
## What

Adds an **MCP supply-chain gate** to Threadlight so a repo's MCP servers and
tools are governed like any other dependency.

- **New producer** `mcp_sbom.py` (pure stdlib) — discovers MCP servers from
  `.mcp.json`, `mcpServers`/`servers` maps, and remote MCP URLs; emits
  `mcp-sbom.json` (kind, pinned version/digest, resolved registry, per-tool
  description + input-schema SHA-256); diffs against a committed `mcp-lock.json`.
- **Pillar 09 wiring** — four new findings: `SUP-010` (pinned), `SUP-011`
  (known registry), `SUP-012` (lock committed + drift-free), `SUP-013` (no inline
  credentials). The assessor writes the SBOM sidecar next to its manifest.
- **CI/CD knob** `--mcp-gate soft|hard` — adds a post-deploy gate (GitHub Actions
  + Azure DevOps) that enforces the SBOM's must-fix count.
- Remediation points at `foundry-toolbox`, Key Vault, and ACR — this hardens how
  teams consume MCP on the platform; it does not replace it.

## Versions

- `threadlight-production-ready` 0.5.1 → 0.6.0
- `threadlight-cicd` 0.2.1 → 0.3.0
- plugin `1.6.0` → `1.7.0`

## Tests

New: `test_mcp_sbom.py`, `test_mcp_supply_chain_pillar.py`, `test_mcp_gate.py`.
Full per-skill suites green (production-ready carries 2 pre-existing
stale-fixture e2e fails, unrelated to this change).
BODY
)"
```

- [ ] **Step 6: File the awesome-gbb issue (NOT committed to any repo)**

```bash
gh issue create --repo aiappsgbb/awesome-gbb \
  --title "Add a reusable MCP provenance/SBOM primitive for Foundry agent repos" \
  --body-file /Users/ricchi/.copilot/session-state/4fb2c69e-dfa9-4656-9328-398a3f07983e/files/awesome-gbb-mcp-provenance-issue.md
```

- [ ] **Step 7: Update the session plan**

Update the session task tracker (`p2a-build` → done) and note the PR number + issue number in the session `plan.md`. Report the PR URL and issue URL back to the user.

---

## Notes for the executing engineer

- **`python` is not on PATH — always `python3`.** Tests are bare `test_*` functions; run with `python3 -m pytest <path> -v`.
- **Every commit needs the trailer** `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- **Review before every commit**, not just at the end (Step 3). Every changed line ships as standalone product content on its own merit.
- **Producer is import-direction-safe:** `production_ready.py` imports `mcp_sbom` lazily; `mcp_sbom.py` NEVER imports `production_ready`.
- **Known v1 limitations (acceptable, documented):** `_detect_inline_creds` reads only `cfg["env"]` / `cfg["headers"]`, not `docker run -e KEY=VAL` inside `args`; `_is_exact_version` accepts anything starting with a digit that isn't a known range token (e.g. `1.x` is treated as a range and rejected, but exotic specifiers are best-effort). These are fine for v1 — only the tested cases must pass.

## Intentional refinements vs. the approved spec

These deliberate deviations from `docs/superpowers/specs/2026-07-02-mcp-supply-chain-gate-design.md` keep v1 simpler and internally consistent. They are choices, not gaps:

- **Test fixtures (spec §7):** the spec calls for on-disk `tests/fixtures/` dirs. This plan uses an inline `_write_repo(**files)` temp-dir helper instead — same coverage, zero fixture files to maintain, and each test reads as a self-contained repo.
- **SBOM JSON shape (spec §5.1):** the emitted `mcp-sbom.json` flattens the server `source` fields to the top level (rather than nesting under `"source"`), emits `declared_in` as a single string, attaches per-server `findings` as a `{id: status}` map (rather than an array of `"ID:status"` strings), and uses top-level keys `schema_version` / `generator` / `generator_version` with a summary of `{server_count, pinned, unpinned, remote, inline_creds, must_fix, should_fix}`. **The gate contract `summary.must_fix` / `summary.should_fix` is preserved exactly** (that is all the CI gate and the assessor read), so downstream consumers are unaffected; a later capstone can adapt keys if it needs the nested form.
- **SUP-011 remote hosts (spec §4.3):** the spec mentions an "allowlisted remote host". v1 has no allowlist infrastructure, so any remote whose URL host parses is treated as a known source (registry = host ≠ `unknown` → pass). Tightening to an explicit allowlist is future work.
- **Live tier `SUP-110` (spec §3 D7 / §9):** deferred, not built — as the spec directs.
- **Drift + duplicate-id hardening (applied during execution):** `diff_lock` also compares the locked `kind` and `ref` (guarded with `is not None` for legacy locks) so a package-identity swap at the same version string is caught, not just a version/digest change. `check` / `assess` iterate per-server status positionally (a list of `(server, status)` / a status list zipped with `sbom["servers"]`) rather than keying by `server.id`, so two config files declaring the same id can no longer mask one another; `offenders` is a sorted de-duplicated set. Lock lookup inside `_per_server_status` stays id-keyed (an accepted v1 limitation).

