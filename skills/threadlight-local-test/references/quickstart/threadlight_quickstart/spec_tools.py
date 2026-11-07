"""SPEC section 6 tools for Pattern 0, so skills that name real tools resolve locally.

For every tool in ``specs/SPEC.md`` section 6 Pattern 0 registers a tool with
the SAME name, backed in this order:

1. ``design``    – the function of that name in the project's own
   ``src/mcp/server.py`` (threadlight-design's mock server), imported unchanged
   so its safeguards (field allow-lists, validation, required citations) apply;
2. ``knowledge`` – a read-only local search over ``specs/knowledge/*.md`` when
   section 6 backs the tool with a knowledge base (Foundry IQ / AI Search);
3. ``adapter``   – a generic read/write adapter over the in-memory sample data.

When ``specs/knowledge/*.md`` exists but section 6 declares no knowledge tool,
Pattern 0 also registers the read-only ``knowledge.TOOL_NAME``
(``knowledge_search``) so the agent can read the policy instead of trying a
skill script. threadlight-deploy generate_only serves the same tool name.

Read tools never need approval. Write tools (section 11a ``consequence``, the
section 5b Read/Write column, else the tool-name prefix) are registered with
``approval_mode="always_require"``: a human approves each call (Streamlit
buttons or ``--approve-tool``), and nothing approves them implicitly.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from . import knowledge
from .stub_tools import InMemoryStore

log = logging.getLogger(__name__)

READ_PREFIXES = ("get", "list", "query", "search", "find", "lookup", "fetch", "check", "read")
KNOWLEDGE_BACKING = re.compile(r"foundry[\s-]*iq|knowledge[\s-]*base|ai[\s-]*search|search[\s-]*index", re.I)
JSON_TYPES = {"string": "string", "str": "string", "text": "string", "int": "integer", "integer": "integer",
              "number": "number", "float": "number", "decimal": "number", "bool": "boolean", "boolean": "boolean"}


def _section(spec: str, number: str) -> str:
    m = re.search(rf"^##\s+{re.escape(number)}\b[^\n]*\n(.*?)(?=^##\s|\Z)", spec, re.M | re.S)
    return m.group(1) if m else ""


def _rows(block: str) -> list[list[str]]:
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                rows.append(cells)
    return rows


def _consequences(spec: str) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for row in _rows(_section(spec, "5b")):
        names = re.findall(r"`([A-Za-z_]\w*)`", row[0])
        modes = [c.upper() for c in row[1:] if c.strip().upper() in {"R", "W", "READ", "WRITE", "R/W"}]
        if names and modes:
            kinds[names[0]] = "read" if modes[0] in {"R", "READ"} else "write"
    sec11a = re.search(r"^#{2,4}\s*11a\b[^\n]*\n(.*?)(?=^#{2,4}\s*11[b-z]\b|^##\s+\d|\Z)", spec, re.M | re.S)
    if sec11a:
        for item in re.split(r"^\s*-\s+(?=id:)", sec11a.group(1), flags=re.M)[1:]:
            ident = re.match(r"id:\s*[\"']?([A-Za-z_]\w*)", item)
            cons = re.search(r"^\s*consequence:\s*[\"']?([A-Za-z_-]+)", item, re.M)
            if ident and cons:
                kinds[ident.group(1)] = "read" if cons.group(1).lower().startswith("read") else "write"
    return kinds


def parse_spec_tools(spec: str) -> list[dict[str, Any]]:
    """SPEC section 6 as ``[{name, description, args, kind, knowledge}]`` in SPEC order."""
    block = _section(spec, "6")
    kinds = _consequences(spec)
    plan: dict[str, dict[str, Any]] = {}
    for m in re.finditer(r"^#{3,4}\s+`([A-Za-z_]\w*)`[^\n]*\n(.*?)(?=^#{2,4}\s|\Z)", block, re.M | re.S):
        name, body = m.group(1), m.group(2)
        desc = re.search(r"^\s*[-*]\s*\*\*Description\*\*\s*:?\s*(.+)$", body, re.M | re.I)
        backed = re.search(r"^\s*[-*]\s*\*\*Backed\s+by\*\*\s*:?\s*(.+)$", body, re.M | re.I)
        args = []
        table = _rows(body)
        if table and "param" in table[0][0].lower():
            header = [h.lower() for h in table[0]]
            i_type = next((i for i, h in enumerate(header) if "type" in h), 1)
            i_req = next((i for i, h in enumerate(header) if "required" in h), None)
            for row in table[1:]:
                arg = re.sub(r"[^\w]", "", row[0])
                if not arg:
                    continue
                raw = row[i_type].strip("` ").lower() if i_type < len(row) else "string"
                required = i_req is None or (i_req < len(row) and row[i_req].strip().lower().startswith("y"))
                args.append({"name": arg, "type": raw, "required": required})
        plan[name] = {"name": name, "description": (desc.group(1).strip() if desc else ""), "args": args,
                      "knowledge": bool(backed and KNOWLEDGE_BACKING.search(backed.group(1)))}
    for row in _rows(block):
        names = re.findall(r"`([A-Za-z_]\w*)`", row[0])
        if names and names[0] not in plan and "tool" not in row[0].lower():
            ticked = re.findall(r"`([A-Za-z_]\w*)`", row[1]) if len(row) > 1 else []
            plan[names[0]] = {"name": names[0], "description": row[-1].strip("` "), "knowledge": False,
                              "args": [{"name": a, "type": "string", "required": True} for a in ticked]}
    for tool in plan.values():
        if tool["name"] in kinds:
            tool["kind"] = kinds[tool["name"]]
        elif tool["knowledge"] or tool["name"].split("_")[0] in READ_PREFIXES or any(
                part in READ_PREFIXES for part in tool["name"].split("_")[1:2]):
            tool["kind"] = "read"
        else:
            tool["kind"] = "write"
        if tool["knowledge"] and tool["kind"] != "read":
            tool["knowledge"] = False
    return list(plan.values())


def _schema(args: list[dict[str, Any]]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for arg in args:
        raw = arg["type"]
        if raw.startswith(("array", "list")) or raw.endswith("[]"):
            props[arg["name"]] = {"type": "array", "items": {"type": "string"}}
        else:
            props[arg["name"]] = {"type": JSON_TYPES.get(raw, "string")}
    return {"type": "object", "properties": props,
            "required": [a["name"] for a in args if a.get("required")]}


def _singular(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token


def _store_for(tool: str, stores: dict[str, InMemoryStore]) -> InMemoryStore | None:
    tokens = {_singular(t) for t in tool.lower().split("_")}
    ranked = sorted(stores.values(), key=lambda s: (-len(tokens & {_singular(t) for t in s.name.split("_")}), s.name))
    return ranked[0] if ranked else None


def _adapter(tool: dict[str, Any], stores: dict[str, InMemoryStore]) -> Callable[..., Any]:
    names = [a["name"] for a in tool["args"]]
    many = tool["name"].split("_")[0] in {"list", "query", "search", "find"} or "_list_" in tool["name"]

    def read(**kwargs: Any) -> Any:
        given = {k: v for k, v in kwargs.items() if k in names and v not in (None, "", [])}
        store = _store_for(tool["name"], stores)
        if store is None:
            return {"error": "not_found", "message": "no sample data"}
        rows = list(store.records.values())
        fields = set().union(*(r.keys() for r in rows)) if rows else set()
        for arg, value in given.items():
            field = arg if arg in fields else ("id" if arg.endswith("_id") else None)
            if field is None:
                if isinstance(value, str) and not arg.endswith("_id"):
                    needle = value.lower()
                    rows = [r for r in rows if needle in str(r).lower()] or rows
                continue
            wanted = {str(v) for v in value} if isinstance(value, list) else {str(value)}
            rows = [r for r in rows if str(r.get(field)) in wanted]
        if many or not given:
            return {"records": rows, "count": len(rows)}
        return rows[0] if rows else {"error": "not_found", "tool": tool["name"], "arguments": given}

    def write(**kwargs: Any) -> Any:
        given = {k: v for k, v in kwargs.items() if k in names}
        store = _store_for(tool["name"], stores)
        key = next((v for k, v in given.items() if k.endswith("_id") or k == "id"), None)
        field = tool["name"].split("_")[-1]
        if store is not None and key is not None and str(key) in store.records:
            store.update(str(key), **{field: {k: v for k, v in given.items() if k != "id"}})
            return {"ok": True, "record_id": f"{tool['name']}-{key}", "stored_on": f"{store.name}/{key}",
                    "note": "in-memory demo write; reset every launch"}
        return {"error": "not_found", "tool": tool["name"], "arguments": given}

    return read if tool["kind"] == "read" else write


def _load_design_server(root: Path, sample_dir: Path):
    path = root / "src" / "mcp" / "server.py"
    if not path.is_file():
        return None
    os.environ["SAMPLE_DATA_DIR"] = str(sample_dir)
    os.environ.setdefault("KNOWLEDGE_DIR", str(root / "specs" / "knowledge"))
    name = f"threadlight_quickstart_design_server_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - fall back to adapters, never crash the demo
        log.warning("Could not import %s (%s); SPEC tools use sample-data adapters.", path, exc)
        return None
    finally:
        sys.dont_write_bytecode = previous
    return module


def build_spec_tools(
    root: Path, sample_dir: Path, stores: dict[str, InMemoryStore], tool_factory: Callable[..., Any],
    function_tool: Callable[..., Any] | None = None,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Return ``(tools, wiring)`` for SPEC section 6, or ``([], [])`` without a SPEC tool list."""
    spec_path = root / "specs" / "SPEC.md"
    if not spec_path.is_file():
        return [], []
    plan = parse_spec_tools(spec_path.read_text(encoding="utf-8"))
    if not plan:
        return [], []
    design = _load_design_server(root, sample_dir)
    knowledge_dir = root / "specs" / "knowledge"
    if (knowledge_dir.is_dir() and any(knowledge_dir.glob("*.md")) and not any(t["knowledge"] for t in plan)
            and knowledge.TOOL_NAME not in {t["name"] for t in plan}):
        plan.append({"name": knowledge.TOOL_NAME, "description": knowledge.TOOL_DESCRIPTION, "kind": "read",
                     "knowledge": True, "args": [{"name": "query", "type": "string", "required": True}]})
    tools: list[Any] = []
    wiring: list[dict[str, Any]] = []
    for spec_tool in plan:
        mode = "never_require" if spec_tool["kind"] == "read" else "always_require"
        description = spec_tool["description"] or f"{spec_tool['kind']} tool from SPEC section 6"
        fn = getattr(design, spec_tool["name"], None) if design is not None else None
        if callable(fn):
            source = "design server (src/mcp/server.py)"
            tools.append(tool_factory(fn, name=spec_tool["name"], description=(fn.__doc__ or description).strip(),
                                      approval_mode=mode))
        else:
            if spec_tool.get("knowledge"):
                source = "knowledge (local search over specs/knowledge)"
                params = [a["name"] for a in spec_tool["args"]]

                def impl(_params=params, **kwargs: Any) -> Any:
                    query = " ".join(str(kwargs[p]) for p in _params if kwargs.get(p))
                    return knowledge.search(knowledge_dir, query)
            else:
                source = "sample-data adapter"
                impl = _adapter(spec_tool, stores)
            if function_tool is None:
                impl.__name__ = spec_tool["name"]
                tools.append(impl)
            else:
                tools.append(function_tool(name=spec_tool["name"], description=description, func=impl,
                                           approval_mode=mode, input_model=_schema(spec_tool["args"])))
        wiring.append({"tool": spec_tool["name"], "kind": spec_tool["kind"], "source": source,
                       "approval": "human approval" if mode == "always_require" else "none (read-only)"})
    return tools, wiring
