import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import matrix

ARMS = [
    {"arm": "mini", "model_deployment": "gpt-5.4-mini", "wire_api": "responses"},
    {"arm": "router", "model_deployment": "model-router", "wire_api": "completions"},
    {"arm": "strong", "model_deployment": "gpt-5.4", "wire_api": "responses"},
]

def test_plan_waves_groups_by_workload_no_same_deployment_overlap():
    waves = matrix.plan_waves(["returns-triage", "fsi-kyc-aml"], ARMS)
    assert len(waves) == 2
    for wave in waves:
        deps = [c["model_deployment"] for c in wave]
        assert len(deps) == len(set(deps))      # no same-deployment overlap
        assert len({c["workload"] for c in wave}) == 1

def test_dispatch_matrix_records_manifest():
    calls = []
    def fake_runner(args):
        calls.append(args)
        if args[:2] == ["run", "list"]:
            return '[{"databaseId": 999, "createdAt": "2026-06-30T10:00:00Z"}]'
        return ""
    cells = matrix.dispatch_matrix(
        ["returns-triage"], ARMS, repo="o/r", ref="br",
        runner=fake_runner, poll=False)
    assert len(cells) == 3
    assert all(c["run_id"] == 999 for c in cells)
    assert any(a[:2] == ["workflow", "run"] for a in calls)
