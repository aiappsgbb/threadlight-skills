"""Pilot MCP server for the governed-write demo: ONE unbound read tool.

`returns_get_case` reads a synthetic case (with its exact Cosmos `_etag`) using the
server's own read-only managed identity. The consequential write
`returns_apply_decision` is deliberately NOT implemented here: the governed GHCP
host routes it to the authenticated gateway, which calls the separately
authorizing business API. Reads stay unbound (no ACS), as in every Fast-PoC.
"""
import os
import sys

from mcp.server.fastmcp import FastMCP

CASE_FIELDS = ("case_id", "customer_id", "status", "amount", "currency", "eligible",
               "high_risk", "reason_code", "decision", "audit_id")

mcp = FastMCP("governed-write-returns", host="0.0.0.0", port=int(os.getenv("PORT") or "8080"))
_container = None


async def _cases():
    global _container
    if _container is None:
        from azure.cosmos.aio import CosmosClient
        from azure.identity.aio import ManagedIdentityCredential
        credential = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
        client = CosmosClient(os.environ["RETURNS_COSMOS_URL"], credential=credential)
        _container = client.get_database_client(os.environ["RETURNS_COSMOS_DATABASE"]).get_container_client(
            os.environ["RETURNS_COSMOS_CONTAINER"])
    return _container


@mcp.tool()
async def returns_get_case(case_id: str) -> dict:
    """Read one synthetic return case and its exact revision (_etag). Read-only."""
    print(f"tool=returns_get_case case_id={case_id}", file=sys.stderr, flush=True)
    from azure.cosmos.exceptions import CosmosResourceNotFoundError
    try:
        record = await (await _cases()).read_item(case_id, partition_key=case_id)
    except CosmosResourceNotFoundError:
        return {"error": "not_found", "case_id": case_id}
    if record.get("kind") != "case":
        return {"error": "not_found", "case_id": case_id}
    result = {key: record[key] for key in CASE_FIELDS if key in record}
    result["_etag"] = record["_etag"]
    return result


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT") or "stdio")
