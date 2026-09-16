"""MCP acceptance uses the copied producer, not a permissive summary default."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_release_acceptance import NOW, START
from test_verified_pipeline import generator, framing


@pytest.mark.parametrize("platform", generator.SUPPORTED_PLATFORMS)
def test_generated_mcp_producer_is_executable_and_complete(tmp_path, platform):
    generator.generate(framing(platform), tmp_path)
    scripts = tmp_path / ".threadlight/skills/threadlight-production-ready/scripts"
    output = tmp_path / "mcp-output.json"
    result = subprocess.run(
        [sys.executable, str(scripts / "mcp_sbom.py"), "--root", str(tmp_path),
         "--out", str(output), "--check"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert data["schema_version"] == "1.0"
    assert data["servers"] == []
    assert data["summary"]["must_fix"] == 0
    spec = importlib.util.spec_from_file_location("copied_mcp_gate", scripts / "evidence_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.validate_release_manifest("mcp", data, now=NOW, not_before=START)["status"] == "pass"
    with pytest.raises(ValueError):
        module.validate_release_manifest("mcp", {}, now=NOW, not_before=START)


@pytest.mark.parametrize("platform", generator.SUPPORTED_PLATFORMS)
def test_mcp_runs_in_validation_and_cannot_be_downgraded(tmp_path, platform):
    generator.generate(framing(platform), tmp_path)
    runner = (tmp_path / ".threadlight/skills/threadlight-cicd/scripts/release_runner.py").read_text()
    assert '"tests/mcp-sbom.json"' in runner
    assert '"--check"' in runner
    values = framing(platform)
    values["mcp_gate"] = "soft"
    with pytest.raises(ValueError):
        generator.generate(values, tmp_path)
