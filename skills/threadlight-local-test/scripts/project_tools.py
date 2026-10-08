#!/usr/bin/env python3
"""threadlight-local-test "project-tools" harness.

Imports the project's generated MCP server (``src/mcp/server.py``) and calls
its REAL tools, in SPEC section 6 order, with identifiers taken from the SPEC
sample data (``specs/sample-data``). This replaces Pattern 0 generic CRUD for
generate-only repos: every tool the hosted agent will call is exercised offline.

Arguments are resolved in this order: a value returned by an earlier call
(e.g. ``get_order`` -> ``customer_id``), a record in the noun-matched sample
file, then a literal ``"sample"``. A call is ``ok`` when it returns no
``error`` key. No network, no Azure login, no LLM.

Usage::

    python project_tools.py --project <repo> --report <report.json>

Exit 0 when every tool call succeeds, 1 otherwise, 2 for a missing server/plan.
"""
from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any


def _load_server(project: Path):
    server = project / "src" / "mcp" / "server.py"
    if not server.is_file():
        raise FileNotFoundError(f"{server} missing (run threadlight-deploy generate_only first)")
    os.environ["SAMPLE_DATA_DIR"] = str(project / "specs" / "sample-data")
    spec = importlib.util.spec_from_file_location("threadlight_project_mcp_server", server)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _plan(project: Path, module) -> list[dict[str, Any]]:
    plan_path = project / "specs" / "project-tools.json"
    if plan_path.is_file():
        return json.loads(plan_path.read_text(encoding="utf-8"))["tools"]
    return [
        {"name": name, "args": [{"name": p, "type": "str"} for p in inspect.signature(getattr(module, name)).parameters]}
        for name in getattr(module, "TOOLS", {})
    ]


def _is_list(arg: dict[str, Any]) -> bool:
    return str(arg.get("type", "")).startswith("list")


def _sample_value(module, tool: str, arg: dict[str, Any], skip: int = 0) -> Any:
    name = arg["name"]
    field = name[:-1] if name.endswith("_ids") else name
    seen = 0
    for _, rows in module._files_for(tool):
        for row in rows:
            if field in row and row[field] not in (None, "") and isinstance(row[field], (str, int, float)):
                if seen == skip:
                    return [row[field]] if _is_list(arg) else row[field]
                seen += 1
    return None


def _collect(context: dict[str, Any], result: Any) -> None:
    rows = result.get("records") if isinstance(result, dict) and isinstance(result.get("records"), list) else [result]
    for row in rows:
        if isinstance(row, dict):
            for key, value in row.items():
                if isinstance(value, (str, int, float)) and key not in context:
                    context[key] = value


def _arguments(module, tool: dict[str, Any], context: dict[str, Any], attempt: int) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for arg in tool.get("args", []):
        name = arg["name"]
        single = name[:-1] if name.endswith("_ids") else name
        value = None
        if attempt == 0 and single in context:
            value = [context[single]] if _is_list(arg) else context[single]
        if value is None:
            value = _sample_value(module, tool["name"], arg, skip=max(0, attempt - 1))
        if value is None:
            if arg.get("optional"):
                continue
            value = ["sample"] if _is_list(arg) else "sample"
        args[name] = value
    return args


def _ok(result: Any) -> bool:
    return not (isinstance(result, dict) and "error" in result)


def run(project: Path) -> dict[str, Any]:
    module = _load_server(project)
    context: dict[str, Any] = {}
    calls = []
    for tool in _plan(project, module):
        fn = getattr(module, tool["name"], None)
        if not callable(fn):
            calls.append({"tool": tool["name"], "arguments": {}, "result": {"error": "tool not exported"}, "ok": False})
            continue
        attempts = []
        for attempt in range(3):
            args = _arguments(module, tool, context, attempt)
            try:
                result = fn(**args)
            except Exception as exc:  # noqa: BLE001 - report, do not crash the harness
                result = {"error": f"{type(exc).__name__}: {exc}"}
            attempts.append((args, result))
            if _ok(result):
                break
        args, result = attempts[-1]
        if _ok(result):
            _collect(context, result)
        calls.append({"tool": tool["name"], "arguments": args, "result": result, "ok": _ok(result),
                      "attempts": len(attempts)})
    return {
        "schema": "threadlight-local-test-report/v1",
        "pattern": "project-tools",
        "project": project.name,
        "status": "pass" if calls and all(c["ok"] for c in calls) else "fail",
        "calls": calls,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call the generated MCP server's real tools with SPEC sample-data ids.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = run(args.project.resolve())
    except (FileNotFoundError, ImportError, KeyError, ValueError) as exc:
        print(f"project_tools: {exc}", file=sys.stderr)
        return 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    failed = [c["tool"] for c in report["calls"] if not c["ok"]]
    print(json.dumps({"status": report["status"], "calls": len(report["calls"]), "failed": failed}))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
