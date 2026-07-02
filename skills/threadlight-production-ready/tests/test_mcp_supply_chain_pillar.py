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
