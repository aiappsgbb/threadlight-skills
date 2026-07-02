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


def test_docker_platform_flag_does_not_swallow_image():
    # --platform takes a value; the image (with digest) must still be found.
    root = _write_repo(**{
        ".mcp.json": json.dumps({"mcpServers": {
            "plat": {"command": "docker", "args": [
                "run", "--rm", "--platform", "linux/amd64",
                "ghcr.io/acme/mcp@sha256:" + "a" * 64,
            ]},
        }}),
    })
    servers = _by_id(m.discover(root))
    s = servers["plat"]
    assert s.kind == "docker"
    assert s.ref == "ghcr.io/acme/mcp@sha256:" + "a" * 64
    assert s.pinned is True
    assert s.digest == "sha256:" + "a" * 64
    assert s.registry == "ghcr.io"


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
