#!/usr/bin/env python3
"""threadlight-local-test "project-tools" harness.

Imports the project's generated MCP server (``src/mcp/server.py``) and calls
its REAL tools, in SPEC section 6 order, with identifiers taken from the SPEC
sample data (``specs/sample-data``). This replaces Pattern 0 generic CRUD for
generate-only repos: every tool the hosted agent will call is exercised offline.

Arguments are resolved in this order: a value returned by an earlier call
(e.g. ``get_order`` -> ``customer_id``), then a record in the noun-matched
sample file. Only required non-identifier values (e.g. ``decision``) fall back
to a literal ``"sample"``. A call is ``ok`` when it returns no ``error`` key.

A tool fails without being called when its contract is degenerate: no declared
arguments, placeholder argument names (``none_id``, ``see_below``) parsed from
a "none / see below" SPEC cell, a required identifier (``*_id``) that no
earlier result or sample record provides, or a resolved call of ``{}``.
No network, no Azure login, no LLM, no bytecode written into the project.

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

sys.dont_write_bytecode = True

DEGENERATE_NAMES = {"none", "none_id", "see_below", "see_below_id", "none_see_below", "none_see_below_id",
                    "n_a", "na", "tbd", "nil", "below", "below_id"}


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


def _is_identifier(name: str) -> bool:
    return name.endswith(("_id", "_ids")) or name == "id"


def _contract_failure(tool: dict[str, Any]) -> str:
    names = [a["name"] for a in tool.get("args", [])]
    if not names:
        if tool.get("no_inputs"):
            return ""  # the SPEC explicitly declares no inputs (for example "none" or "—")
        return "no arguments declared in the SPEC section 6 contract"
    bad = sorted(n for n in names if n.lower() in DEGENERATE_NAMES)
    if bad:
        return f"degenerate argument(s) {', '.join(bad)} parsed from a placeholder SPEC input"
    return ""


def _arguments(module, tool: dict[str, Any], context: dict[str, Any], attempt: int) -> tuple[dict[str, Any], str]:
    args: dict[str, Any] = {}
    unresolved: list[str] = []
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
            if _is_identifier(name):
                unresolved.append(name)
                continue
            value = ["sample"] if _is_list(arg) else "sample"
        args[name] = value
    if unresolved:
        return args, f"unresolved required identifier(s) {', '.join(unresolved)}: no earlier result or sample record"
    if not args and not tool.get("no_inputs"):
        return args, "empty call: no argument resolved from SPEC sample data"
    return args, ""


def _ok(result: Any) -> bool:
    return not (isinstance(result, dict) and "error" in result)


def run(project: Path) -> dict[str, Any]:
    module = _load_server(project)
    context: dict[str, Any] = {}
    calls = []
    for tool in _plan(project, module):
        fn = getattr(module, tool["name"], None)
        if not callable(fn):
            calls.append({"tool": tool["name"], "arguments": {}, "result": {"error": "tool not exported"},
                          "ok": False, "failure": "tool not exported"})
            continue
        failure = _contract_failure(tool)
        if failure:
            calls.append({"tool": tool["name"], "arguments": {}, "result": None, "ok": False,
                          "failure": failure, "attempts": 0})
            continue
        attempts = []
        for attempt in range(3):
            args, failure = _arguments(module, tool, context, attempt)
            if failure:
                result = {"error": failure}
            else:
                try:
                    result = fn(**args)
                except Exception as exc:  # noqa: BLE001 - report, do not crash the harness
                    result = {"error": f"{type(exc).__name__}: {exc}"}
            attempts.append((args, result, failure))
            if not failure and _ok(result):
                break
        args, result, failure = attempts[-1]
        ok = not failure and _ok(result)
        if ok:
            _collect(context, result)
        elif not failure:
            failure = str(result.get("error")) if isinstance(result, dict) else "tool returned an error"
        calls.append({"tool": tool["name"], "arguments": args, "result": result, "ok": ok,
                      "failure": failure, "attempts": len(attempts)})
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
