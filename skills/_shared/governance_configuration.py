"""Closed Task10 environment projection: retain config digests, never env payloads."""
from hashlib import sha256
import json
import re

# These are Task10 compose()/governance.bicep wire names, not prefix heuristics.
AGENT_ENVIRONMENT = frozenset({
    "GOV_CONTROL_PLANE_URL", "GOVERNED_TOOL_GATEWAY_URL", "TL_GOV_IMAGE_DIGEST", "TL_GOV_SPOOL_DIR",
})
SERVICE_ENVIRONMENT = frozenset({
    "TL_GOV_SERVICE", "AZURE_CLIENT_ID", "GOV_CONFIG_JSON", "GATEWAY_CONFIG_JSON",
})
DECLARED_FILES = frozenset({"host", "fixture", "native_probe"})


def validate_digests(value, names):
    if (not isinstance(value, dict) or not set(value) <= names
            or any(not isinstance(v, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", v) for v in value.values())):
        raise ValueError("governance-configuration-digests-invalid")
    return dict(value)


def environment_values(*sources, names=AGENT_ENVIRONMENT):
    values = {}
    for source in sources:
        if not isinstance(source, (dict, list)) or len(source) > 512:
            raise ValueError("governance-environment-invalid")
        entries = [{"name": k, "value": v} for k, v in source.items()] if isinstance(source, dict) else source
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("governance-environment-invalid")
            name = entry.get("name")
            if not isinstance(name, str) or name not in names:
                continue
            value = entry.get("value")
            if (name in seen or "secretRef" in entry or not isinstance(value, str)
                    or not value or len(value) > (65536 if name.endswith("_CONFIG_JSON") else 2048)
                    or name in values and values[name] != value):
                raise ValueError("governance-environment-unresolved-or-conflicting")
            seen.add(name)
            values[name] = value
    return values


def configuration_digest(value):
    return "sha256:" + sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def project_environment(*sources, names=AGENT_ENVIRONMENT):
    """Hash only named settings. Strict service schemas bind roles/scopes/policy too."""
    from govern_control_plane.models import parse
    values = environment_values(*sources, names=names)
    digests = {}
    for name, value in values.items():
        if "${" in value or value.startswith("@Microsoft.KeyVault("):
            raise ValueError("governance-environment-unresolved")
        if name in ("GOV_CONFIG_JSON", "GATEWAY_CONFIG_JSON"):
            from govern_control_plane.app import AzureConfiguration
            from govern_gateway.server import Configuration
            schema = AzureConfiguration if name == "GOV_CONFIG_JSON" else Configuration
            try:
                value = parse(schema, value.encode()).model_dump(mode="json")
            except Exception:
                raise ValueError("governance-service-configuration-invalid") from None
        digests[name] = configuration_digest(value)
    return digests
