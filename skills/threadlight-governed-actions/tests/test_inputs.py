"""Tests for threadlight-governed-actions' input resolver.

Exercises ``inputs.resolve_inputs`` (required ``specs/SPEC.md`` for design and
pre-deploy, a nonempty runtime/tool declaration set for pre-deploy,
deterministic discovery of every input category, and explicit recording of
unavailable optional capabilities) and ``inputs.allowlisted_evidence_path``
(rejecting absolute paths, ``..`` traversal, and symlink escapes).

``allowlisted_evidence_path`` is a *path* safety gate only: it never parses
or inspects file content, so legitimate eval/policy JSON containing
``prompt``/``input``/``output`` keys is discovered like any other file
rather than rejected as "payload-bearing". Content-level payload validation
belongs only to code that actually embeds a file's parsed content into
assessor-produced evidence (see ``maf_adapter.MAFAdapter.resolved_tuple``),
never to generic path discovery.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_inputs.py \
        skills/threadlight-governed-actions/tests/test_maf_adapter.py -q
"""
from __future__ import annotations

import json
import textwrap
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import inputs
from inputs import InputResolutionError, ResolvedInputs, allowlisted_evidence_path, resolve_inputs


def write_spec(root: Path, body: str = "approval required for `payments.refund`") -> Path:
    specs_dir = root / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    path = specs_dir / "SPEC.md"
    path.write_text(f"# SPEC\n\n## 8. Action Governance and Approval\n\n{body}\n", encoding="utf-8")
    return path


def write_python_file(root: Path, relative: str, body: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Required prerequisites
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", ["design", "pre-deploy"])
def test_missing_spec_raises_for_design_and_pre_deploy(tmp_path: Path, phase: str):
    with pytest.raises(InputResolutionError, match="specs/SPEC.md"):
        resolve_inputs(tmp_path, phase)


def test_missing_spec_is_allowed_for_post_deploy(tmp_path: Path):
    resolved = resolve_inputs(tmp_path, "post-deploy")
    assert resolved.spec is None


def test_present_spec_is_recorded_as_repository_relative_path(tmp_path: Path):
    write_spec(tmp_path)
    write_python_file(
        tmp_path, "app/agent.py", "def tool():\n    return None\n"
    )
    resolved = resolve_inputs(tmp_path, "design")
    assert resolved.spec == Path("specs/SPEC.md")


def test_pre_deploy_requires_a_nonempty_runtime_or_tool_declaration_set(
    tmp_path: Path,
):
    write_spec(tmp_path)
    with pytest.raises(InputResolutionError, match="runtime/tool declaration"):
        resolve_inputs(tmp_path, "pre-deploy")


def test_pre_deploy_accepts_a_registry_only_declaration_set(tmp_path: Path):
    write_spec(tmp_path)
    (tmp_path / "agent.yaml").write_text("tools: []\n", encoding="utf-8")
    resolved = resolve_inputs(tmp_path, "pre-deploy")
    assert resolved.registries == (Path("agent.yaml"),)


def test_design_allows_an_empty_runtime_declaration_set(tmp_path: Path):
    write_spec(tmp_path)
    resolved = resolve_inputs(tmp_path, "design")
    assert resolved.runtime_files == ()
    assert resolved.registries == ()


def test_unsupported_phase_is_rejected(tmp_path: Path):
    write_spec(tmp_path)
    with pytest.raises(InputResolutionError):
        resolve_inputs(tmp_path, "not-a-real-phase")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Deterministic discovery of every input category
# ---------------------------------------------------------------------------


def test_resolve_inputs_discovers_task2_registry_names(tmp_path: Path):
    write_spec(tmp_path)
    (tmp_path / "agent.yaml").write_text("tools: []\n", encoding="utf-8")
    (tmp_path / "tool-registry.json").write_text("{}\n", encoding="utf-8")
    resolved = resolve_inputs(tmp_path, "design")
    assert resolved.registries == (Path("agent.yaml"), Path("tool-registry.json"))


def test_resolve_inputs_discovers_python_files_excluding_venv_and_vendor(
    tmp_path: Path,
):
    write_spec(tmp_path)
    write_python_file(tmp_path, "app/agent.py", "def tool():\n    return None\n")
    write_python_file(
        tmp_path, ".venv/lib/pkg.py", "def phantom():\n    return None\n"
    )
    write_python_file(
        tmp_path, "node_modules/pkg/index.py", "def phantom():\n    return None\n"
    )
    resolved = resolve_inputs(tmp_path, "design")
    assert resolved.runtime_files == (Path("app/agent.py"),)


def test_resolve_inputs_discovers_task2_policy_globs(tmp_path: Path):
    write_spec(tmp_path)
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance" / "probe-contract.json").write_text("{}", encoding="utf-8")
    (tmp_path / "policies").mkdir()
    (tmp_path / "policies" / "rule.yaml").write_text("id: x\n", encoding="utf-8")
    (tmp_path / "unrelated.json").write_text("{}", encoding="utf-8")
    resolved = resolve_inputs(tmp_path, "design")
    assert set(resolved.policy_files) == {
        Path("governance/probe-contract.json"),
        Path("policies/rule.yaml"),
    }


def test_resolve_inputs_discovers_approval_files_by_ast_symbol(tmp_path: Path):
    write_spec(tmp_path)
    write_python_file(
        tmp_path,
        "src/governance/approvals.py",
        """\
        def issue_approval(request):
            return None
        """,
    )
    write_python_file(
        tmp_path,
        "src/governance/handler.py",
        """\
        class ApprovalHandler:
            pass
        """,
    )
    write_python_file(
        tmp_path, "app/agent.py", "def unrelated():\n    return None\n"
    )
    resolved = resolve_inputs(tmp_path, "design")
    assert set(resolved.approval_files) == {
        Path("src/governance/approvals.py"),
        Path("src/governance/handler.py"),
    }


@pytest.mark.parametrize("symbol", ["consume_approval", "redeem_approval"])
def test_resolve_inputs_recognizes_every_approval_symbol(tmp_path: Path, symbol: str):
    write_spec(tmp_path)
    write_python_file(
        tmp_path,
        "src/governance/approvals.py",
        f"""\
        def {symbol}(token):
            return None
        """,
    )
    resolved = resolve_inputs(tmp_path, "design")
    assert resolved.approval_files == (Path("src/governance/approvals.py"),)


def test_resolve_inputs_discovers_tests_conformance_and_eval_reports(tmp_path: Path):
    write_spec(tmp_path)
    write_python_file(tmp_path, "tests/test_thing.py", "def test_x():\n    pass\n")
    (tmp_path / "conformance" / "claims" / "maf").mkdir(parents=True)
    (tmp_path / "conformance" / "claims" / "maf" / "REPORT.md").write_text(
        "# report\n", encoding="utf-8"
    )
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "results.json").write_text("{}", encoding="utf-8")
    resolved = resolve_inputs(tmp_path, "design")
    assert Path("tests/test_thing.py") in resolved.test_and_report_files
    assert (
        Path("conformance/claims/maf/REPORT.md") in resolved.test_and_report_files
    )
    assert Path("evals/results.json") in resolved.test_and_report_files


def test_resolve_inputs_discovers_workflow_files(tmp_path: Path):
    write_spec(tmp_path)
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    (workflows / "deploy.yaml").write_text("name: deploy\n", encoding="utf-8")
    (workflows / "README.md").write_text("not a workflow\n", encoding="utf-8")
    resolved = resolve_inputs(tmp_path, "design")
    assert set(resolved.workflow_files) == {
        Path(".github/workflows/ci.yml"),
        Path(".github/workflows/deploy.yaml"),
    }


def test_resolve_inputs_discovers_both_codeowners_locations(tmp_path: Path):
    write_spec(tmp_path)
    (tmp_path / "CODEOWNERS").write_text("* @owner\n", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("* @owner\n", encoding="utf-8")
    resolved = resolve_inputs(tmp_path, "design")
    assert set(resolved.ownership_files) == {
        Path("CODEOWNERS"),
        Path(".github/CODEOWNERS"),
    }


def test_resolve_inputs_discovers_dependency_and_lock_files(tmp_path: Path):
    write_spec(tmp_path)
    for name in (
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "uv.lock",
        "poetry.lock",
        "pdm.lock",
    ):
        (tmp_path / name).write_text("", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    resolved = resolve_inputs(tmp_path, "design")
    assert set(resolved.dependency_files) == {
        Path("pyproject.toml"),
        Path("requirements.txt"),
        Path("requirements-dev.txt"),
        Path("uv.lock"),
        Path("poetry.lock"),
        Path("pdm.lock"),
        Path("package.json"),
    }


# ---------------------------------------------------------------------------
# Optional GitHub/Azure capability is explicit, never omitted
# ---------------------------------------------------------------------------


def test_missing_github_and_azure_capabilities_are_explicit_not_omitted(
    tmp_path: Path,
):
    write_spec(tmp_path)
    resolved = resolve_inputs(tmp_path, "design")
    assert "live_github" in resolved.missing_capabilities
    assert "live_azure" in resolved.missing_capabilities
    assert resolved.missing_capabilities["live_github"]
    assert resolved.missing_capabilities["live_azure"]


# ---------------------------------------------------------------------------
# Parse failures identify path + affected finding IDs; never empty success
# ---------------------------------------------------------------------------


def test_unreadable_evidence_file_raises_with_path_and_finding_ids(tmp_path: Path):
    write_spec(tmp_path)
    (tmp_path / "governance").mkdir()
    blocked = tmp_path / "governance" / "probe-contract.json"
    blocked.write_text("{}", encoding="utf-8")
    blocked.chmod(0o000)
    try:
        with pytest.raises(InputResolutionError) as excinfo:
            resolve_inputs(tmp_path, "design")
        message = str(excinfo.value)
        assert "governance/probe-contract.json" in message
        assert "MED-001" in message
    finally:
        blocked.chmod(0o644)


def test_resolve_inputs_accepts_realistic_eval_and_policy_json_with_prompt_and_schema_keys(
    tmp_path: Path,
):
    """A path-safe discovery pass must never abort on legitimate content.

    Real eval/policy evidence routinely carries ``prompt``/``input``/
    ``output``/``input_schema`` keys (that is what an eval report or a
    probe contract *is*). ``resolve_inputs`` only ever records the paths of
    these files — it never embeds their content into evidence — so their
    content must never be inspected or rejected during discovery.
    """
    write_spec(tmp_path)
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance" / "probe-contract.json").write_text(
        json.dumps(
            {
                "id": "probe-1",
                "prompt": "issue a refund for order 42",
                "input_schema": {"type": "object", "properties": {"order_id": {}}},
                "output": {"status": "denied"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "eval-report.json").write_text(
        json.dumps(
            {
                "id": "eval-1",
                "prompt": "attempt an unapproved payment",
                "input": {"amount": 500},
                "output": {"result": "blocked"},
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_inputs(tmp_path, "design")

    assert Path("governance/probe-contract.json") in resolved.policy_files
    assert Path("evals/eval-report.json") in resolved.test_and_report_files


# ---------------------------------------------------------------------------
# allowlisted_evidence_path: reject absolute/../symlink-escape; path-only,
# content is never parsed or inspected
# ---------------------------------------------------------------------------


def test_allowlisted_evidence_path_rejects_absolute_candidate(tmp_path: Path):
    with pytest.raises(InputResolutionError, match="absolute"):
        allowlisted_evidence_path(tmp_path, Path("/etc/passwd"))


def test_allowlisted_evidence_path_rejects_dotdot_traversal(tmp_path: Path):
    with pytest.raises(InputResolutionError, match=r"\.\."):
        allowlisted_evidence_path(tmp_path, Path("../outside.json"))


def test_allowlisted_evidence_path_rejects_symlink_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside-evidence"
    outside.mkdir(exist_ok=True)
    secret = outside / "secret.json"
    secret.write_text("{}", encoding="utf-8")
    try:
        link = tmp_path / "escape.json"
        link.symlink_to(secret)
        with pytest.raises(InputResolutionError, match="escapes"):
            allowlisted_evidence_path(tmp_path, Path("escape.json"))
    finally:
        import shutil

        shutil.rmtree(outside, ignore_errors=True)


def test_allowlisted_evidence_path_accepts_a_clean_relative_file(tmp_path: Path):
    candidate = tmp_path / "evidence.json"
    candidate.write_text(json.dumps({"id": "e-1", "sha256": "sha256:abc"}), encoding="utf-8")
    resolved = allowlisted_evidence_path(tmp_path, Path("evidence.json"))
    assert resolved == candidate.resolve()


def test_allowlisted_evidence_path_accepts_json_with_prompt_and_schema_keys(
    tmp_path: Path,
):
    """Regression: realistic eval/policy JSON must never be rejected.

    ``allowlisted_evidence_path`` only validates the *path* (absolute/../
    symlink-escape/existence/readability); it must never parse the file to
    reject it for carrying ``prompt``/``input``/``output`` keys, which is
    exactly what real eval and policy evidence looks like.
    """
    candidate = tmp_path / "evidence.json"
    candidate.write_text(
        json.dumps(
            {
                "prompt": "do the thing",
                "input_schema": {"type": "object"},
                "output": {"result": "denied"},
            }
        ),
        encoding="utf-8",
    )
    resolved = allowlisted_evidence_path(tmp_path, Path("evidence.json"))
    assert resolved == candidate.resolve()


def test_allowlisted_evidence_path_does_not_validate_json_syntax(tmp_path: Path):
    """Path-only allowlisting never parses content, so invalid JSON text is
    accepted like any other readable file — a downstream consumer that
    actually needs the parsed content is responsible for its own parse
    error handling.
    """
    candidate = tmp_path / "not-actually-json.json"
    candidate.write_text("{not valid json", encoding="utf-8")
    resolved = allowlisted_evidence_path(tmp_path, Path("not-actually-json.json"))
    assert resolved == candidate.resolve()


def test_allowlisted_evidence_path_rejects_missing_file(tmp_path: Path):
    with pytest.raises(InputResolutionError, match="does not exist"):
        allowlisted_evidence_path(tmp_path, Path("missing.json"))


# ---------------------------------------------------------------------------
# ResolvedInputs is a frozen, explicit contract
# ---------------------------------------------------------------------------


def test_resolved_inputs_is_frozen(tmp_path: Path):
    write_spec(tmp_path)
    resolved = resolve_inputs(tmp_path, "design")
    assert isinstance(resolved, ResolvedInputs)
    with pytest.raises(FrozenInstanceError):
        resolved.spec = Path("elsewhere.md")  # type: ignore[misc]


def test_input_resolution_error_is_a_value_error():
    assert issubclass(InputResolutionError, ValueError)
