"""Real local collector verification, followed by read-only current artifact checks."""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys

import pytest

from test_governance_probe import native_collector_harness, packaged_collector_project, reference
from skills._shared import governance_readiness as readiness
from skills._shared.tests.governance_consumer_fixtures import write


@pytest.mark.parametrize("dependency", ["native", "association"])
def test_current_complete_envelope_matches_actually_verified_collection(tmp_path, monkeypatch, dependency):
    probe = reference("governance_probe")
    async def case():
        async with native_collector_harness(tmp_path, monkeypatch) as h:
            project, options = packaged_collector_project(tmp_path, h)
            from test_governance_gates import ROOT
            emitted = subprocess.run(
                [sys.executable, str(ROOT / "skills/threadlight-govern/scripts/govern_check.py"),
                 "--target", str(project), "--emit", "--json"], capture_output=True, text=True)
            assert emitted.returncode == 0, emitted.stderr
            inventory = json.loads(emitted.stdout)
            assert len(inventory["bindings"]) == len(h.config["contract"]["tools"])
            readiness.inventory_matches(inventory, readiness.load_contract(project))
            report = await probe.collect_project(project, options, credential=h.credential,
                signer=h.signer, run=h.run, http=h.http, timeout=8)
            assert report["governance_gaps"] == [], report
            value = report["governance_manifest"]
            write(project, "specs/governance-manifest.json", value)
            write(project, ".threadlight/governance-live.json", report)
            assert readiness.assess(project)["live"]
            path = project / ("src/agent/policy-envelope.json" if dependency == "native"
                              else ".threadlight/probe-envelope.json")
            original = path.read_bytes()
            for mutation in ("missing", "signature", "key", "expiry", "publisher", "extra"):
                signed = json.loads(original)
                if mutation == "missing":
                    del signed["signature"]
                elif mutation == "signature":
                    signed["signature"] = base64.b64encode(b"different-valid-shape-signature").decode()
                elif mutation == "key":
                    signed["envelope"]["key_id"] = signed["envelope"]["key_id"] + "different"
                elif mutation == "expiry":
                    signed["envelope"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
                elif mutation == "publisher":
                    signed["envelope"]["tenant_id"] = "22222222-2222-2222-2222-222222222222"
                else:
                    signed["envelope"]["publisher"] = "untrusted"
                path.write_text(json.dumps(signed))
                result = readiness.assess(project)
                assert not result["live"] and result["status"] != "pass", (dependency, mutation, result)
            path.write_bytes(original)
            assert readiness.assess(project)["live"]
            evidence = value["collection_evidence"]
            assert set(evidence["verified_policies"]) == {"policy", "native_policy"}
            assert "signature_verified" not in readiness.current_context(project)["policy_bundle"]
            legacy = deepcopy(value)
            del legacy["collection_evidence"]["verified_policies"]
            write(project, "specs/governance-manifest.json", legacy)
            write(project, ".threadlight/governance-live.json", {**report, "governance_manifest": legacy})
            assert not readiness.assess(project)["live"]
    asyncio.run(case())
