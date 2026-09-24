"""Real MAF agent using private Azure inference and the existing governed MCP client."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


INSTRUCTIONS = """You triage synthetic retail returns and record recommendations only.
Use returns_get_case to read the authoritative case and its exact _etag before deciding.
Never invent a revision, eligibility, amount, human identity, or approval.
Eligible ordinary returns can receive approve_refund. Ineligible ordinary returns
can receive deny_refund. Amounts over 500 or high_risk require escalate_to_supervisor.
Use returns_apply_decision to record the result. This is NOT financial settlement.
If a tool returns pending_approval, stop and report its exact operation_id.
Never infer human consent or invent governance_operation_id. Resume only when the
operator supplies the exact operation_id and original resume_arguments.
If a tool denies or reports an unknown outcome, stop; do not change identifiers
or arguments to bypass it. Report the actual tool result, never claim a write
without a returned audit_id. You have no payment, direct database or shell tool.
"""


def contract(*, evidence=False, confirmation=False):
    if type(evidence) is not bool:
        raise ValueError("explicit_boolean_evidence_selection_required")
    if type(confirmation) is not bool:
        raise ValueError("explicit_boolean_confirmation_selection_required")
    document = {
        "framework": "microsoft-agent-framework",
        "governance": {
            "mode": "selective",
            "environment_modes": {
                "development": "evaluate_only", "staging": "evaluate_only",
                "preproduction": "enforce", "production": "enforce",
            },
            "lifecycle_bindings": [],
        },
        "tools": [
            {
                "id": "returns_apply_decision", "consequence": "write",
                "policy_binding": "returns-safe", "enforcement_path": "governed-tool-gateway",
                "intervention_points": ["pre_tool_call"], "safe_principles": ["Safety"],
                "requires": ["authorization", "signed-policy-bundle", "decision-receipt",
                             "idempotency-or-transaction"],
            },
            {
                "id": "returns_get_case", "consequence": "read", "policy_binding": "none",
                "enforcement_path": "none", "intervention_points": [],
                "safe_principles": [], "requires": [],
            },
        ],
    }
    if evidence:
        document["tools"][0]["requires"].append("signed-evidence")
        document["tools"].append({
            **document["tools"][1], "id": "returns_verify_purchase",
        })
    if confirmation:
        document["tools"][0]["requires"].append("user-confirmation")
    return document


def agent_instructions(*, evidence=False, confirmation=False):
    contract(evidence=evidence, confirmation=confirmation)
    instructions = INSTRUCTIONS
    if evidence:
        instructions += (
            "\nBefore returns_apply_decision call returns_verify_purchase with the EXACT proposed arguments. "
            "Only use its attestation as governance_evidence; never invent claims. "
            "Insufficient evidence is not approval. A declared defect is not a verified defect. "
            "On resume, obtain a fresh token for the same arguments if needed; never change the operation ID "
            "to bypass unknown outcomes or a changed source revision.")
    if confirmation:
        instructions += (
            "\nRequesting-user confirmation happens outside the agent through the configured confirmation channel. "
            "In the normal native Logic Apps/Outlook flow, the user chooses Approve or Reject directly in the email. "
            "Do not tell the user to run commands or a command-line confirmation client. "
            "Never create or register a user context, impersonate a user, or perform their confirmation. "
            "Use only the supplied governance_request_context and its governance_operation_id; "
            "never invent either value. If returns_apply_decision returns pending_confirmation, stop and report "
            "the exact confirmation_id and operation_id; no business effect has occurred. "
            "Resume only the same operation using the original resume_arguments and supplied context "
            "after the user has independently confirmed. Email delivery or login is not consent, "
            "and user confirmation does not replace any separately required reviewer.")
    return instructions


def model_client(config, credential):
    from azure.identity.aio import get_bearer_token_provider
    from openai import AsyncOpenAI
    parsed = urlsplit(config["model_endpoint"])
    if (
        parsed.scheme != "https" or not parsed.hostname
        or not parsed.hostname.endswith(".openai.azure.com") or parsed.port not in (None, 443)
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("fixed_private_azure_model_endpoint_required")
    return AsyncOpenAI(
        base_url=config["model_endpoint"].rstrip("/") + "/openai/v1/",
        api_key=get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default"),
        max_retries=0, timeout=30)


async def build_agent(config, stack):
    from agent_framework.openai import OpenAIChatClient
    from azure.identity.aio import ManagedIdentityCredential
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient
    import httpx

    from govern_control_plane.storage import KeyVaultSigner
    from maf_gateway import create_gateway_agent

    evidence = config.get("evidence_enabled", False)
    confirmation = config.get("confirmation_enabled", False)
    config = {**config, "contract": contract(evidence=evidence, confirmation=confirmation)}
    credential = await stack.enter_async_context(ManagedIdentityCredential(
        client_id=config["agent_client_id"], retry_total=0))
    http = await stack.enter_async_context(httpx.AsyncClient(
        timeout=10, trust_env=False, follow_redirects=False))
    crypto = await stack.enter_async_context(CryptographyClient(
        config["key_id"], credential, retry_total=0))
    keys = await stack.enter_async_context(KeyClient(
        config["key_id"].split("/keys/")[0], credential, retry_total=0))
    spec = importlib.util.spec_from_file_location(
        "returns_gateway_host", Path(__file__).with_name("maf-gateway-container.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    authority = module.GatewayAuthority(
        config, credential=credential, signer=KeyVaultSigner(crypto, key_client=keys), http=http)
    await authority.health()
    reads = await build_read_tools(config, credential, stack, http=http)
    model = await stack.enter_async_context(model_client(config, credential))
    client = OpenAIChatClient(model=config["model_deployment"], async_client=model)
    return await create_gateway_agent(
        config=config, client=client, local_tools=reads,
        instructions=agent_instructions(evidence=evidence, confirmation=confirmation),
        credential=credential, authorize=authority.authorize)


async def build_read_tools(config, credential, stack, *, http=None):
    """Use the credential supplied by the host; reads have no ACS dependency."""
    from agent_framework import FunctionTool, SKIP_PARSING
    import httpx
    if http is None:
        http = await stack.enter_async_context(httpx.AsyncClient(
            timeout=10, trust_env=False, follow_redirects=False))

    async def read_case(case_id: str):
        if case_id not in config["cases"]:
            raise ValueError("case_outside_declared_scope")
        token = await credential.get_token(config["business_scope"])
        response = await http.get(
            config["business_url"] + "/cases/" + case_id,
            headers={"Authorization": "Bearer " + token.token})
        response.raise_for_status()
        if len(response.content) > 16384:
            raise ValueError("business_read_too_large")
        result = response.json()
        if result.get("id") != case_id:
            raise ValueError("business_read_case_mismatch")
        return result

    tools = [FunctionTool(
        name="returns_get_case", description="Read the authoritative synthetic return case and revision.",
        input_model={
            "type": "object", "additionalProperties": False,
            "properties": {"case_id": {"type": "string", "enum": config["cases"]}},
            "required": ["case_id"],
        },
        func=read_case, result_parser=SKIP_PARSING)]
    if config.get("evidence_enabled", False):
        async def verify_purchase(**arguments):
            if arguments["case_id"] not in config["cases"]:
                raise ValueError("case_outside_declared_scope")
            token = await credential.get_token(config["business_scope"])
            try:
                response = await http.post(
                    config["business_url"] + "/evidence/purchase", json=arguments,
                    headers={"Authorization": "Bearer " + token.token})
                response.raise_for_status()
                if len(response.content) > 32768:
                    raise ValueError("evidence_response_limit")
                result = response.json()
                if result.get("status") not in ("verified", "insufficient_evidence"):
                    raise ValueError("evidence_response_invalid")
                return result
            except (httpx.HTTPError, ValueError):
                raise ValueError("evidence_provider_unavailable") from None
        tools.append(FunctionTool(
            name="returns_verify_purchase",
            description="Request purchase corroboration from the authorized backend; never certifies a defect.",
            input_model={
                "type": "object", "additionalProperties": False,
                "properties": {
                    "case_id": {"type": "string", "enum": config["cases"]},
                    "expected_etag": {"type": "string", "maxLength": 128},
                    "decision": {"type": "string", "enum": [
                        "approve_refund", "deny_refund", "escalate_to_supervisor", "request_more_info"]},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 512}},
                "required": ["case_id", "expected_etag", "decision", "reason"]},
            func=verify_purchase, result_parser=SKIP_PARSING))
    return tools


def configure_state(config):
    directory = Path(config["agentserver_state_root"])
    if not directory.is_absolute() or directory == Path("/") or directory.is_symlink():
        raise ValueError("explicit_operator_state_directory_required")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["AGENTSERVER_STATE_ROOT"] = str(directory)


async def run(args):
    config = json.loads(args.configuration.read_text())
    async with AsyncExitStack() as stack:
        agent = await build_agent(config, stack)
        if args.serve:
            configure_state(config)
            from agent_framework_foundry_hosting import ResponsesHostServer
            await ResponsesHostServer(agent).run_async(host="127.0.0.1", port=args.port)
            return
        started = datetime.now(timezone.utc).isoformat()
        async with asyncio.timeout(120):
            response = await agent.run(
                args.prompt, options={"store": False, "max_tokens": 1500})
        document = {
            "runtime": "native-MAF-Azure-model-governed-MCP",
            "hosting": "operator-VM-not-Foundry-hosted",
            "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
            "policy_digest": config["policy_digest"],
            "response": ({"capture": "disabled-for-signed-evidence"} if config.get("evidence_enabled", False)
                         else response.to_dict()),
        }
        with args.output.open("x") as output:
            json.dump(document, output, indent=2)
        if not config.get("evidence_enabled", False):
            print(response.text)
        print(f"Recorded native response: {args.output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prompt")
    mode.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.prompt and (args.output is None or args.output.exists()):
        parser.error("a new output path is required before an effect-capable invocation")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
