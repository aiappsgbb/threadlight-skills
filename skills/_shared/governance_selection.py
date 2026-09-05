"""One fail-closed declaration reader for inventory, readiness and orchestration."""
import hashlib
import json
from pathlib import Path
import re

from skills._shared.governance import validate_governance_contract
from skills._shared.probe_evidence import require

CONTRACT = "specs/governance-contract.json"
PARENT = "specs/manifest.json"
SPEC = "specs/SPEC.md"


def parse_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate-json-key")
            value[key] = item
        return value
    require(len(raw) <= 2 * 1024 * 1024, "governance-artifact-too-large")
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite-json")))
    require(isinstance(value, dict), "governance-artifact-must-be-object")
    return value


def read_json(path):
    with Path(path).open("rb") as stream:
        return parse_json(stream.read(2 * 1024 * 1024 + 1))


def parent_contract(value):
    require(not {"governance_mode", "governanceMode"} & value.keys(),
            "unsupported-governance-selector")
    return {k: value[k] for k in ("framework", "governance", "tools")} if "governance" in value else None


def spec_contract(root, path=None):
    path = Path(path) if path is not None else Path(root) / SPEC
    if not path.exists() and not path.is_symlink():
        return None, None, None
    import yaml

    class SpecLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            mapping = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in mapping:
                    raise ValueError("SPEC governance mapping key invalid or duplicated")
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping

    SpecLoader.yaml_implicit_resolvers = {
        key: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"]
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    SpecLoader.add_implicit_resolver(
        "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.I), list("tTfF"))
    data = _read_source(root, path)
    text = data.decode("utf-8")
    candidates = []
    declaration = re.compile(
        r'(?im)^\s*(?:[-*]\s*)?(?:\*\*|`)?governance(?:[_ ]?mode)?(?:\*\*|`)?\s*:'
        r'|["\']governance(?:_mode|Mode)?["\']\s*:'
        r'|^#{1,6}\s+(?:\d+[a-z]?\.\s*)?Runtime Governance Contract\b')
    for block in re.findall(r"```(?:yaml|yml)\s*\n(.*?)```", text, re.S):
        try:
            document = yaml.load(block, Loader=SpecLoader)
        except (yaml.YAMLError, ValueError):
            if declaration.search(block):
                raise ValueError("SPEC governance YAML invalid") from None
            continue
        if isinstance(document, dict) and any(
                key in document for key in ("governance", "governance_mode", "governanceMode")):
            candidates.append(document)
    if not candidates and not declaration.search(text):
        return None, None, None
    require(len(candidates) == 1, "SPEC must contain exactly one explicit governance contract")
    return candidates[0], path, data


def _read_source(root, path):
    root, path = Path(root).resolve(), Path(path)
    require(path.resolve().is_relative_to(root)
            and not any(p.is_symlink() for p in (path, *path.parents) if p != root.parent),
            "contract-source-outside-project-or-symlinked")
    with path.open("rb") as stream:
        data = stream.read(2 * 1024 * 1024 + 1)
    require(len(data) <= 2 * 1024 * 1024, "governance-artifact-too-large")
    return data


def discover_contract(root, *, required=True, contract_path=None, manifest_path=None, spec_path=None):
    root = Path(root)
    documents = []
    for name, explicit, parent in ((CONTRACT, contract_path, False), (PARENT, manifest_path, True)):
        path = Path(explicit) if explicit is not None else Path(name)
        path = path if path.is_absolute() else root / path
        if path.exists() or path.is_symlink() or explicit is not None:
            data = _read_source(root, path)
            value = parse_json(data)
            document = parent_contract(value) if parent else value
            if document is not None:
                documents.append((document, path, data))
    path = Path(spec_path) if spec_path is not None else root / SPEC
    if spec_path is not None and not path.is_absolute():
        path = root / path
    document = spec_contract(root, path)
    if document[0] is not None:
        documents.append(document)
    if not documents and not required:
        return None, None, None
    require(bool(documents), "explicit-governance-contract-missing")
    normalized = [validate_governance_contract(d[0], deployment_target="demo-sandbox") for d in documents]
    require(all(d == normalized[0] for d in normalized), "governance-contract-mirrors-disagree")
    return documents[0]


def load_contract(root, **kwargs):
    return discover_contract(root, **kwargs)[0]


def source_digest(root, source, data=None):
    """Parent deployment output changes do not rewrite the declared inventory."""
    data = _read_source(root, Path(root) / source) if data is None else data
    if source == PARENT:
        document = parent_contract(parse_json(data))
        require(document is not None, "parent-contract-missing")
        data = json.dumps(validate_governance_contract(document, deployment_target="demo-sandbox"),
                          sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()
