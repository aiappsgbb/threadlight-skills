"""The release integration retains the real native owner and fails without binding."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_verified_pipeline import framing, generator


@pytest.mark.parametrize("platform", generator.SUPPORTED_PLATFORMS)
def test_opted_in_release_uses_the_supplied_native_bridge(tmp_path, platform):
    (tmp_path / "agentops.yaml").write_text("version: 1\nagent: support:1\n")
    generator.generate(framing(platform), tmp_path)
    policy = json.loads((tmp_path / "specs/release-policy.example.json").read_text())
    assert policy["evals"]["producer"] == [
        "python3", ".threadlight/skills/threadlight-cicd/scripts/release_agentops.py"]
    assert "evals/runs/release-agentops.json" in policy["evals"]["outputs"]
    assert "specs/agentops-manifest.json" in policy["evals"]["outputs"]
    script = tmp_path / policy["evals"]["producer"][1]
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    text = script.read_text()
    assert "agentops_runtime.main" in text
    assert "load_manifest" in text
    assert "evals_check.py" in text
    assert "evaluation_targets" in text


def test_native_release_bridge_does_not_create_authority_or_run_unbound(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/release_agentops.py"
    result = subprocess.run([sys.executable, str(script), "--repo", str(tmp_path)], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert "Release native evaluation blocked:" in result.stderr
    assert not (tmp_path / ".agentops").exists()
