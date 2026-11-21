"""#220: served-model attribution and subset gating for model router behind the hub."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "references" / "python"))
FRAGMENT = SKILL / "references" / "policies" / "frag-set-llm-served-model.xml"

import router_contract_check as rcc  # noqa: E402

POLICY = """<policies><inbound><base />
  <set-variable name="allowedModels" value="gpt-6-luna,mr-open,mr-subset" />
</inbound></policies>"""

DEPLOYMENTS = [
    {"name": "gpt-6-luna", "properties": {"model": {"name": "gpt-6-luna", "version": "2026-09-22"}}},
    {"name": "mr-open", "properties": {"model": {"name": "model-router", "version": "2025-11-18"}}},
    {"name": "mr-subset", "properties": {"model": {"name": "model-router", "version": "2025-11-18"},
                                         "routing": {"mode": "balanced", "models": [
                                             {"format": "OpenAI", "name": "gpt-6-luna", "version": "2026-09-22"},
                                             {"format": "OpenAI", "name": "gpt-6.1-sol", "version": "2026-09-29"}]}}},
]


def test_allowed_models_parsed_from_contract_policy_xml():
    assert rcc.allowed_models(POLICY) == ["gpt-6-luna", "mr-open", "mr-subset"]


def test_router_without_subset_is_rejected():
    result = rcc.check(["gpt-6-luna", "mr-open", "mr-subset"], DEPLOYMENTS)
    codes = [(f["code"], f["deployment"]) for f in result["violations"]]
    assert codes == [("ROUTER-SUBSET-001", "mr-open")]
    assert result["ok"] is False
    assert result["routers"]["mr-subset"]["subset"] == ["gpt-6-luna", "gpt-6.1-sol"]


def test_router_with_subset_passes():
    assert rcc.check(["gpt-6-luna", "mr-subset"], DEPLOYMENTS)["ok"] is True


def test_auto_is_rejected_because_it_routes_the_whole_pool():
    result = rcc.check(["auto"], DEPLOYMENTS)
    assert [f["code"] for f in result["violations"]] == ["ROUTER-AUTO-001"]


def test_unresolved_allowed_model_fails_closed():
    # A wrong account or a router on another backend must not pass unchecked.
    result = rcc.check(["gpt-6-luna", "missing"], DEPLOYMENTS)
    assert [f["code"] for f in result["violations"]] == ["UNRESOLVED"]
    assert result["ok"] is False


def test_empty_allowed_list_checks_every_router_deployment():
    # An empty allowedModels grants every deployment, so every router is gated.
    result = rcc.check([], DEPLOYMENTS)
    assert [(f["code"], f["deployment"]) for f in result["violations"]] == [("ROUTER-SUBSET-001", "mr-open")]
    assert rcc.check([], DEPLOYMENTS[:1] + DEPLOYMENTS[2:])["ok"] is True


def test_allowed_models_tolerates_empty_value():
    assert rcc.allowed_models('<set-variable name="allowedModels" value="" />') == []


def test_cli_exit_codes(tmp_path):
    pol, deps = tmp_path / "p.xml", tmp_path / "d.json"
    pol.write_text(POLICY)
    deps.write_text(json.dumps(DEPLOYMENTS))
    script = SKILL / "references" / "python" / "router_contract_check.py"
    bad = subprocess.run([sys.executable, str(script), "--policy-xml", str(pol), "--deployments", str(deps)],
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "ROUTER-SUBSET-001" in bad.stdout
    pol.write_text(POLICY.replace("mr-open,", ""))
    ok = subprocess.run([sys.executable, str(script), "--policy-xml", str(pol), "--deployments", str(deps)],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr


# ---- served-model fragment (static; LIVE proves the expressions) ----

def test_fragment_emits_served_model_dimension_within_limits():
    # APIM raw policy XML (quotes inside expressions), so check structure, not strict XML.
    text = FRAGMENT.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<fragment>") and text.rstrip().endswith("</fragment>")
    blocks = re.findall(r'<emit-metric name="([^"]+)"[^>]*namespace="llm-served-model">(.*?)</emit-metric>',
                        text, re.S)
    assert {name for name, _ in blocks} == {"Served Prompt Tokens", "Served Completion Tokens"}
    for _, body in blocks:
        dims = re.findall(r'<dimension name="([^"]+)"', body)
        assert len(dims) <= 5  # emit-metric limit
        assert "servedModel" in dims and "deploymentName" in dims
    assert text.count("<choose>") == text.count("</choose>")


def test_fragment_falls_back_to_routing_trace_and_normalizes_versions():
    text = FRAGMENT.read_text(encoding="utf-8")
    assert "routing_trace" in text and "model_selection_details" in text
    assert re.search(r'-\\\\d\{4\}-\\\\d\{2\}-\\\\d\{2\}\$', text)
    assert "text/event-stream" in text  # streaming is not buffered
    assert "preserveContent: true" in text


def test_fragment_guards_json_null_usage_and_trace_entries():
    text = FRAGMENT.read_text(encoding="utf-8")
    assert text.count('["usage"] as JObject') == 2
    assert '["attempts"] as JArray' in text and "as JObject" in text
