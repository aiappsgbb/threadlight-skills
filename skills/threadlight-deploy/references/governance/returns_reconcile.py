"""Payload-minimized reconciliation of authenticated native response readbacks.

The caller must obtain the response and stores through authenticated APIs and
verify the expected binding. These records describe observed calls, not remote
attestation or an authorization decision.
"""
from datetime import datetime, timezone
import hashlib
import json

from govern_control_plane.models import canonical, strict_json

TOOLS = {"returns_get_case": "none", "returns_apply_decision": "returns-safe"}
CASE_FIELDS = ("id", "_etag", "status", "amount", "eligible", "high_risk")


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def deployment(binding):
    return {key: binding[key] for key in (
        "agent_id", "agent_version", "image_digest", "environment", "subscription", "resource_group")}


def reconcile_response(response, *, binding, gateway_principal, central, operations, audits, read_audits):
    reference = response.get("agent_reference") or {}
    if (reference.get("name") != binding["agent_id"] or reference.get("version") != binding["agent_version"]
            or not response.get("agent_session_id") or response.get("status") not in ("completed", "failed")):
        raise ValueError("native_response_binding_mismatch")
    calls, results = [], {}
    for item in response["output"]:
        if item["type"] == "function_call":
            if item["name"] not in TOOLS or any(c["call_id"] == item["call_id"] for c in calls):
                raise ValueError("unknown_or_duplicate_tool_call")
            calls.append(item)
        elif item["type"] == "function_call_output":
            if item["call_id"] in results:
                raise ValueError("duplicate_tool_output")
            results[item["call_id"]] = item["output"]
        elif item["type"].endswith("_call"):
            raise ValueError("unsupported_native_tool_call")
    if not set(results) <= {call["call_id"] for call in calls}:
        raise ValueError("orphan_tool_output")
    records, reads = [], {}
    for call in calls:
        arguments = strict_json(call["arguments"].encode())
        if not isinstance(arguments, dict):
            raise ValueError("tool_arguments_object_required")
        raw = results.get(call["call_id"])
        output = None
        if isinstance(raw, str):
            try:
                output = json.loads(raw)
            except json.JSONDecodeError:
                pass
        body = {
            "kind": "native-tool-call", "source": "authenticated-native-response-reconciliation",
            "evidence_scope": "post-run-not-attestation",
            "response_id": response["id"], "session_id": response["agent_session_id"],
            "tool_call_id": call["call_id"], "tool_name": call["name"],
            "policy_binding": TOOLS[call["name"]], "subject": binding["principal"],
            "client": binding["client_id"], "deployment": deployment(binding),
            "arguments_digest": digest(arguments), "output_digest": digest(raw),
            "response_started_at": datetime.fromtimestamp(response["created_at"], timezone.utc).isoformat(),
            "response_finished_at": datetime.fromtimestamp(
                response.get("completed_at") or response["created_at"], timezone.utc).isoformat(),
            "status": "failed", "policy_receipts": [],
        }
        if call["name"] == "returns_get_case":
            if (isinstance(output, dict) and set(CASE_FIELDS) <= output.keys()
                    and output.get("id") == arguments.get("case_id")):
                reads[output["id"]] = output
                body.update(status="read-completed", read_audit_delivery="post-run-native-response-reconciliation")
                if output.get("read_audit_id"):
                    matches = [r for r in read_audits if r["id"] == output["read_audit_id"]
                               and r.get("scope") == binding["tenant_id"] + ":" + binding["principal"]]
                    if len(matches) != 1:
                        raise ValueError("independent_read_audit_required")
                    recorded = matches[0]["body"]
                    if (recorded.get("kind") != "case-read" or recorded.get("action_id") != call["name"]
                            or recorded.get("case_id") != output["id"] or recorded.get("case_revision") != output["_etag"]
                            or recorded["subject"] != binding["principal"] or recorded["client"] != binding["client_id"]
                            or recorded["deployment"] != deployment(binding) or recorded["policy_binding"] != "none"
                            or recorded["result_digest"] != digest({k: output[k] for k in CASE_FIELDS})):
                        raise ValueError("read_audit_binding_mismatch")
                    body.update(read_audit_delivery="backend-acknowledged-before-return",
                                read_audit_id=output["read_audit_id"])
        else:
            business_arguments = {k: v for k, v in arguments.items() if k != "governance_operation_id"}
            facts = {
                "tenant": binding["tenant_id"], "subject": binding["principal"], "client": binding["client_id"],
                "action": call["name"], "scope": "returns", "policy": binding["policy_digest"],
                "deployment": deployment(binding),
            }
            action_hash = digest({"facts": facts, "arguments": business_arguments})
            operation_scope = digest([binding["tenant_id"], binding["principal"], call["name"]])
            body.update(action_hash=action_hash, policy_digest=binding["policy_digest"])
            if arguments.get("case_id") in reads:
                body["revision_matched_read"] = arguments.get("expected_etag") == reads[arguments["case_id"]]["_etag"]
            receipts = [
                row["body"]["receipt"] for row in central
                if row.get("scope") == binding["tenant_id"]
                and row.get("body", {}).get("owner") == gateway_principal
                and isinstance(row["body"].get("receipt"), dict)
                and row["body"]["receipt"].get("action_hash") == action_hash
                and row["body"]["receipt"].get("policy_digest") == binding["policy_digest"]
                and row["body"]["receipt"].get("agent_version") == binding["agent_version"]
                and row["body"]["receipt"].get("image_digest") == binding["image_digest"]
            ]
            if isinstance(output, dict) and output.get("status") == "pending_approval":
                intent = output["approval_intent"]
                matches = [r for r in operations if r["id"] == intent["session_id"]
                           and r.get("scope") == operation_scope]
                if (output["review_context"] != facts or intent["action_hash"] != action_hash
                        or digest(output["operation_id"])[7:] != intent["session_id"]
                        or len(matches) != 1 or matches[0]["body"].get("approval_intent") != intent):
                    raise ValueError("independent_pending_operation_required")
                body.update(status="pending", operation_id=output["operation_id"],
                            gateway_correlation_id=intent["session_id"], approval_expires_at=intent["expires_at"])
            elif isinstance(output, dict) and output.get("audit_id"):
                matches = [r for r in audits if r.get("id") == output["audit_id"] and r.get("kind") == "decision-audit"]
                if len(matches) != 1:
                    raise ValueError("independent_business_audit_required")
                audit = matches[0]
                if (audit["result"] != output or audit["arguments"] != business_arguments
                        or output.get("case_id") != business_arguments.get("case_id")
                        or output.get("decision") != business_arguments.get("decision")
                        or audit["provenance"]["action_hash"] != action_hash):
                    raise ValueError("business_audit_binding_mismatch")
                matched = [r for r in receipts if r["receipt_id"] == audit["provenance"]["receipt_id"]
                           and r["decision"] == "allow"]
                if len(matched) != 1:
                    raise ValueError("independent_allow_receipt_required")
                receipt = matched[0]
                if not any(r["id"] == receipt["correlation_id"] and r.get("scope") == operation_scope
                           and r["body"].get("state") == "completed"
                           and r["body"].get("action_hash") == action_hash
                           and r["body"].get("facts_hash") == digest(facts)
                           and r["body"].get("receipt_id") == receipt["receipt_id"] for r in operations):
                    raise ValueError("completed_gateway_operation_required")
                body.update(status="completed", business_audit_id=audit["id"],
                            policy_receipts=[receipt["receipt_id"]], gateway_correlation_id=receipt["correlation_id"])
            else:
                denials = [r for r in receipts if r["decision"] == "deny" and r.get("recorded_at")
                           and response["created_at"] - 1 <= datetime.fromisoformat(
                               r["recorded_at"]).timestamp() <= (response.get("completed_at")
                               or response["created_at"]) + 1]
                if denials:
                    body.update(status="denied", policy_receipts=sorted(r["receipt_id"] for r in denials),
                                receipt_correlation="matching-action-hash-and-response-window")
                elif isinstance(raw, str) and raw and isinstance(arguments.get("governance_operation_id"), str):
                    key = digest(arguments["governance_operation_id"])[7:]
                    matches = [r["body"] for r in operations if r["id"] == key
                               and r.get("scope") == operation_scope]
                    if len(matches) == 1:
                        operation = matches[0]
                        intent = operation.get("approval_intent", {})
                        approvals = [r["body"] for r in central
                                     if r.get("scope") == binding["tenant_id"]
                                     and r["id"] == "approval:" + intent.get("nonce", "")]
                        if (operation.get("state") == "awaiting_approval"
                                and operation.get("input_hash") == action_hash
                                and operation.get("action_hash") == action_hash
                                and operation.get("facts_hash") == digest(facts)
                                and intent.get("context_identity") == digest(facts)
                                and intent.get("action_hash") == action_hash
                                and intent.get("session_id") == key
                                and intent.get("tenant") == binding["tenant_id"]
                                and intent.get("principal") == gateway_principal
                                and intent.get("agent_id") == binding["agent_id"]
                                and intent.get("policy_hash") == binding["policy_digest"]
                                and len(approvals) == 1 and approvals[0].get("intent") == intent
                                and approvals[0].get("state") == "pending"
                                and "grant" in approvals[0] and approvals[0]["grant"] is None
                                and datetime.fromisoformat(intent["expires_at"]).timestamp() <= response["created_at"]):
                            # Observed association only: a generic tool failure does not prove its cause.
                            body.update(approval_observation="expired-ungranted-intent",
                                        gateway_correlation_id=key, approval_expires_at=intent["expires_at"])
        identifier = "call-" + digest([response["id"], call["call_id"]])[7:]
        records.append({"id": identifier, "scope": binding["tenant_id"] + ":" + binding["principal"], "body": body})
    return records


async def collect(configuration, output, *, persist=False):
    """Collect through authenticated native APIs; never grant roles or authorize an action."""
    import logging
    import os
    from contextlib import AsyncExitStack
    from pathlib import Path
    from azure.identity.aio import AzureCliCredential
    from azure.ai.projects.aio import AIProjectClient
    from azure.cosmos.aio import CosmosClient
    from azure.keyvault.keys.aio import KeyClient
    from azure.keyvault.keys.crypto.aio import CryptographyClient
    from govern_control_plane.bootstrap import SignedBootstrap, verify
    from govern_control_plane.models import parse
    from govern_control_plane.storage import AzureStore, KeyVaultSigner, Missing

    if not os.environ.get("AZURE_CONFIG_DIR") or not os.environ.get("AZD_CONFIG_DIR"):
        raise ValueError("paired_tenant_isolation_required")
    expected = configuration["expected_binding"]
    required = {"tenant_id", "key_id", "agent_id", "agent_version", "image_digest",
                "policy_digest", "config_digest", "project_endpoint"}
    if not required <= expected.keys() or not 1 <= len(configuration["responses"]) <= 100:
        raise ValueError("explicit_expected_binding_and_response_scope_required")
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=False)
    signed = parse(SignedBootstrap, Path(configuration["binding_file"]).read_bytes())
    if any(signed.binding.model_dump(mode="json").get(key) != value for key, value in expected.items()):
        raise ValueError("operator_expected_binding_mismatch")
    logging.getLogger("azure").setLevel(logging.CRITICAL + 1)
    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(AzureCliCredential(tenant_id=expected["tenant_id"]))
        keys = await stack.enter_async_context(KeyClient(
            expected["key_id"].split("/keys/")[0], credential, retry_total=0))
        crypto = await stack.enter_async_context(CryptographyClient(
            expected["key_id"], credential, retry_total=0))
        binding = await verify(signed, KeyVaultSigner(crypto, key_client=keys),
                               tenant_id=expected["tenant_id"], key_id=expected["key_id"])
        project = await stack.enter_async_context(AIProjectClient(
            endpoint=expected["project_endpoint"], credential=credential, api_version="v1", retry_total=0))
        version = await project.agents.get_version(agent_name=binding.agent_id, agent_version=binding.agent_version)
        if (version.instance_identity.principal_id != binding.principal
                or not version.definition.container_configuration.image.endswith("@" + binding.image_digest)):
            raise ValueError("native_version_identity_or_image_mismatch")
        client = await stack.enter_async_context(project.get_openai_client(agent_name=binding.agent_id))
        responses, seen = [], set()
        for reference in configuration["responses"]:
            if reference["id"] in seen:
                raise ValueError("duplicate_response_reference")
            seen.add(reference["id"])
            response = await client.responses.retrieve(
                reference["id"], extra_headers={"x-agent-session-id": reference["session_id"]})
            response = response.model_dump(mode="json")
            if response["id"] != reference["id"] or response.get("agent_session_id") != reference["session_id"]:
                raise ValueError("native_response_reference_mismatch")
            responses.append(response)
        cosmos = await stack.enter_async_context(CosmosClient(
            configuration["cosmos_url"], credential, retry_total=0))
        database = cosmos.get_database_client(configuration["cosmos_database"])
        snapshots = {}
        for name in ("governance-records", "gateway-idempotency", "returns-cases", "runner-activity"):
            snapshots[name] = [row async for row in database.get_container_client(name).query_items("SELECT * FROM c")]
        rows = []
        for response in responses:
            rows.extend(reconcile_response(
                response, binding=binding.model_dump(mode="json"),
                gateway_principal=configuration["gateway_principal"],
                central=snapshots["governance-records"], operations=snapshots["gateway-idempotency"],
                audits=snapshots["returns-cases"], read_audits=snapshots["runner-activity"]))
        expected_count = sum(sum(i["type"] == "function_call" for i in r["output"]) for r in responses)
        if len(rows) != expected_count:
            raise ValueError("tool_call_coverage_mismatch")
        if persist:
            store = AzureStore(None, database.get_container_client("runner-activity"),
                               account_reader=cosmos._get_database_account)
            for row in rows:
                try:
                    existing, _ = await store.read(row["scope"], row["id"])
                except Missing:
                    await store.create(row["scope"], row["id"], row["body"])
                    existing, _ = await store.read(row["scope"], row["id"])
                if existing != row["body"]:
                    (destination / "record-conflict.json").write_bytes(canonical({
                        "id": row["id"], "existing": existing, "observed": row["body"]}))
                    raise ValueError("persisted_call_record_mismatch")
        summary = {
            "schema": "threadlight-returns-reconciliation/v1",
            "source": "authenticated-native-response-and-independent-store-reads",
            "scope": "selected-response-set-not-universal-agent-attestation",
            "tool_inventory": TOOLS, "responses": len(responses), "tool_calls": len(rows),
            "persisted_and_read_back": len(rows) if persist else 0,
            "response_ids": sorted(seen), "records": rows,
        }
        (destination / "reconciliation.json").write_bytes(canonical(summary))
        return summary


def main():
    import argparse
    import asyncio
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--persist", action="store_true",
                        help="Create and read back minimized records in the configured runner-activity container")
    args = parser.parse_args()
    result = asyncio.run(collect(strict_json(args.configuration.read_bytes()), args.output, persist=args.persist))
    print(json.dumps({key: result[key] for key in (
        "scope", "responses", "tool_calls", "persisted_and_read_back")}))


if __name__ == "__main__":
    main()
