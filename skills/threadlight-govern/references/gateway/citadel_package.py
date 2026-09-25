"""Local Citadel generation and policyXml overlays; never provision or sign."""
from __future__ import annotations

import argparse
import html
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from govern_bundle.policy_bundle import build_bundle, verify_bundle
from govern_control_plane.models import canonical, parse
from .citadel import Binding, RESERVED_PREFIXES
from .dispatcher import Registry

UPSTREAM = "23fbc8fe7f2f068de3ed3c80da2344760faac1e6"
APIM_API_VERSION = "2025-09-01-preview"


def raw_policy_xml(encoded):
    """APIM rawxml parses C# before XML entity decoding; literals stay escaped."""
    ET.fromstring(encoded)
    result = re.sub(r'="(@(?:\(|\{)[^"]*)"',
                    lambda match: '="' + html.unescape(match[1]) + '"', encoded)
    return re.sub(r'>(@(?:\(|\{)[^<]*)<',
                  lambda match: ">" + html.unescape(match[1]) + "<", result)


def mcp_api_properties(binding):
    binding = parse(Binding, canonical(binding))
    internal, public = urlsplit(binding.gateway_url), urlsplit(binding.public_url)
    return {
        "type": "mcp", "displayName": binding.producer.contract_id,
        "path": public.path.lstrip("/"), "protocols": ["https"],
        "serviceUrl": f"https://{internal.netloc}",
        "subscriptionRequired": True,
        "subscriptionKeyParameterNames": {"header": "api-key", "query": "api-key"},
        "mcpProperties": {"endpoints": {"message": {"uriTemplate": "/"}}},
    }


def policies(binding, *, asset_id, publish="", access=""):
    if binding is None:
        return publish, access
    if publish or access:
        raise ValueError("citadel_custom_policy_requires_explicit_review")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", asset_id):
        raise ValueError("citadel_asset_id_invalid")
    binding = parse(Binding, canonical(binding))

    def root():
        node = ET.Element("policies")
        sections = {name: ET.SubElement(node, name) for name in ("inbound", "backend", "outbound", "on-error")}
        for name in ("inbound", "outbound", "on-error"):
            ET.SubElement(sections[name], "base")
        return node, sections

    def reject(parent, condition):
        choose = ET.SubElement(parent, "choose")
        when = ET.SubElement(choose, "when", condition=condition)
        reply = ET.SubElement(when, "return-response")
        ET.SubElement(reply, "set-status", code="403", reason="Forbidden")

    def header(parent, name, value):
        node = ET.SubElement(parent, "set-header", name=name, **{"exists-action": "override"})
        ET.SubElement(node, "value").text = value

    node, section = root()
    inbound = section["inbound"]
    prefixes = " || ".join(
        f'k.StartsWith("{prefix.decode()}", StringComparison.OrdinalIgnoreCase)'
        for prefix in RESERVED_PREFIXES)
    reject(inbound, '@(context.Request.Headers.ContainsKey("x-threadlight-consumer-authorization")'
           f' || context.Request.Headers.Keys.Any(k => {prefixes}))')
    public_origin = "https://" + urlsplit(binding.public_url).netloc
    reject(inbound, '@(context.Request.Headers.ContainsKey("Origin")'
           f' && context.Request.Headers.GetValueOrDefault("Origin","") != "{public_origin}")')
    ET.SubElement(inbound, "set-variable", name="threadlightOriginalAuthorization",
                  value='@(context.Request.Headers.GetValueOrDefault("Authorization",""))')
    ET.SubElement(inbound, "set-variable", name="publishedBackendId", value='@(context.Api.Id)')
    ET.SubElement(inbound, "include-fragment", **{"fragment-id": "security-handler"})
    ET.SubElement(inbound, "include-fragment", **{"fragment-id": "mcp-usage"})
    jwt = ET.SubElement(inbound, "validate-jwt", **{
        "header-name": "Authorization", "require-scheme": "Bearer",
        "failed-validation-httpcode": "401", "require-expiration-time": "true",
        "require-signed-tokens": "true"})
    ET.SubElement(jwt, "openid-config", url=(
        f"https://login.microsoftonline.com/{binding.tenant_id}/v2.0/.well-known/openid-configuration"))
    ET.SubElement(ET.SubElement(jwt, "audiences"), "audience").text = binding.consumer.audience
    ET.SubElement(ET.SubElement(jwt, "issuers"), "issuer").text = (
        f"https://login.microsoftonline.com/{binding.tenant_id}/v2.0")
    claims = ET.SubElement(jwt, "required-claims")
    for name, value in {
        "tid": binding.tenant_id, "oid": binding.consumer.principal, "azp": binding.consumer.client,
        "idtyp": "app", "roles": "Governance.Workload",
    }.items():
        ET.SubElement(ET.SubElement(claims, "claim", name=name, match="all"), "value").text = value
    header(inbound, "x-threadlight-consumer-authorization",
           '@((string)context.Variables["threadlightOriginalAuthorization"])')
    ET.SubElement(inbound, "authentication-managed-identity", **{
        "resource": binding.proxy.audience, "client-id": binding.proxy.client,
        "output-token-variable-name": "threadlightProxyToken", "ignore-error": "false"})
    header(inbound, "Authorization", '@("Bearer " + (string)context.Variables["threadlightProxyToken"])')
    internal = urlsplit(binding.gateway_url)
    header(inbound, "Host", internal.netloc)
    header(inbound, "Origin", "https://" + internal.netloc)
    ET.SubElement(inbound, "set-backend-service", **{"base-url": "https://" + internal.netloc})
    ET.SubElement(inbound, "rewrite-uri", template=internal.path, **{"copy-unmatched-params": "false"})
    ET.SubElement(section["backend"], "forward-request", timeout="15", **{"buffer-response": "false"})
    ET.SubElement(section["on-error"], "include-fragment", **{"fragment-id": "raise-alert-events"})
    publish = ET.tostring(node, encoding="unicode")
    node, section = root()
    ET.SubElement(section["backend"], "base")
    inbound = section["inbound"]
    reject(inbound, f'@(context.Api.Id != "{asset_id}")')
    ET.SubElement(inbound, "set-variable", name="jwtRequired", value="true")
    for name, value in {
        "jwtAudience": binding.consumer.audience,
        "jwtIssuer": f"https://login.microsoftonline.com/{binding.tenant_id}/v2.0",
        "jwtOpenIdConfigUrl": (
            f"https://login.microsoftonline.com/{binding.tenant_id}/v2.0/.well-known/openid-configuration"),
        "requiredRoles": "Governance.Workload",
    }.items():
        ET.SubElement(inbound, "set-variable", name=name, value=value)
    ET.SubElement(inbound, "set-variable", name="contractToolApis", value=asset_id)
    ET.SubElement(inbound, "set-variable", name="contractAgentApis", value="")
    ET.SubElement(inbound, "include-fragment", **{"fragment-id": "set-asset-kind"})
    reject(inbound, '@(context.Variables.GetValueOrDefault<string>("assetKind","") != "tool")')
    ET.SubElement(inbound, "rate-limit-by-key", calls="60", **{
        "renewal-period": "60", "counter-key": '@(context.Subscription.Id + ":tool")'})
    ET.SubElement(inbound, "quota-by-key", calls="10000", **{
        "renewal-period": "2592000", "counter-key": '@(context.Subscription.Id + ":tool")'})
    return publish, ET.tostring(node, encoding="unicode")


def assemble(binding, *, producer_path, consumer_path, destination, policy_id, version):
    """Build a third immutable generation, referencing both exact owner bundles."""
    binding = parse(Binding, canonical(binding))
    roots = []
    for owner, path in ((binding.producer, producer_path), (binding.consumer, consumer_path)):
        bundle = verify_bundle(Path(path), expected_digest=owner.policy.digest)
        registry = parse(Registry, (bundle.root / "gateway-registry.json").read_bytes())
        expected = binding.registry().model_copy(update={"actions": [owner.action]})
        if registry != expected:
            raise ValueError("citadel_owner_registry_mismatch")
        roots.append(bundle)
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix=".citadel-", dir=destination.parent) as temporary:
        source = Path(temporary)
        for record in roots[0].files:
            target = source / record["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(roots[0].root / record["path"], target)
        (source / "gateway-registry.json").write_bytes(canonical(
            binding.registry().model_dump(mode="json", by_alias=True)))
        (source / "citadel-binding.json").write_bytes(canonical(binding))
        for owner, bundle in zip((binding.producer, binding.consumer), roots):
            verify_bundle(bundle.root, expected_digest=owner.policy.digest)
        return build_bundle(source=source, destination=destination, policy_id=policy_id, version=version)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--producer", required=True, type=Path)
    parser.add_argument("--consumer", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--policy-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--asset-id", required=True)
    args = parser.parse_args()
    binding = parse(Binding, args.binding.read_bytes())
    publish, access = policies(binding, asset_id=args.asset_id)
    built = assemble(binding, producer_path=args.producer, consumer_path=args.consumer,
                     destination=args.destination, policy_id=args.policy_id, version=args.version)
    print(canonical({
        "scope": "unsigned-local-artifacts-not-live-proof", "upstream": UPSTREAM,
        "apim_api_version": APIM_API_VERSION, "mcp_api_properties": mcp_api_properties(binding),
        "policy_digest": built.bundle_digest, "publish_policy_xml": raw_policy_xml(publish),
        "access_policy_xml": raw_policy_xml(access),
    }).decode())


if __name__ == "__main__":
    main()
