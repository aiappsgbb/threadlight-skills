"""Issue #134: the workspace generation procedure must match what ships.

SKILL.md Step 2 copies ``references/shapes/{shape}/`` into ``src/workspace/``.
These checks keep the shape availability index, the reference table and the
generation procedure honest: a shape marked runnable must have a working
entrypoint and assets, and every other shape must be generated from the spec
(never reported as reused). The smoke test serves a copy of each runnable
shape from a local static server; it is offline and is not browser, hosted
or governance evidence.
"""
from __future__ import annotations

import functools
import http.server
import json
import re
import shutil
import subprocess
import threading
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL_MD = SKILL_DIR / "SKILL.md"
REFS = SKILL_DIR / "references"
SHAPES = REFS / "shapes"
PANELS = REFS / "hitl-panels"
CATALOG_SHAPES = {"case-list", "inbox", "dashboard", "console", "kanban", "map", "chat"}
PANEL_FILES = ("decision-pane.html", "action-toolbar.html", "audit-viewer.html")
RUNNABLE = "runnable reference"
NOT_SHIPPED = "not shipped"


def _skill() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def shape_index() -> dict[str, dict[str, str]]:
    match = re.search(
        r"<!-- threadlight:shape-index -->(.*?)<!-- /threadlight:shape-index -->", _skill(), re.S
    )
    assert match, "SKILL.md must carry a machine-readable shape availability index"
    rows = {}
    for line in match.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or not cells[0].startswith("`"):
            continue
        shape, availability, reference, entrypoint = cells
        rows[shape.strip("`")] = {
            "availability": availability,
            "reference": reference.strip("`"),
            "entrypoint": entrypoint.strip("`"),
        }
    return rows


def runnable_shapes() -> list[str]:
    try:
        index = shape_index()
    except AssertionError:
        return ["case-list"]
    return sorted(s for s, row in index.items() if row["availability"].startswith(RUNNABLE))


class _Assets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.refs.append(attrs["src"])
        if tag == "link" and attrs.get("href"):
            self.refs.append(attrs["href"])
        if tag == "img" and attrs.get("src"):
            self.refs.append(attrs["src"])


def _local_refs(shape_dir: Path) -> list[str]:
    parser = _Assets()
    parser.feed((shape_dir / "index.html").read_text(encoding="utf-8"))
    refs = [r for r in parser.refs if not r.startswith("data:")]
    for js in sorted(shape_dir.rglob("*.js")):
        refs += re.findall(r"""['"]\./((?:data|components|assets)/[\w./-]+)['"]""", js.read_text(encoding="utf-8"))
    return refs


def test_index_lists_every_catalog_shape_with_a_defined_availability():
    index = shape_index()
    assert set(index) == CATALOG_SHAPES
    for shape, row in index.items():
        assert row["availability"].startswith((RUNNABLE, NOT_SHIPPED)), shape
    assert runnable_shapes(), "at least one shape must ship a runnable reference"
    assert index["case-list"]["availability"].startswith(RUNNABLE)


@pytest.mark.parametrize("shape", sorted(CATALOG_SHAPES))
def test_index_matches_the_files_on_disk(shape):
    row = shape_index()[shape]
    directory = SHAPES / shape
    if row["availability"].startswith(RUNNABLE):
        assert row["reference"] == f"references/shapes/{shape}/"
        entry = directory / row["entrypoint"]
        assert entry.is_file(), f"{shape}: advertised runnable but {entry} is missing"
        assert (directory / "README.md").is_file()
    else:
        assert not (directory / "index.html").exists(), f"{shape} ships index.html but is marked {row}"
        assert row["reference"] in {"—", "-"}


def test_generation_procedure_is_capability_aware():
    text = _flat(_skill())
    step2 = text[text.index("### Step 2"):text.index("### Step 3")]
    assert "Each shape ships a polished" not in step2
    assert "shape availability index" in step2.lower()
    assert "generate" in step2.lower() and "from the spec" in step2.lower()
    assert "never" in step2.lower() and "reuse" in step2.lower()
    for shape in CATALOG_SHAPES - set(runnable_shapes()):
        assert f"`references/shapes/{shape}/` | Polished" not in text


def test_reference_table_and_closing_note_agree_with_the_index():
    text = _skill()
    refs = text[text.index("## Reference files"):text.index("## Anti-patterns")]
    for shape in CATALOG_SHAPES - set(runnable_shapes()):
        assert f"references/shapes/{shape}/` | Polished" not in refs
    assert "seeded as empty placeholders" not in refs
    for shape in runnable_shapes():
        assert f"`references/shapes/{shape}/`" in refs


def _referenced_paths(markdown: str) -> set[str]:
    paths = set(re.findall(r"`(references/[\w./-]+)`", markdown))
    return {p for p in paths if "{" not in p}


@pytest.mark.parametrize(
    "document", [SKILL_MD, SHAPES / "README.md", PANELS / "README.md"], ids=lambda p: str(p.relative_to(SKILL_DIR))
)
def test_referenced_reference_paths_exist(document):
    missing = sorted(p for p in _referenced_paths(document.read_text(encoding="utf-8"))
                     if not (SKILL_DIR / p).exists())
    assert not missing, f"{document.relative_to(SKILL_DIR)} references missing paths: {missing}"


@pytest.mark.parametrize("shape", runnable_shapes())
def test_runnable_shape_assets_exist_and_are_offline(shape):
    directory = SHAPES / shape
    refs = _local_refs(directory)
    assert refs, f"{shape}: index.html references no assets"
    for ref in refs:
        assert not re.match(r"^(https?:)?//", ref), f"{shape}: external asset {ref}"
        assert (directory / ref).is_file(), f"{shape}: missing asset {ref}"
    for path in directory.rglob("*"):
        if path.suffix in {".html", ".js", ".css"}:
            assert not re.search(r"""(src|href)=["']https?://""", path.read_text(encoding="utf-8")), path


@pytest.mark.parametrize("shape", runnable_shapes())
def test_runnable_shape_carries_identical_hitl_panels(shape):
    for name in PANEL_FILES:
        copy = SHAPES / shape / "components" / name
        assert copy.is_file(), f"{shape}: missing components/{name}"
        assert copy.read_bytes() == (PANELS / name).read_bytes(), f"{shape}: components/{name} drifted"


def _cases(shape: str) -> list[dict]:
    document = json.loads((SHAPES / shape / "data" / "cases.json").read_text(encoding="utf-8"))
    assert document["schema"] == "threadlight-workspace-cases/v1"
    return document["cases"]


def test_case_list_sample_has_a_gateway_shaped_pending_approval():
    pending = [c for c in _cases("case-list") if c["status"] == "pending_approval"]
    assert pending
    for case in pending:
        result = case["pending"]
        assert result["status"] == "pending_approval"
        assert result["operation_id"]
        assert set(result["proposed_arguments"]) == {"case_id", "decision", "expected_etag", "reason"}
        assert result["proposed_arguments"]["case_id"] == case["id"]
        assert result["review_context"]["action"]


def test_case_list_never_records_an_approval_itself():
    js = (SHAPES / "case-list" / "workspace.js").read_text(encoding="utf-8")
    assert "threadlight-review-action" in js  # operator fallback only
    assert "localStorage.setItem" not in js
    readme = _flat((SHAPES / "case-list" / "README.md").read_text(encoding="utf-8"))
    assert "never approves" in readme


def test_case_list_pending_card_is_one_click_for_the_approver():
    """Approvers never get a terminal: email notice or an authenticated panel button."""
    js = (SHAPES / "case-list" / "workspace.js").read_text(encoding="utf-8")
    html = (SHAPES / "case-list" / "index.html").read_text(encoding="utf-8")
    for element in ("pending-email", "pending-panel", "pending-approve", "pending-reject",
                    "pending-cli"):
        assert f'id="{element}"' in html
    # Email mode tells the operator where the decision happens; panel mode posts only the
    # operation and the choice to the configured authenticated approval endpoint.
    assert "Approval email sent to " in js
    assert "approval.url" in js and "X-Threadlight-Panel" in js
    assert "JSON.stringify({ operation_id: c.pending.operation_id, approved: approved })" in js
    # The command block is the operator fallback, shown only without an approval mode.
    assert re.search(r"\$\('pending-cli'\)\.hidden = !!mode", js)
    assert "if (amount == null) return '—';" in js  # unknown amount is never shown as 0
    readme = _flat((SHAPES / "case-list" / "README.md").read_text(encoding="utf-8"))
    assert "config.approval" in readme and "operator fallback" in readme


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.mark.parametrize("shape", runnable_shapes())
def test_smoke_generated_workspace_serves_entrypoint_data_and_assets(shape, tmp_path):
    workspace = tmp_path / "src" / "workspace"
    shutil.copytree(SHAPES / shape, workspace)
    handler = functools.partial(_Quiet, directory=str(workspace))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/"

        def get(path: str) -> bytes:
            with urllib.request.urlopen(base + path, timeout=5) as response:
                assert response.status == 200, path
                return response.read()

        assert b"<!doctype html>" in get("index.html").lower()
        for ref in _local_refs(workspace):
            assert get(ref), ref
        data = json.loads(get("data/cases.json"))
        assert data["cases"]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("shape", runnable_shapes())
def test_runnable_shape_javascript_parses(shape, tmp_path):
    directory = SHAPES / shape
    scripts = sorted(directory.rglob("*.js"))
    assert scripts
    for index, html in enumerate(sorted(directory.rglob("*.html"))):
        for n, body in enumerate(re.findall(r"<script>(.*?)</script>", html.read_text(encoding="utf-8"), re.S)):
            inline = tmp_path / f"inline-{index}-{n}.js"
            inline.write_text(body, encoding="utf-8")
            scripts.append(inline)
    for script in scripts:
        result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script}: {result.stderr}"
