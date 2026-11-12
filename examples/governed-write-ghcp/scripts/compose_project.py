#!/usr/bin/env python3
"""Fold the kit's business API and policy publisher into the generated pilot azd project.

Run once, after ``generate.py foundation`` and before the first ``azd provision``:

    python scripts/compose_project.py --project <pilot>

It makes one azd project own every build and deployment of the kit:

* ``infra/business.bicep`` becomes a module of ``infra/main.bicep`` (business
  Cosmos, writer identity, the business API and the publisher job), wired to the
  pilot's registry, Container Apps environment and MCP identity and to the
  governance foundation's publisher identity. The pilot MCP server reads the
  business Cosmos endpoint from that module, so one ``azd provision`` creates
  both and no hand-copied endpoint is needed.
* ``src/mcp`` (the kit's read-only MCP server), ``src/returns-business``
  (``package_returns_mcp.py`` output) and ``src/govern-publisher`` (portable
  control plane plus ``publish_policy.py``) are build contexts with
  ``docker.remoteBuild: true`` (ACR builds, no local Docker).
* ``returns-business`` and ``govern-publisher`` are built with ``azd publish``
  and bound by digest: set ``RETURNS_BUSINESS_IMAGE`` / ``GOVERN_PUBLISHER_IMAGE``
  with ``azd env set`` and run ``azd provision`` again. Their ``predeploy`` hook
  refuses ``azd deploy``. Write the business service configuration (final policy
  digest, registry deployment, identities) to ``infra/business-config.json``
  before deploying the business image; it is ``{}`` until then.

The input project is a copy of the GHCP pilot template; the template itself is
never edited.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

EXAMPLE = Path(__file__).resolve().parents[1]
CATALOG = EXAMPLE.parents[1]
GOVERNANCE = CATALOG / "skills/threadlight-deploy/references/governance"
CONTROL_PLANE = CATALOG / "skills/threadlight-govern/references/control-plane"
# Public MCR base pinned by digest (ACR Tasks cannot parse FROM --platform with ARG).
PYTHON_IMAGE = ("mcr.microsoft.com/oryx/python:3.12@sha256:"
                "6f1b91cead9a8327af947065bb0bfe2e8a8a9b0c04e9aa5404657718035f4e13")
GUARD = "echo 'Image is published (azd publish) and bound by digest in Bicep.' >&2; exit 1"
PUBLISHER_DOCKERFILE = """ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY vendor/ ./vendor/
RUN python -m pip install --no-cache-dir ./vendor/control-plane && useradd --uid 10001 publisher
COPY publish_policy.py ./
USER 10001
CMD ["python", "publish_policy.py"]
"""
COSMOS_PARAM = re.compile(r"@description\([^\n]*\)\nparam returnsCosmosUrl string = ''\n")
MODULE = """
@description('Business API image by digest (azd publish returns-business); empty skips the app.')
param businessImage string = ''
@description('Policy publisher image by digest (azd publish govern-publisher); empty skips the job.')
param publisherImage string = ''
var returnsCosmosUrl = business.outputs.RETURNS_COSMOS_URL
module business 'business.bicep' = {
  name: 'returns-business'
  params: {
    tags: union(allTags, governanceConfig.?tags ?? {})
    prefix: governanceConfig.prefix
    acrName: acr.name
    environmentId: cae.id
    mcpReaderPrincipalId: uami.properties.principalId
    operatorPrincipalId: principalId
    publisherIdentityId: resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', '${governanceConfig.prefix}-publisher')
    publisherClientId: governance.outputs.TL_GOV_PUBLISHER_CLIENT
    publisherImage: publisherImage
    publisherEnv: [
      { name: 'TL_TENANT_ID', value: tenant().tenantId }
      { name: 'TL_KEY_ID', value: governance.outputs.TL_GOV_KEY_VERSION }
      { name: 'TL_POLICY_ID', value: governanceConfig.?policy_id ?? 'returns-write' }
      { name: 'TL_BLOB_URL', value: governance.outputs.TL_GOV_BLOB_URL }
      { name: 'TL_BLOB_CONTAINER', value: 'signed-catalog' }
    ]
    businessImage: businessImage
    businessConfig: loadJsonContent('business-config.json')
  }
  dependsOn: [governance]
}
output RETURNS_COSMOS_URL string = business.outputs.RETURNS_COSMOS_URL
output RETURNS_BUSINESS_URL string = business.outputs.RETURNS_BUSINESS_URL
"""


def _service(project):
    return {"host": "containerapp", "project": project, "language": "docker",
            "docker": {"path": "Dockerfile", "context": ".", "remoteBuild": True,
                       "buildArgs": [f"PYTHON_IMAGE={PYTHON_IMAGE}"]},
            "hooks": {"predeploy": {"shell": "sh", "run": GUARD}}}


def compose(project):
    import yaml
    project = Path(project)
    main = project / "infra/main.bicep"
    text = main.read_text()
    if "module governance 'governance.bicep'" not in text:
        raise ValueError("run_generate_foundation_first")
    if "module business 'business.bicep'" in text:
        raise ValueError("already_composed")
    text, count = COSMOS_PARAM.subn("", text)
    if count != 1:
        raise ValueError("pilot_template_changed_returnsCosmosUrl")
    main.write_text(text + MODULE)
    shutil.copyfile(EXAMPLE / "infra/business.bicep", project / "infra/business.bicep")
    (project / "infra/business-config.json").write_text("{}\n")
    path = project / "infra/main.parameters.json"
    payload = json.loads(path.read_text())
    payload["parameters"].pop("returnsCosmosUrl", None)
    payload["parameters"].update({"businessImage": {"value": "${RETURNS_BUSINESS_IMAGE=}"},
                                  "publisherImage": {"value": "${GOVERN_PUBLISHER_IMAGE=}"}})
    path.write_text(json.dumps(payload, indent=2) + "\n")

    source = project / "src"
    source.mkdir(exist_ok=True)
    shutil.rmtree(source / "mcp", ignore_errors=True)
    shutil.copytree(EXAMPLE / "src/mcp", source / "mcp", ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run([sys.executable, str(GOVERNANCE / "package_returns_mcp.py"),
                    "--output", str(source / "returns-business")], check=True, capture_output=True)
    publisher = source / "govern-publisher"
    vendor = publisher / "vendor/control-plane"
    vendor.mkdir(parents=True)
    for item in CONTROL_PLANE.iterdir():
        if item.suffix in (".py", ".toml"):
            shutil.copyfile(item, vendor / item.name)
    shutil.copyfile(EXAMPLE / "scripts/publish_policy.py", publisher / "publish_policy.py")
    (publisher / "Dockerfile").write_text(PUBLISHER_DOCKERFILE)

    azure_path = project / "azure.yaml"
    azure = yaml.safe_load(azure_path.read_text())
    azure["services"]["mcp"] = {"host": "containerapp", "project": "src/mcp", "language": "docker",
                                "docker": {"path": "Dockerfile", "context": ".", "remoteBuild": True}}
    azure["services"]["returns-business"] = _service("src/returns-business")
    azure["services"]["govern-publisher"] = _service("src/govern-publisher")
    azure_path.write_text(yaml.safe_dump(azure, sort_keys=False))
    return {"status": "composed-not-provisioned", "services": ["mcp", "returns-business", "govern-publisher"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=Path, required=True, help="pilot project after generate.py foundation")
    print(json.dumps(compose(parser.parse_args().project)))


if __name__ == "__main__":
    main()
