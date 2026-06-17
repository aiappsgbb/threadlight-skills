"""Defense-in-depth: NO emitted artifact may contain a long-lived secret.

Threadlight CI/CD is OIDC / Workload-Identity-Federation only. This test
renders every platform x path combination and asserts none of the written
files carry a client secret, an AZURE_CREDENTIALS JSON blob, or a PAT.
"""
import importlib.util
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "generate_pipeline", ROOT / "scripts" / "generate_pipeline.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["generate_pipeline"] = mod
_spec.loader.exec_module(mod)

FORBIDDEN = [
    "AZURE_CREDENTIALS",
    "client-secret",
    "clientSecret",
    "--password",
    "PERSONAL_ACCESS_TOKEN",
    "System.AccessToken",  # not needed for WIF; flag if it sneaks in
]


def _matrix():
    base = {
        "target_subscription_id": "11111111-1111-1111-1111-111111111111",
        "target_resource_group": "rg-pilot-prod",
        "target_location": "eastus2",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "env_name": "prod",
    }
    gh = dict(base, platform="github-actions", repo_full_name="aiappsgbb/contoso-pilot")
    ado = dict(base, platform="azure-devops", ado_org="contoso",
               ado_project="AI-Pilots", ado_service_connection="sc-contoso-pilot-prod")
    combos = []
    for plat in (gh, ado):
        for req, exists in ((False, None), (True, True), (True, False)):
            for priv in (False, True):
                combos.append(dict(plat, central_env_required=req,
                                   central_env_exists=exists, private_network=priv))
    return combos


def test_no_emitted_file_contains_a_long_lived_secret():
    for framing in _matrix():
        tmp = pathlib.Path(tempfile.mkdtemp())
        written = mod.generate(framing, out_root=tmp)
        for p in written:
            text = p.read_text(encoding="utf-8")
            for needle in FORBIDDEN:
                assert needle not in text, (
                    f"{needle!r} leaked into {p.name} for platform={framing['platform']} "
                    f"required={framing['central_env_required']} priv={framing.get('private_network')}"
                )


def test_no_emitted_file_has_unresolved_tokens():
    for framing in _matrix():
        tmp = pathlib.Path(tempfile.mkdtemp())
        written = mod.generate(framing, out_root=tmp)
        for p in written:
            if p.suffix == ".json":
                continue
            text = p.read_text(encoding="utf-8")
            leftovers = re.findall(r"\{\{[A-Z_]+\}\}", text)
            assert leftovers == [], f"Unresolved tokens {leftovers} in {p.name}"
