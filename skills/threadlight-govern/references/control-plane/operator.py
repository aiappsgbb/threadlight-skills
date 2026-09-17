"""Submit one explicit operator request; never retry, redispatch or fabricate evidence."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from azure.core.exceptions import AzureError
import httpx

from .client import ServiceTransport, ApprovalUnavailable
from .models import ObjectId, canonical, parse, strict_json


class OperatorTransport(ServiceTransport):
    @staticmethod
    def validate_configuration(base_url, scope, timeout):
        if scope.endswith("/Governance.Operate"):
            scope = scope.removesuffix("/Governance.Operate") + "/.default"
        ServiceTransport.validate_configuration(base_url, scope, timeout)


async def submit(request, *, gateway_url, scope, credential, http=None):
    if (not isinstance(request, dict) or request.get("operation") not in {
            "inspect", "reconcile", "admission"} or len(canonical(request)) > 32768):
        raise ValueError("invalid_operator_request")
    async with OperatorTransport(
            base_url=gateway_url, scope=scope, credential=credential, http=http) as transport:
        code, result = await transport.request("POST", "/governance/operations", request)
    if code != 200 or result.get("retry_authorized") is not False or result.get("state") not in {
            "missing", "open", "stopped", "pending", "awaiting_approval", "rejected",
            "completed", "not_executed"}:
        raise ValueError("operator_request_not_completed_inspect_before_retry")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--gateway-url", required=True, help="Exact gateway HTTPS origin, no /mcp suffix")
    parser.add_argument("--scope", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--credential", choices=["azure-cli", "managed-identity"], required=True)
    parser.add_argument("--client-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        parse(ObjectId, canonical(args.tenant))
        parse(ObjectId, canonical(args.subscription))
        OperatorTransport.validate_configuration(args.gateway_url, args.scope, 5)
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("operator_output_must_be_new")
        raw = args.request.read_bytes()
        if len(raw) > 32768:
            raise ValueError("operator_request_too_large")
        request = strict_json(raw)
        if args.credential == "azure-cli":
            if not os.environ.get("AZURE_CONFIG_DIR") or not os.environ.get("AZD_CONFIG_DIR"):
                raise ValueError("paired_tenant_isolation_required")
            context = json.loads(subprocess.check_output(
                ["az", "account", "show", "--output", "json"], timeout=15))
            if context.get("tenantId") != args.tenant or context.get("id") != args.subscription:
                raise ValueError("operator_tenant_subscription_mismatch")
        elif not args.client_id:
            raise ValueError("explicit_operator_managed_identity_required")

        async def run():
            from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential
            credential = (AzureCliCredential(tenant_id=args.tenant, process_timeout=15)
                          if args.credential == "azure-cli" else
                          ManagedIdentityCredential(client_id=args.client_id, retry_total=0))
            async with credential:
                return await submit(request, gateway_url=args.gateway_url, scope=args.scope,
                                    credential=credential)
        result = asyncio.run(run())
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, sort_keys=True)
            output.write("\n")
        print(json.dumps({"state": result["state"], "execution": "not-dispatched",
                          "retry_authorized": False}))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, AzureError, httpx.HTTPError, ApprovalUnavailable):
        print("Operator request not confirmed. Preserve evidence and inspect state before retry; "
              "no business retry is authorized.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
