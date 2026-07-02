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
