"""Corrected official vectors, real MAF loop and production boundary factory.

CTK controls/resolvers are the official scripted test oracles, not ACS mocks.
The complementary provider suite evaluates real ACS/OPA through the same bundle.
No transcripts, recorded contexts, vectors or outcomes are rewritten.
"""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.governance_runtime


@pytest.fixture(scope="module")
def ctk():
    sys.path.insert(0, str(ROOT / "scripts/ci"))
    import governance_ctk
    official = governance_ctk.activate()
    from test_runtime_provider import runtime
    adapter = runtime()
    original_agent = official.Agent

    class BoundaryProvider:
        """CTK supplies controls; install their native bundle through our factory."""
        _claimed = False
        _bindings = {}
        max_output_bytes = 1024 * 1024

        def __init__(self, bundle):
            self.bundle = bundle

        def middleware(self):
            return self.bundle

    def create_agent(**kwargs):
        middleware = kwargs.pop("middleware")
        return adapter.create_governed_agent(
            BoundaryProvider(middleware[0]), middleware=middleware, **kwargs,
        )

    official.Agent = create_agent
    official.create_agent_hooks_middleware = adapter.hooks_bundle
    yield official
    official.Agent = original_agent


@pytest.mark.parametrize("vector_id", [f"AH-CTK-{n:03}" for n in (
    1, 2, 3, 10, 11, 12, 20, 21, 22, 30, 31, 32, 40, 50, 60, 61,
    70, 71, 72, 73, 74, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89,
    90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105,
)])
def test_corrected_official_ctk(ctk, vector_id):
    from agent_hooks.ctk.runner import load_vectors, run_vector
    vectors = load_vectors()
    assert len(vectors) == 51
    vector = next(v for v in vectors if v["id"] == vector_id)
    result = asyncio.run(run_vector(ctk.AgentFrameworkHarness(), vector))
    artifact = ROOT / ".governance-validation/ctk/results"
    artifact.mkdir(exist_ok=True)
    (artifact / f"{vector_id}.json").write_text(json.dumps(asdict(result), indent=2))
    assert result.status == "pass", (result.status, result.failures, result.detail)


def test_undeclared_incremental_vectors_are_explicitly_reported(ctk):
    from agent_hooks.ctk.runner import load_vectors, run_vector
    from agent_hooks.ctk.harness import Capability
    assert Capability.INCREMENTAL_OUTPUT not in ctk.AgentFrameworkHarness.capabilities
    vectors = [v for v in load_vectors() if "incremental_output" in v["capabilities"]]
    assert len(vectors) == 4
    results = [asdict(asyncio.run(run_vector(ctk.AgentFrameworkHarness(), v))) for v in vectors]
    assert all(r["status"] == "skip" and r["detail"] for r in results)
    (ROOT / ".governance-validation/ctk/undeclared-capabilities.json").write_text(
        json.dumps(results, indent=2)
    )
