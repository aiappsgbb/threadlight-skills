"""Mock MCP server skeleton for a Threadlight pilot.

Runs over stdio locally and streamable-http (port 8080, path /mcp) on ACA.
Replace the example tool with the SPEC section 5b tools.
"""
import json
import os
import pathlib
import sys

from mcp.server.fastmcp import FastMCP

_HERE = pathlib.Path(__file__).resolve().parent
# In the container this file lives at /app/server.py, so never derive the data
# directory from parents[2] (IndexError there). Use the env var, else the
# local repo layout, else the container layout.
_DATA = pathlib.Path(
    os.getenv("SAMPLE_DATA_DIR")
    or next(
        (str(p) for p in (_HERE.parent.parent / "specs" / "sample-data", _HERE / "sample-data") if p.is_dir()),
        str(_HERE / "sample-data"),
    )
)

mcp = FastMCP("__PROJECT_NAME__", host="0.0.0.0", port=int(os.getenv("PORT") or "8080"))


def _load(name: str) -> dict:
    return json.loads((_DATA / name).read_text(encoding="utf-8"))


@mcp.tool()
def get_record(record_id: str) -> dict:
    """Example read tool: return one record from sample-data/records.json."""
    # stdout carries the stdio JSON-RPC stream; logs go to stderr.
    print(f"tool=get_record record_id={record_id}", file=sys.stderr, flush=True)
    for record in _load("records.json").get("records", []):
        if record.get("id") == record_id:
            return record
    return {"error": "not_found", "record_id": record_id}


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT") or "stdio")
