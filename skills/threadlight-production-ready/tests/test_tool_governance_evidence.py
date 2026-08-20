from __future__ import annotations

from contextlib import contextmanager
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path

import pytest

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
REPO_ROOT = SKILL_DIR.parent.parent
PR_SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"
SAFE_CHECK_SCRIPT = REPO_ROOT / "skills" / "threadlight-safe-check" / "scripts" / "safe_check.py"
GOVERNED_FIXTURE = (
    REPO_ROOT
    / "skills"
    / "threadlight-safe-check"
    / "tests"
    / "fixtures"
    / "tool-governance-enabled"
)
SCRATCH_ROOT = TEST_DIR / "_scratch_tool_governance"

sys.path.insert(0, str(PR_SCRIPT.parent))
import production_ready as pr  # noqa: E402

sys.path.insert(0, str(SAFE_CHECK_SCRIPT.parent))
import safe_check as sc  # noqa: E402

_COUNTER = count()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    _write(path, json.dumps(payload, indent=2) + "\n")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def _repo_copy(name: str) -> Path:
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    root = SCRATCH_ROOT / f"{name}-{next(_COUNTER)}"
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(GOVERNED_FIXTURE, root)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _manifest(repo: Path) -> dict[str, object]:
    return _read_json(repo / "specs" / "manifest.json")


def _contract(repo: Path) -> dict[str, object]:
    return dict(_manifest(repo)["tool_governance"])


def _adapter(repo: Path) -> dict[str, object]:
    return _read_json(repo / "policies" / "tool-governance" / "adapter-manifest.json")


def _probe_path(repo: Path) -> Path:
    return repo / "tests" / "tool-governance-probe-manifest.json"


def _design_path(repo: Path) -> Path:
    return repo / "tests" / "safe-check-design-manifest.json"


def _postdeploy_path(repo: Path) -> Path:
    return repo / "tests" / "postdeploy-manifest.json"


def _contract_hash(repo: Path) -> str:
    return sc.canonical_sha256(_manifest(repo)["tool_governance"])


def _adapter_hash(repo: Path) -> str:
    return sc.canonical_sha256(_adapter(repo))


def _set_runtime(repo: Path, *, framework: str, runtime_shape: str = "agent", protocol: str = "invocations") -> None:
    _write(
        repo / "specs" / "foundation.md",
        "# Foundation\n\n```yaml\n"
        f"framework: {framework}\n"
        f"runtime_shape: {runtime_shape}\n"
        f"protocol: {protocol}\n"
        "policy_route: default-agent\n"
        "source: provided\n"
        "```\n",
    )
    adapter = _adapter(repo)
    adapter["runtime"] = {
        "framework": framework,
        "runtime_shape": runtime_shape,
        "protocol": protocol,
    }
    _write_json(repo / "policies" / "tool-governance" / "adapter-manifest.json", adapter)


def _set_all_bindings(
    repo: Path,
    *,
    enforcement_point: str,
    signal_kind: str,
    signal_path: str,
    signal_text: str,
) -> None:
    manifest = _manifest(repo)
    for tool in manifest["tool_governance"]["tools"]:
        tool["enforcement_point"] = enforcement_point
    _write_json(repo / "specs" / "manifest.json", manifest)
    adapter = _adapter(repo)
    for binding in adapter["bindings"]:
        binding["enforcement_point"] = enforcement_point
        binding["wire_signals"] = [{"path": signal_path, "kind": signal_kind}]
    _write_json(repo / "policies" / "tool-governance" / "adapter-manifest.json", adapter)
    _write(repo / signal_path, signal_text)


def _fresh_govern_manifest() -> dict[str, object]:
    return {
        "schema": "threadlight-govern-manifest/v2",
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": "governed",
        "capabilities": {
            key: {"status": "pass"}
            for key in (
                "policy_artefact_present",
                "policy_schema_valid",
                "policy_versioned",
                "policy_default_deny",
                "sensitive_action_rules_present",
                "policy_tests_present",
                "ci_gate_present",
                "attestation_present",
                "attestation_fresh",
                "asi_reference_present",
            )
        },
    }


def _sync_policy_artifacts(repo: Path) -> None:
    adapter = _adapter(repo)
    contract_hash = _contract_hash(repo)
    adapter["contract_sha256"] = contract_hash
    _write_json(repo / "policies" / "tool-governance" / "adapter-manifest.json", adapter)
    for binding in adapter.get("bindings", []):
        if not isinstance(binding, dict):
            continue
        rel = binding.get("policy_artifact")
        if not isinstance(rel, str) or not rel:
            continue
        payload = {
            "schema": "threadlight.tool-governance/test-policy/v1",
            "contract_sha256": contract_hash,
        }
        _write_json(repo / rel, payload)


def _run_probe(repo: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(repo / "tests" / "tool_governance_probe.py"),
            "--out",
            "tests/tool-governance-probe-manifest.json",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def _emit_design_manifest(repo: Path) -> None:
    rc = sc.phase_design(repo / "specs" / "manifest.json", _design_path(repo))
    assert rc == 0, _design_path(repo).read_text(encoding="utf-8")


def _write_postdeploy_summary(repo: Path) -> None:
    manifest = _manifest(repo)
    block = manifest.get("tool_governance")
    tool_governance: dict[str, object]
    if not isinstance(block, dict) or block.get("enabled") is not True:
        tool_governance = {"enabled": False, "status": "not-applicable"}
    else:
        tool_governance = {
            "enabled": True,
            "status": "pass",
            "contract_sha256": _contract_hash(repo),
            "adapter_manifest_sha256": _adapter_hash(repo),
            "evidence": "tests/tool-governance-probe-manifest.json",
        }
    payload = {
        "phase": "post-deploy",
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployment_manifest": manifest.get("deployment_manifest", {}),
        "tool_governance": tool_governance,
        "gaps": [],
    }
    _write_json(_postdeploy_path(repo), payload)


def _prepare_repo(repo: Path) -> None:
    _sync_policy_artifacts(repo)
    _run_probe(repo)
    _emit_design_manifest(repo)
    _write_postdeploy_summary(repo)


def _ctx(repo: Path, manifest_override: dict[str, object] | None = None) -> "pr.RepoContext":
    return pr.RepoContext.from_repo(
        repo,
        manifest_override if manifest_override is not None else _read_json(_postdeploy_path(repo)),
    )


def _by_id(findings) -> dict[str, pr.Finding]:
    return {finding.id: finding for finding in findings}


def _mutate_adapter(repo: Path, mutator) -> None:
    adapter = _adapter(repo)
    mutator(adapter)
    _write_json(repo / "policies" / "tool-governance" / "adapter-manifest.json", adapter)


def test_tool_governance_passes_for_ghcp_fixture_using_canonical_manifest() -> None:
    with _repo_copy("governance-pass") as repo:
        _prepare_repo(repo)
        ctx = _ctx(repo)

        findings = _by_id(pr._check_tool_governance_static(ctx))

        assert findings["AGT-007"].status == "pass"
        assert findings["AGT-008"].status == "pass"
        assert findings["AGT-103"].status == "pass"


def test_tool_governance_ignores_ctx_manifest_as_contract_authority() -> None:
    with _repo_copy("governance-canonical-authority") as repo:
        _prepare_repo(repo)
        poisoned = _read_json(_postdeploy_path(repo))
        poisoned["tool_governance"] = {
            "enabled": False,
            "status": "not-applicable",
            "contract_sha256": "sha256:poisoned",
            "evidence": "tests/other.json",
        }

        findings = _by_id(pr._check_tool_governance_static(_ctx(repo, poisoned)))

        assert findings["AGT-007"].status == "pass"
        assert findings["AGT-008"].status == "pass"
        assert findings["AGT-103"].status == "pass"


def test_disabled_tool_governance_is_not_applicable() -> None:
    with _repo_copy("governance-disabled") as repo:
        manifest = _manifest(repo)
        manifest["tool_governance"]["enabled"] = False
        _write_json(repo / "specs" / "manifest.json", manifest)
        _emit_design_manifest(repo)
        _write_postdeploy_summary(repo)
        ctx = _ctx(repo)

        findings = _by_id(pr._check_tool_governance_static(ctx))

        assert {fid: findings[fid].status for fid in ("AGT-007", "AGT-008", "AGT-103")} == {
            "AGT-007": "not-applicable",
            "AGT-008": "not-applicable",
            "AGT-103": "not-applicable",
        }


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda repo: _design_path(repo).unlink(), "missing"),
        (
            lambda repo: _write_json(
                _design_path(repo),
                {
                    **_read_json(_design_path(repo)),
                    "tool_governance": {
                        **_read_json(_design_path(repo))["tool_governance"],
                        "status": "fail",
                    },
                },
            ),
            "status",
        ),
        (
            lambda repo: _write_json(
                _design_path(repo),
                {
                    **_read_json(_design_path(repo)),
                    "tool_governance": {
                        **_read_json(_design_path(repo))["tool_governance"],
                        "contract_sha256": "sha256:deadbeef",
                    },
                },
            ),
            "hash",
        ),
    ],
)
def test_agt007_requires_passing_design_evidence(mutation, expected: str) -> None:
    with _repo_copy(f"agt007-{expected}") as repo:
        _prepare_repo(repo)
        mutation(repo)
        findings = _by_id(pr._check_tool_governance_static(_ctx(repo)))
        assert findings["AGT-007"].status == "must-fix", findings["AGT-007"].detail


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("missing-binding", lambda repo: _mutate_adapter(repo, lambda adapter: adapter["bindings"].pop())),
        ("duplicate-binding", lambda repo: _mutate_adapter(repo, lambda adapter: adapter["bindings"].append(dict(adapter["bindings"][0])))),
        (
            "extra-binding",
            lambda repo: _mutate_adapter(
                repo,
                lambda adapter: adapter["bindings"].append(
                    {
                        "tool_name": "surprise_tool",
                        "enforcement_point": "mcp-server",
                        "adapter_id": "mcp-tool-governance",
                        "policy_artifact": "policies/tool-governance/generated/mcp-policy.json",
                        "wire_signals": [{"path": "src/mcp/server.py", "kind": "mcp-server-policy-binding"}],
                    }
                ),
            ),
        ),
        (
            "runtime-mismatch",
            lambda repo: _mutate_adapter(repo, lambda adapter: adapter["runtime"].update({"protocol": "responses"})),
        ),
        (
            "runtime-unknown",
            lambda repo: _set_runtime(repo, framework="unsupported-framework") or None,
        ),
        (
            "ghcp-middleware",
            lambda repo: _set_all_bindings(
                repo,
                enforcement_point="agent-middleware",
                signal_kind="pre-tool-policy-binding",
                signal_path="src/agent/runtime.py",
                signal_text="agent_os.integrations\npre_tool_call\n",
            )
            or _set_runtime(repo, framework="github-copilot-sdk"),
        ),
        (
            "policy-missing",
            lambda repo: (repo / "policies" / "tool-governance" / "generated" / "mcp-policy.json").unlink(),
        ),
        (
            "policy-hash-missing",
            lambda repo: _write_json(
                repo / "policies" / "tool-governance" / "generated" / "mcp-policy.json",
                {"schema": "threadlight.tool-governance/test-policy/v1"},
            ),
        ),
        (
            "signal-missing",
            lambda repo: _write(repo / "src" / "mcp" / "server.py", "THREADLIGHT_CANARY_ONLY = True\n"),
        ),
        (
            "bad-audit",
            lambda repo: _mutate_adapter(
                repo,
                lambda adapter: adapter.setdefault("audit", {}).update({"schema": "bad", "sink": ""}),
            ),
        ),
        ("bad-probe", lambda repo: _mutate_adapter(repo, lambda adapter: adapter.update({"probe": {}}))),
    ],
)
def test_agt008_fails_closed_for_adapter_wiring_gaps(name: str, mutate) -> None:
    with _repo_copy(f"agt008-{name}") as repo:
        _prepare_repo(repo)
        mutate(repo)
        findings = _by_id(pr._check_tool_governance_static(_ctx(repo)))
        assert findings["AGT-008"].status == "must-fix", findings["AGT-008"].detail


@pytest.mark.parametrize(
    ("name", "framework", "enforcement_point", "signal_kind", "signal_path", "signal_text"),
    [
        (
            "maf-pre-tool",
            "microsoft-agent-framework",
            "agent-middleware",
            "pre-tool-policy-binding",
            "src/agent/container.py",
            "agent_os.integrations\npre_tool_call\n",
        ),
        (
            "gateway",
            "github-copilot-sdk",
            "gateway",
            "gateway-policy-binding",
            "src/gateway/adapter.py",
            'BINDING = "threadlight.tool-governance/gateway/v1"\n',
        ),
        (
            "dotnet",
            "dotnet-harness",
            "agent-middleware",
            "dotnet-with-governance",
            "src/agent/Program.cs",
            "builder.WithGovernance(policy);\n",
        ),
    ],
)
def test_agt008_accepts_supported_signal_kinds(
    name: str,
    framework: str,
    enforcement_point: str,
    signal_kind: str,
    signal_path: str,
    signal_text: str,
) -> None:
    with _repo_copy(f"agt008-signal-{name}") as repo:
        _set_runtime(repo, framework=framework)
        _set_all_bindings(
            repo,
            enforcement_point=enforcement_point,
            signal_kind=signal_kind,
            signal_path=signal_path,
            signal_text=signal_text,
        )
        _prepare_repo(repo)
        findings = _by_id(pr._check_tool_governance_static(_ctx(repo)))
        assert findings["AGT-008"].status == "pass", findings["AGT-008"].detail


@pytest.mark.parametrize(
    ("name", "mutator"),
    [
        (
            "missing-adapter",
            lambda repo: (repo / "policies" / "tool-governance" / "adapter-manifest.json").unlink(),
        ),
        ("missing", lambda repo: _probe_path(repo).unlink()),
        (
            "stale",
            lambda repo: _write_json(
                _probe_path(repo),
                {**_read_json(_probe_path(repo)), "generated_at": (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            ),
        ),
        (
            "schema",
            lambda repo: _write_json(_probe_path(repo), {**_read_json(_probe_path(repo)), "schema": "bad"}),
        ),
        (
            "status",
            lambda repo: _write_json(_probe_path(repo), {**_read_json(_probe_path(repo)), "status": "fail"}),
        ),
        (
            "contract-hash",
            lambda repo: _write_json(_probe_path(repo), {**_read_json(_probe_path(repo)), "contract_sha256": "sha256:deadbeef"}),
        ),
        (
            "adapter-hash",
            lambda repo: _write_json(_probe_path(repo), {**_read_json(_probe_path(repo)), "adapter_manifest_sha256": "sha256:deadbeef"}),
        ),
        (
            "vector",
            lambda repo: _write_json(_probe_path(repo), {**_read_json(_probe_path(repo)), "vectors": _read_json(_probe_path(repo))["vectors"][:2]}),
        ),
        (
            "audit",
            lambda repo: _write_json(_probe_path(repo), {**_read_json(_probe_path(repo)), "audit_field_results": [{"vector_id": "allow-canary", "missing": ["actor_id"], "status": "fail"}]}),
        ),
        (
            "correlation",
            lambda repo: _mutate_vector(repo, "allow-canary", lambda vector: vector.update({"correlation_id": ""})),
        ),
        (
            "naive-time",
            lambda repo: _write_json(
                _probe_path(repo),
                {
                    **_read_json(_probe_path(repo)),
                    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                },
            ),
        ),
        (
            "outcome",
            lambda repo: _mutate_vector(repo, "allow-canary", lambda vector: vector.update({"outcome_events": []})),
        ),
        (
            "conditional",
            lambda repo: _mutate_vector(repo, "conditional-canary", lambda vector: vector.pop("approval_id", None)),
        ),
    ],
)
def test_agt103_requires_current_correlatable_probe_evidence(name: str, mutator) -> None:
    with _repo_copy(f"agt103-{name}") as repo:
        _prepare_repo(repo)
        mutator(repo)
        findings = _by_id(pr._check_tool_governance_static(_ctx(repo)))
        assert findings["AGT-103"].status == "should-fix", findings["AGT-103"].detail


def _mutate_vector(repo: Path, vector_id: str, mutator) -> None:
    payload = _read_json(_probe_path(repo))
    vectors = payload["vectors"]
    for vector in vectors:
        if vector["id"] == vector_id:
            mutator(vector)
            break
    _write_json(_probe_path(repo), payload)


def test_agt103_escalates_for_irreversible_write_contracts() -> None:
    with _repo_copy("agt103-irreversible") as repo:
        manifest = _manifest(repo)
        manifest["tool_governance"]["tools"][2]["action_class"] = "irreversible-write"
        _write_json(repo / "specs" / "manifest.json", manifest)
        _prepare_repo(repo)
        _probe_path(repo).unlink()
        findings = _by_id(pr._check_tool_governance_static(_ctx(repo)))
        assert findings["AGT-103"].status == "must-fix", findings["AGT-103"].detail


def test_agt_static_emits_new_ids_once_on_legacy_and_govern_paths() -> None:
    with _repo_copy("agt-static-legacy") as repo:
        _prepare_repo(repo)
        legacy = pr._check_agt_static(_ctx(repo), "auto")
        for fid in ("AGT-007", "AGT-008", "AGT-103"):
            assert sum(1 for finding in legacy if finding.id == fid) == 1

    with _repo_copy("agt-static-govern") as repo:
        _prepare_repo(repo)
        _write_json(repo / "specs" / "govern-manifest.json", _fresh_govern_manifest())
        governed = pr._check_agt_static(_ctx(repo), "auto")
        for fid in ("AGT-007", "AGT-008", "AGT-103"):
            assert sum(1 for finding in governed if finding.id == fid) == 1
