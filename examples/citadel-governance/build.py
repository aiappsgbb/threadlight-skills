"""Build unsigned synthetic owner bundles and generation; no cloud operations."""
import argparse
from pathlib import Path
import tempfile

import yaml

from govern_bundle.policy_bundle import build_bundle
from govern_control_plane.models import canonical, parse, strict_json
from govern_gateway.citadel import Binding
from govern_gateway.citadel_package import assemble, policies


def build(output):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("refusing_to_replace_existing_example")
    document = strict_json(Path(__file__).with_name("binding.template.json").read_bytes())
    parse(Binding, canonical(document))
    with tempfile.TemporaryDirectory(prefix=".citadel-example-", dir=output.parent) as temporary:
        stage = Path(temporary)
        for owner, condition in (
            ("producer", 'input.policy_target.value.decision == "deny_refund"'),
            ("consumer", 'input.policy_target.value.reason == "consumer-denied"'),
        ):
            source = stage / (owner + "-source")
            source.mkdir()
            binding = parse(Binding, canonical(document))
            registry = binding.registry().model_copy(update={"actions": [getattr(binding, owner).action]})
            (source / "gateway-registry.json").write_bytes(canonical(
                registry.model_dump(mode="json", by_alias=True)))
            (source / "manifest.yaml").write_text(yaml.safe_dump({
                "agent_control_specification_version": "0.3.1-beta",
                "policies": {"safe": {"type": "rego", "data": ["safe.rego"],
                                      "query": "data.citadel.pre_tool_call"}},
                "intervention_points": {"pre_tool_call": {
                    "policy_target": "$.tool_call.args",
                    "policy": {"id": "safe", "query": "data.citadel.pre_tool_call"}}},
            }))
            (source / "safe.rego").write_text(
                'package citadel\nimport rego.v1\npre_tool_call := {"decision":"deny"} if '
                + condition + ' else := {"decision":"allow"}\n')
            bundle = build_bundle(source=source, destination=stage / owner,
                                  policy_id=owner, version="1")
            document[owner]["policy"]["digest"] = bundle.bundle_digest
        binding = parse(Binding, canonical(document))
        generation = assemble(binding, producer_path=stage / "producer",
            consumer_path=stage / "consumer", destination=stage / "generation", policy_id="citadel", version="1")
        (stage / "binding.json").write_bytes(canonical(binding))
        (stage / "binding.schema.json").write_bytes(canonical(Binding.model_json_schema()))
        publish, access = policies(binding, asset_id="returns")
        (stage / "publish-policy.xml").write_text(publish)
        (stage / "access-policy.xml").write_text(access)
        (stage / "source-result.json").write_bytes(canonical({
            "scope": "synthetic-unsigned-local-only", "policy_id": "citadel", "version": "1",
            "policy_digest": generation.bundle_digest, "business_effect": "decision-not-settlement",
        }))
        for path in stage.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        stage.chmod(0o755)
        stage.rename(output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(build(parser.parse_args().output))
