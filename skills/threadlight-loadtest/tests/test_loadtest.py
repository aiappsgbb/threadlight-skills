"""Tests for the threadlight-loadtest skill: guarded, budget-capped load
evidence (`scripts/loadtest.py` + `scripts/adapters.py`).

Covers: FakeAdapter call accounting, budget-ceiling abort, production
confirmation abort, deterministic sample percentiles, partial-run
diagnostics without a spec plan, no-adapter partial, invalid
profile/budget/sample rejection, command-adapter safety (no shell=True,
list-form argv, timeout), determinism, atomic writes preserving a prior
valid manifest, schema/jsonschema parity, privacy (forbidden-key +
secret-value scrubbing), and a successful complete run's advisory spec plan.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import adapters as ad  # noqa: E402
import loadtest as lt  # noqa: E402

REPO_ROOT = SKILL_ROOT.parent.parent
sys.path.insert(0, str(REPO_ROOT))
from skills._shared.manifest import ManifestValidationError  # noqa: E402

GENERATED_AT = "2026-08-17T10:00:00Z"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def base_profile(**overrides) -> dict:
    profile = {
        "name": "checkout-agent-smoke",
        "endpoint": {"url": None, "credential_ref": None},
        "duration_s": 30,
        "virtual_users": 10,
        "tokens_per_request_estimate": 500,
        "price_per_1k_tokens_usd": 0.002,
        # The approved profile shape carries a DIRECT declared projection; the
        # helpers supply a small in-budget default so rich-shape tests exercise
        # the adapter/SLO/spec-plan paths. Derivation-specific tests build bare
        # dicts instead (see the budget-estimate tests).
        "projected_token_cost_usd": 0.30,
    }
    profile.update(overrides)
    return profile


def configured_profile(**overrides) -> dict:
    return base_profile(
        endpoint={"url": "https://staging.example.test/api", "credential_ref": "kv:load-test-key"},
        **overrides,
    )


def make_samples(latencies, *, success=True, tokens=50):
    return [
        {"latency_ms": v, "success": success, "tokens": tokens}
        for v in latencies
    ]


class FakeAdapter:
    """Test-only LoadAdapter satisfying the Protocol shape without any real
    subprocess. Tracks call count so tests can assert guarded runs never
    invoke it."""

    def __init__(self, name="fake-engine", result=None):
        self.name = name
        self.calls = 0
        self._result = result if result is not None else {
            "status": "complete", "samples": make_samples([100, 200, 300, 400, 500]),
        }

    def run(self, profile):
        self.calls += 1
        return self._result


class PoisonAdapter:
    """Raises if ever called — used to prove a gate aborted BEFORE the
    adapter would have been invoked."""

    name = "poison"

    def run(self, profile):  # pragma: no cover - should never run
        raise AssertionError("adapter.run() must not be called when a gate blocks the run")


def finding_map(manifest):
    return {f["id"]: f["status"] for f in manifest["findings"]}


# ---------------------------------------------------------------------------
# 1. FakeAdapter calls / successful complete run + advisory spec plan
# ---------------------------------------------------------------------------
def test_complete_run_calls_adapter_exactly_once_and_builds_spec_plan():
    adapter = FakeAdapter(result={
        "status": "complete",
        "samples": make_samples([100, 200, 300, 400, 500], tokens=50),
    })
    profile = configured_profile(slo={"max_p95_latency_ms": 900, "max_error_rate": 0.5})

    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=adapter, generated_at=GENERATED_AT,
    )

    assert adapter.calls == 1
    assert manifest["status"] == "complete"
    assert manifest["adapter_name"] == "fake-engine"
    assert finding_map(manifest) == {"LOAD-001": "pass", "LOAD-002": "pass", "LOAD-003": "pass"}

    diag = manifest["diagnostics"]
    assert diag["p50_latency_ms"] == 300
    assert diag["p95_latency_ms"] == 500
    assert diag["error_rate"] == 0.0
    assert diag["tokens_per_request"] == 50.0

    plan = manifest["spec_update_plan"]
    assert plan is not None
    assert plan["action"] == "advisory"
    assert plan["target"] == "SPEC.md"
    snippet = plan["snippet"]
    assert GENERATED_AT in snippet
    assert "p50_latency_ms: 300" in snippet
    assert "p95_latency_ms: 500" in snippet
    assert "throughput_rps" in snippet
    assert "tokens_per_request" in snippet
    assert "error_rate" in snippet


def test_complete_run_never_writes_spec_md_itself(tmp_path, monkeypatch):
    """The spec_update_plan is plan-only — run_loadtest touches no files."""
    monkeypatch.chdir(tmp_path)
    adapter = FakeAdapter()
    profile = configured_profile()
    lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=adapter, generated_at=GENERATED_AT,
    )
    assert not (tmp_path / "SPEC.md").exists()


# ---------------------------------------------------------------------------
# 2. Budget abort
# ---------------------------------------------------------------------------
def test_budget_ceiling_exceeded_aborts_before_any_adapter_call():
    profile = configured_profile(projected_token_cost_usd=25.0)
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=0.01, endpoint_class="non-production",
        adapter=PoisonAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "aborted"
    assert finding_map(manifest)["LOAD-002"] == "must-fix"
    assert manifest["adapter_name"] is None
    assert manifest["diagnostics"]["sample_count"] == 0
    assert "spec_update_plan" not in manifest
    assert manifest["budget"]["within_ceiling"] is False
    assert manifest["budget"]["projection_source"] == "declared"


def test_budget_exactly_at_ceiling_is_not_aborted():
    profile = configured_profile(projected_token_cost_usd=1.0)
    ceiling = lt.estimate_projected_token_cost_usd(profile)
    adapter = FakeAdapter()
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=ceiling, endpoint_class="non-production",
        adapter=adapter, generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "complete"
    assert adapter.calls == 1
    assert manifest["budget"]["within_ceiling"] is True


# ---------------------------------------------------------------------------
# 3. Production confirmation abort
# ---------------------------------------------------------------------------
def test_production_endpoint_without_confirmation_aborts_before_adapter():
    profile = configured_profile()
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=1000.0, endpoint_class="production",
        adapter=PoisonAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "aborted"
    assert finding_map(manifest)["LOAD-001"] == "must-fix"
    assert manifest["adapter_name"] is None


def test_production_endpoint_with_explicit_confirmation_proceeds():
    profile = configured_profile()
    adapter = FakeAdapter()
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=1000.0, endpoint_class="production",
        adapter=adapter, allow_production=True, generated_at=GENERATED_AT,
    )
    assert adapter.calls == 1
    assert manifest["status"] == "complete"
    assert finding_map(manifest)["LOAD-001"] == "pass"
    assert manifest["allow_production"] is True


def test_budget_gate_checked_even_when_production_also_blocked():
    """Both gates would fire; budget is checked (and reported) first per the
    documented gate order, and the adapter is still never called."""
    profile = configured_profile(tokens_per_request_estimate=5000, price_per_1k_tokens_usd=10.0)
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=0.01, endpoint_class="production",
        adapter=PoisonAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "aborted"
    assert finding_map(manifest)["LOAD-002"] == "must-fix"
    # LOAD-001 is still reported accurately even though budget is what aborted.
    assert finding_map(manifest)["LOAD-001"] == "must-fix"


# ---------------------------------------------------------------------------
# 4. Sample percentiles / summarize_samples determinism
# ---------------------------------------------------------------------------
def test_summarize_samples_percentiles_match_documented_example():
    samples = make_samples([100, 200, 300, 400, 500])
    summary = lt.summarize_samples(samples)
    assert summary["p50_latency_ms"] == 300
    assert summary["p95_latency_ms"] == 500
    assert summary["sample_count"] == 5


def test_summarize_samples_error_rate_tokens_and_throughput():
    samples = [
        {"latency_ms": 100, "success": True, "tokens": 40},
        {"latency_ms": 200, "success": False, "tokens": 60},
        {"latency_ms": 300, "success": True, "tokens": 50},
        {"latency_ms": 400, "success": True, "tokens": 50},
    ]
    summary = lt.summarize_samples(samples, duration_s=4.0)
    assert summary["error_rate"] == pytest.approx(0.25)
    assert summary["tokens_per_request"] == pytest.approx(50.0)
    assert summary["throughput_rps"] == pytest.approx(1.0)


def test_summarize_samples_empty_list_is_valid_not_an_error():
    summary = lt.summarize_samples([])
    assert summary["sample_count"] == 0
    assert summary["p50_latency_ms"] is None
    assert summary["throughput_rps"] is None


def test_summarize_samples_without_duration_leaves_throughput_none():
    summary = lt.summarize_samples(make_samples([100, 200]))
    assert summary["throughput_rps"] is None


def test_summarize_samples_percentiles_are_order_independent():
    ordered = lt.summarize_samples(make_samples([100, 200, 300, 400, 500]))
    shuffled = lt.summarize_samples(make_samples([500, 100, 400, 200, 300]))
    assert ordered == shuffled


# ---------------------------------------------------------------------------
# 5. Partial adapter run: diagnostics retained, no spec plan, LOAD-002 not-verified
# ---------------------------------------------------------------------------
def test_partial_adapter_run_keeps_diagnostics_but_no_spec_plan():
    partial_samples = make_samples([150, 250], success=True)
    adapter = FakeAdapter(result={
        "status": "partial", "samples": partial_samples, "error": "k6 timed out after 30.0s",
    })
    profile = configured_profile()
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=adapter, generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "partial"
    assert finding_map(manifest)["LOAD-002"] == "not-verified"
    assert "spec_update_plan" not in manifest
    assert manifest["diagnostics"]["sample_count"] == 2
    assert manifest["diagnostics"]["adapter_error"] == "k6 timed out after 30.0s"


def test_complete_claim_with_zero_samples_is_treated_as_partial():
    """A 'complete' status with an empty sample list is not trustworthy
    evidence and must be downgraded, never treated as a successful run."""
    adapter = FakeAdapter(result={"status": "complete", "samples": []})
    profile = configured_profile()
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=adapter, generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "partial"
    assert "spec_update_plan" not in manifest
    assert finding_map(manifest)["LOAD-002"] == "not-verified"


def test_partial_run_with_slo_declared_still_scores_load_003():
    adapter = FakeAdapter(result={
        "status": "partial",
        "samples": make_samples([100, 200, 300, 400, 1000]),
        "error": "connection reset midway",
    })
    profile = configured_profile(slo={"max_p95_latency_ms": 500})
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=adapter, generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "partial"
    assert finding_map(manifest)["LOAD-003"] == "must-fix"


# ---------------------------------------------------------------------------
# 6. No adapter selected -> partial, LOAD-002 not-verified, no install
# ---------------------------------------------------------------------------
def test_no_adapter_selected_yields_partial_manifest_with_no_calls():
    profile = configured_profile()
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=None, generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "partial"
    assert manifest["adapter_name"] is None
    assert finding_map(manifest)["LOAD-002"] == "not-verified"
    assert manifest["diagnostics"]["sample_count"] == 0


def test_select_adapter_prefers_k6_then_locust_then_none():
    assert ad.select_adapter({"k6", "locust"}) == "k6"
    assert ad.select_adapter({"locust"}) == "locust"
    assert ad.select_adapter(set()) is None
    assert ad.select_adapter(["some-other-tool"]) is None


def test_detect_available_commands_never_installs_only_probes(monkeypatch):
    probed = []

    def fake_which(name):
        probed.append(name)
        return "/usr/local/bin/" + name if name == "k6" else None

    available = ad.detect_available_commands(which=fake_which)
    assert available == {"k6"}
    assert set(probed) == {"k6", "locust"}


def test_missing_endpoint_or_credential_yields_partial_not_verified():
    profile = base_profile()  # endpoint.url / credential_ref both None
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "partial"
    assert manifest["status"] != "complete"
    assert finding_map(manifest)["LOAD-002"] == "not-verified"
    assert manifest["endpoint_configured"] is False


def test_missing_only_credential_ref_still_blocks_the_run():
    profile = base_profile(endpoint={"url": "https://x.test", "credential_ref": None})
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=PoisonAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "partial"
    assert manifest["endpoint_configured"] is False


# ---------------------------------------------------------------------------
# 7. Invalid profile / budget / sample rejection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_budget", [0, -5, True, False, float("nan"), float("inf"), "10"])
def test_invalid_budget_ceiling_is_rejected(bad_budget):
    profile = configured_profile()
    with pytest.raises(lt.LoadTestValidationError):
        lt.run_loadtest(
            profile=profile, budget_ceiling_usd=bad_budget, endpoint_class="non-production",
            adapter=FakeAdapter(), generated_at=GENERATED_AT,
        )


def test_absent_endpoint_is_not_a_validation_error():
    """Endpoint is optional: a profile with no endpoint at all validates
    cleanly (it becomes a partial/not-verified run at execution time, not an
    input-validation error)."""
    profile = base_profile()
    del profile["endpoint"]
    lt.validate_profile(profile)  # must not raise
    # And a minimal approved-shape profile (no endpoint, no duration) is valid.
    lt.validate_profile({
        "peak_requests_per_second": 2, "hold_seconds": 10, "projected_token_cost_usd": 5.0,
    })


def test_malformed_endpoint_type_is_still_a_controlled_error():
    """A present-but-malformed endpoint (wrong type) is a controlled
    LoadTestValidationError, never a raw crash."""
    with pytest.raises(lt.LoadTestValidationError):
        lt.validate_profile(base_profile(endpoint=["not", "an", "object"]))


def test_unknown_profile_key_is_rejected():
    profile = configured_profile(unexpected_field="nope")
    with pytest.raises(lt.LoadTestValidationError):
        lt.validate_profile(profile)


@pytest.mark.parametrize("bad_users", [0, -1, 1.5, True, "10"])
def test_invalid_virtual_users_is_rejected(bad_users):
    profile = configured_profile(virtual_users=bad_users)
    with pytest.raises(lt.LoadTestValidationError):
        lt.validate_profile(profile)


def test_invalid_endpoint_class_is_rejected():
    profile = configured_profile()
    with pytest.raises(lt.LoadTestValidationError):
        lt.run_loadtest(
            profile=profile, budget_ceiling_usd=10.0, endpoint_class="staging",
            adapter=None, generated_at=GENERATED_AT,
        )


@pytest.mark.parametrize("bad_sample", [
    {"latency_ms": -1, "success": True, "tokens": 5},
    {"latency_ms": float("nan"), "success": True, "tokens": 5},
    {"latency_ms": 5, "success": "yes", "tokens": 5},
    {"latency_ms": 5, "success": True, "tokens": -1},
    {"latency_ms": 5, "success": True},
    {"latency_ms": 5, "tokens": 5},
])
def test_invalid_sample_is_rejected(bad_sample):
    with pytest.raises(lt.LoadTestValidationError):
        lt.summarize_samples([bad_sample])


def test_adapter_returning_unknown_status_is_rejected():
    adapter = FakeAdapter(result={"status": "bogus", "samples": []})
    profile = configured_profile()
    with pytest.raises(lt.LoadTestValidationError):
        lt.run_loadtest(
            profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
            adapter=adapter, generated_at=GENERATED_AT,
        )


# ---------------------------------------------------------------------------
# 8. Command adapter safety: no shell=True, list argv, timeout, single command
# ---------------------------------------------------------------------------
class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_command_adapter_invokes_list_argv_with_shell_false_and_timeout():
    captured = {}

    def fake_runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeCompleted(0, stdout=json.dumps({"latency_ms": 120, "success": True, "tokens": 30}))

    adapter = ad.CommandLoadAdapter(
        name="k6", command_path="/usr/local/bin/k6", timeout_s=42.0, runner=fake_runner,
    )
    profile = configured_profile(script_path="scripts/harness.js")
    result = adapter.run(profile)

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert all(isinstance(part, str) for part in argv)
    assert argv[0] == "/usr/local/bin/k6"
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 42.0
    assert result["status"] == "complete"
    assert result["samples"] == [{"latency_ms": 120, "success": True, "tokens": 30}]


def test_command_adapter_never_returns_raw_stdout_stderr():
    def fake_runner(argv, **kwargs):
        return _FakeCompleted(
            1, stdout="line one\nline two\nsecret internal debug info",
            stderr="password=hunter2 leaked in logs",
        )

    adapter = ad.CommandLoadAdapter(name="k6", command_path="/bin/k6", runner=fake_runner)
    result = adapter.run(configured_profile(script_path="s.js"))
    assert result["status"] == "partial"
    assert "hunter2" not in result["error"]
    assert "[REDACTED]" in result["error"]
    assert "raw" not in result  # sanity: no such key at all
    assert "stdout" not in result
    assert "stderr" not in result


def test_command_adapter_scrubs_bearer_token_from_error():
    def fake_runner(argv, **kwargs):
        return _FakeCompleted(1, stderr="Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456")

    adapter = ad.CommandLoadAdapter(name="k6", command_path="/bin/k6", runner=fake_runner)
    result = adapter.run(configured_profile(script_path="s.js"))
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result["error"]
    assert "[REDACTED]" in result["error"]


def test_command_adapter_handles_timeout_as_partial_not_a_crash():
    def fake_runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 1))

    adapter = ad.CommandLoadAdapter(name="locust", command_path="/bin/locust", timeout_s=1, runner=fake_runner)
    result = adapter.run(configured_profile(script_path="s.py"))
    assert result["status"] == "partial"
    assert result["samples"] == []
    assert "timed out" in result["error"]


def test_command_adapter_only_invokes_the_selected_command_name():
    """A locust-selected adapter never shells out to k6 (or anything else) —
    argv[0] is always exactly the resolved command_path for `name`."""
    seen = []

    def fake_runner(argv, **kwargs):
        seen.append(argv[0])
        return _FakeCompleted(0, stdout=json.dumps({"latency_ms": 1, "success": True, "tokens": 1}))

    adapter = ad.CommandLoadAdapter(name="locust", command_path="/opt/bin/locust", runner=fake_runner)
    adapter.run(configured_profile(script_path="s.py"))
    assert seen == ["/opt/bin/locust"]


# ---------------------------------------------------------------------------
# 9a. Determinism
# ---------------------------------------------------------------------------
def test_run_loadtest_is_deterministic_for_identical_inputs():
    profile = configured_profile(slo={"max_p95_latency_ms": 900})
    samples = make_samples([120, 130, 140, 900, 200])

    manifest_a = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(result={"status": "complete", "samples": list(samples)}),
        generated_at=GENERATED_AT,
    )
    manifest_b = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(result={"status": "complete", "samples": list(samples)}),
        generated_at=GENERATED_AT,
    )
    assert manifest_a == manifest_b


def test_run_loadtest_makes_no_network_calls(monkeypatch):
    import socket

    def _forbidden(*args, **kwargs):  # pragma: no cover - only hit on regression
        raise AssertionError("run_loadtest must never open a network socket")

    monkeypatch.setattr(socket, "socket", _forbidden)
    profile = configured_profile()
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "complete"


# ---------------------------------------------------------------------------
# 9b. Atomic write + previous valid manifest preserved
# ---------------------------------------------------------------------------
def test_write_load_manifest_is_atomic_and_schema_validated(tmp_path):
    profile = configured_profile()
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    destination = tmp_path / "specs" / "load-manifest.json"
    lt.write_load_manifest(destination, manifest)

    assert destination.exists()
    on_disk = json.loads(destination.read_text())
    assert on_disk == manifest
    # No leftover temp file.
    leftovers = list(destination.parent.glob(".*.tmp"))
    assert leftovers == []


def test_invalid_manifest_write_preserves_prior_valid_manifest(tmp_path):
    profile = configured_profile()
    good_manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    destination = tmp_path / "load-manifest.json"
    lt.write_load_manifest(destination, good_manifest)
    original_bytes = destination.read_bytes()

    broken_manifest = dict(good_manifest)
    del broken_manifest["budget"]  # violates the schema's required-key contract

    with pytest.raises(ManifestValidationError):
        lt.write_load_manifest(destination, broken_manifest)

    assert destination.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# 9c. Schema (stdlib validator + jsonschema parity)
# ---------------------------------------------------------------------------
def test_stdlib_validator_matches_schema_file_for_complete_manifest():
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = SKILL_ROOT / "references" / "load-manifest.schema.json"
    schema = json.loads(schema_path.read_text())

    profile = configured_profile(slo={"max_error_rate": 0.5})
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    jsonschema.Draft7Validator.check_schema(schema)
    jsonschema.validate(manifest, schema)
    lt.validate_load_manifest(manifest)  # must not raise


def test_stdlib_validator_matches_schema_file_for_aborted_and_partial():
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = SKILL_ROOT / "references" / "load-manifest.schema.json"
    schema = json.loads(schema_path.read_text())

    aborted = lt.run_loadtest(
        profile=configured_profile(tokens_per_request_estimate=5000, price_per_1k_tokens_usd=10.0),
        budget_ceiling_usd=0.01, endpoint_class="non-production",
        adapter=PoisonAdapter(), generated_at=GENERATED_AT,
    )
    partial = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=None, generated_at=GENERATED_AT,
    )
    for manifest in (aborted, partial):
        jsonschema.validate(manifest, schema)
        lt.validate_load_manifest(manifest)


def test_schema_rejects_spec_plan_on_a_non_complete_manifest():
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = SKILL_ROOT / "references" / "load-manifest.schema.json"
    schema = json.loads(schema_path.read_text())

    manifest = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=None, generated_at=GENERATED_AT,
    )
    tampered = dict(manifest)
    tampered["spec_update_plan"] = {
        "action": "advisory", "target": "SPEC.md", "section": "Load Profile", "snippet": "x",
    }
    with pytest.raises(Exception):
        jsonschema.validate(tampered, schema)
    with pytest.raises(ManifestValidationError):
        lt.validate_load_manifest(tampered)


def test_schema_rejects_unknown_finding_id():
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = SKILL_ROOT / "references" / "load-manifest.schema.json"
    schema = json.loads(schema_path.read_text())

    manifest = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    tampered = json.loads(json.dumps(manifest))
    tampered["findings"][0]["id"] = "LOAD-999"
    with pytest.raises(Exception):
        jsonschema.validate(tampered, schema)
    with pytest.raises(ManifestValidationError):
        lt.validate_load_manifest(tampered)


# ---------------------------------------------------------------------------
# 9d. Privacy — forbidden keys / secret values never persisted
# ---------------------------------------------------------------------------
def test_manifest_never_contains_forbidden_shaped_keys():
    manifest = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    lt._assert_no_unsafe_content(manifest)  # must not raise
    dumped = json.dumps(manifest).lower()
    for forbidden in ("credential_ref", "\"url\"", "password", "api_key", "bearer "):
        assert forbidden not in dumped


def test_forbidden_key_shape_is_rejected_by_the_privacy_scan_directly():
    """`_assert_no_unsafe_content` is a defense-in-depth layer independent of
    the schema's additionalProperties allowlist — exercise it directly
    against an arbitrary forbidden-shaped key so the two layers are each
    proven to work, even though the schema already blocks unknown keys on
    real manifest shapes."""
    with pytest.raises(lt.LoadTestPrivacyError):
        lt._assert_no_unsafe_content({"nested": {"access_token": "sneaked-in"}})


def test_forbidden_key_in_a_hand_built_manifest_is_rejected_and_nothing_written(tmp_path):
    manifest = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    tampered = json.loads(json.dumps(manifest))
    tampered["diagnostics"]["access_token"] = "sneaked-in"
    # The strict schema allowlist rejects the unknown key before the privacy
    # scan even runs — either way, the manifest must never be written.
    with pytest.raises((lt.LoadTestPrivacyError, ManifestValidationError)):
        lt.write_load_manifest(tmp_path / "m.json", tampered)
    assert not (tmp_path / "m.json").exists()


def test_secret_shaped_value_in_a_hand_built_manifest_is_rejected(tmp_path):
    manifest = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    tampered = json.loads(json.dumps(manifest))
    tampered["profile_name"] = "leaked sk-abcdefghijklmnopqrstuvwxyz123456 in here"
    with pytest.raises(lt.LoadTestPrivacyError):
        lt.write_load_manifest(tmp_path / "m.json", tampered)


def test_endpoint_url_and_credential_ref_never_reach_the_manifest():
    profile = configured_profile()
    manifest = lt.run_loadtest(
        profile=profile, budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    dumped = json.dumps(manifest)
    assert profile["endpoint"]["url"] not in dumped
    assert profile["endpoint"]["credential_ref"] not in dumped
    assert manifest["endpoint_configured"] is True


def test_adapter_args_is_validated_but_never_leaked_unexpectedly():
    profile = configured_profile(adapter_args=["--flag", "value"])
    lt.validate_profile(profile)  # must not raise
    with pytest.raises(lt.LoadTestValidationError):
        lt.validate_profile(configured_profile(adapter_args=["ok", 5]))


# ---------------------------------------------------------------------------
# freshness / source_oldest_at from samples
# ---------------------------------------------------------------------------
def test_source_oldest_at_is_derived_from_earliest_sample_timestamp():
    samples = [
        {"latency_ms": 100, "success": True, "tokens": 10, "observed_at": "2026-08-17T10:05:00Z"},
        {"latency_ms": 200, "success": True, "tokens": 10, "observed_at": "2026-08-17T10:00:00Z"},
        {"latency_ms": 300, "success": True, "tokens": 10, "observed_at": "2026-08-17T10:02:00Z"},
    ]
    adapter = FakeAdapter(result={"status": "complete", "samples": samples})
    manifest = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=adapter, generated_at=GENERATED_AT,
    )
    assert manifest["freshness"]["source_oldest_at"] == "2026-08-17T10:00:00Z"


def test_source_oldest_at_is_null_when_samples_have_no_timestamps():
    manifest = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0, endpoint_class="non-production",
        adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["freshness"]["source_oldest_at"] is None


# ---------------------------------------------------------------------------
# Budget estimate specifics
# ---------------------------------------------------------------------------
def test_budget_estimate_declared_projection_wins_verbatim():
    profile = {"projected_token_cost_usd": 42.5, "request_count": 999,
               "tokens_per_request_estimate": 100, "price_per_1k_tokens_usd": 1.0}
    cost, source = lt.project_token_cost(profile)
    assert source == "declared"
    assert cost == pytest.approx(42.5)


def test_budget_estimate_uses_explicit_request_count_when_supplied():
    profile = {"request_count": 1000, "tokens_per_request_estimate": 100,
               "price_per_1k_tokens_usd": 1.0}
    cost, source = lt.project_token_cost(profile)
    assert source == "derived"
    assert cost == pytest.approx((100 * 1000 / 1000.0) * 1.0)


def test_budget_estimate_derives_request_count_from_rate_and_hold():
    profile = {"peak_requests_per_second": 3, "hold_seconds": 20,
               "tokens_per_request_estimate": 100, "price_per_1k_tokens_usd": 1.0}
    cost, source = lt.project_token_cost(profile)
    assert source == "derived"
    assert cost == pytest.approx((100 * (3 * 20) / 1000.0) * 1.0)


def test_budget_estimate_never_falls_back_to_virtual_users():
    """The old one-request-per-virtual-user fallback UNDERCOUNTED a sustained
    run and has been removed: without a declared projection or explicit
    request_count / rate+duration inputs, the projection is 'unavailable'
    (never silently derived from virtual_users)."""
    profile = {"virtual_users": 4, "tokens_per_request_estimate": 100,
               "price_per_1k_tokens_usd": 1.0}
    cost, source = lt.project_token_cost(profile)
    assert cost is None
    assert source == "unavailable"


def test_budget_estimate_zero_declared_projection_is_valid_only_because_explicit():
    """A zero projection is honoured ONLY when explicitly declared (never a
    silent default)."""
    cost, source = lt.project_token_cost({"projected_token_cost_usd": 0})
    assert cost == 0.0
    assert source == "declared"


# ---------------------------------------------------------------------------
# Task-5 contract: approved profile shape, projection provenance, p99/cold/
# scale metrics, omitted spec plan, masked secrets, findings uniqueness.
# ---------------------------------------------------------------------------
def approved_profile(**overrides) -> dict:
    """The minimal approved profile shape: peak RPS + hold + a DIRECT declared
    projection. No endpoint, no duration_s, no virtual_users."""
    profile = {
        "peak_requests_per_second": 2,
        "hold_seconds": 10,
        "projected_token_cost_usd": 5.0,
    }
    profile.update(overrides)
    return profile


def _valid_complete_manifest():
    return lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0,
        endpoint_class="non-production", adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )


def test_plan_pseudocode_declared_projection_over_ceiling_aborts():
    """Plan pseudocode: profile(projected_token_cost_usd=50), ceiling 1 -> the
    declared value is compared DIRECTLY to the ceiling and the run aborts with
    zero adapter calls."""
    manifest = lt.run_loadtest(
        profile=approved_profile(projected_token_cost_usd=50),
        budget_ceiling_usd=1, endpoint_class="non-production",
        adapter=PoisonAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "aborted"
    assert finding_map(manifest)["LOAD-002"] == "must-fix"
    assert manifest["adapter_name"] is None
    assert "spec_update_plan" not in manifest
    budget = manifest["budget"]
    assert budget["projected_usd"] == pytest.approx(50.0)
    assert budget["within_ceiling"] is False
    assert budget["projection_source"] == "declared"
    # profile carried no name -> a safe default is used, never a raise.
    assert manifest["profile_name"] == lt.DEFAULT_PROFILE_NAME


def test_plan_pseudocode_within_ceiling_but_production_aborts_on_load_001():
    manifest = lt.run_loadtest(
        profile=approved_profile(projected_token_cost_usd=0.5),
        budget_ceiling_usd=100, endpoint_class="production",
        adapter=PoisonAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "aborted"
    assert finding_map(manifest)["LOAD-001"] == "must-fix"
    assert manifest["budget"]["within_ceiling"] is True
    assert manifest["budget"]["projection_source"] == "declared"
    assert "spec_update_plan" not in manifest


def test_projection_unavailable_returns_partial_not_verified_before_adapter():
    manifest = lt.run_loadtest(
        profile={"peak_requests_per_second": 2, "hold_seconds": 10},  # no projection inputs
        budget_ceiling_usd=100, endpoint_class="non-production",
        adapter=PoisonAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "partial"
    assert finding_map(manifest)["LOAD-002"] == "not-verified"
    assert "spec_update_plan" not in manifest
    budget = manifest["budget"]
    assert budget["projected_usd"] is None
    assert budget["within_ceiling"] is None
    assert budget["projection_source"] == "unavailable"


def test_derived_projection_over_ceiling_also_aborts():
    """A DERIVED (not declared) projection is compared to the ceiling too."""
    manifest = lt.run_loadtest(
        profile={"request_count": 1000, "tokens_per_request_estimate": 500,
                 "price_per_1k_tokens_usd": 2.0},  # -> 1000 * 500/1000 * 2.0 = 1000 USD
        budget_ceiling_usd=1.0, endpoint_class="non-production",
        adapter=PoisonAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "aborted"
    assert finding_map(manifest)["LOAD-002"] == "must-fix"
    assert manifest["budget"]["projection_source"] == "derived"
    assert manifest["budget"]["within_ceiling"] is False


def test_endpoint_missing_yields_partial_even_with_valid_projection():
    """Declared projection within ceiling + adapter available, but no endpoint
    configured -> partial, no adapter call, no input-validation raise."""
    manifest = lt.run_loadtest(
        profile=approved_profile(), budget_ceiling_usd=100,
        endpoint_class="non-production", adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    assert manifest["status"] == "partial"
    assert manifest["endpoint_configured"] is False
    assert finding_map(manifest)["LOAD-002"] == "not-verified"
    assert "spec_update_plan" not in manifest


# --- p99 / cold-start / scale-time metrics --------------------------------
def test_diagnostics_include_p99_cold_start_and_scale_time_metrics():
    samples = [
        {"latency_ms": v, "success": True, "tokens": 50,
         "cold_start_latency_ms": cs, "time_to_scale_s": ts}
        for v, cs, ts in [
            (100, 800, 12.0), (200, None, None), (300, 950, 15.0),
            (400, None, None), (500, 1100, 18.0),
        ]
    ]
    adapter = FakeAdapter(result={"status": "complete", "samples": samples})
    manifest = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0,
        endpoint_class="non-production", adapter=adapter, generated_at=GENERATED_AT,
    )
    diag = manifest["diagnostics"]
    assert diag["p99_latency_ms"] == 500        # nearest-rank ceil(0.99*5)=5 -> 500
    assert diag["cold_start_latency_ms"] == 1100  # worst observed cold start
    assert diag["time_to_scale_s"] == 18.0        # worst observed scale time
    snippet = manifest["spec_update_plan"]["snippet"]
    assert "p99_latency_ms: 500" in snippet
    assert "cold_start_latency_ms: 1100" in snippet
    assert "time_to_scale_s: 18.00" in snippet


def test_p99_nearest_rank_is_deterministic_for_100_samples():
    summary = lt.summarize_samples(make_samples(list(range(1, 101))))
    assert summary["p50_latency_ms"] == 50   # ceil(0.50*100)=50
    assert summary["p95_latency_ms"] == 95   # ceil(0.95*100)=95
    assert summary["p99_latency_ms"] == 99   # ceil(0.99*100)=99


def test_cold_start_and_scale_time_are_null_and_omitted_when_absent():
    manifest = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0,
        endpoint_class="non-production", adapter=FakeAdapter(), generated_at=GENERATED_AT,
    )
    diag = manifest["diagnostics"]
    assert diag["p99_latency_ms"] is not None
    assert diag["cold_start_latency_ms"] is None
    assert diag["time_to_scale_s"] is None
    snippet = manifest["spec_update_plan"]["snippet"]
    assert "cold_start_latency_ms" not in snippet
    assert "time_to_scale_s" not in snippet


@pytest.mark.parametrize("bad_metric", [
    {"latency_ms": 5, "success": True, "tokens": 5, "cold_start_latency_ms": -1},
    {"latency_ms": 5, "success": True, "tokens": 5, "time_to_scale_s": float("inf")},
    {"latency_ms": 5, "success": True, "tokens": 5, "cold_start_latency_ms": "x"},
])
def test_invalid_optional_sample_metric_is_rejected(bad_metric):
    with pytest.raises(lt.LoadTestValidationError):
        lt.summarize_samples([bad_metric])


def test_spec_plan_snippet_is_safe_quoted_yaml_under_load_profile_performance():
    yaml = pytest.importorskip("yaml")
    manifest = _valid_complete_manifest()
    plan = manifest["spec_update_plan"]
    assert plan["section"] == "load_profile/performance"
    parsed = yaml.safe_load(plan["snippet"])
    assert set(parsed) == {"load_profile"}
    perf = parsed["load_profile"]["performance"]
    assert perf["captured_at"] == GENERATED_AT           # quoted -> stays a string
    assert isinstance(perf["captured_at"], str)
    assert perf["p50_latency_ms"] == 300
    assert perf["p99_latency_ms"] == 500


# --- omitted spec_update_plan key -----------------------------------------
def test_complete_manifest_may_omit_spec_update_plan_key():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SKILL_ROOT / "references" / "load-manifest.schema.json").read_text())
    manifest = _valid_complete_manifest()
    assert "spec_update_plan" in manifest          # a complete run includes it
    trimmed = {k: v for k, v in manifest.items() if k != "spec_update_plan"}
    lt.validate_load_manifest(trimmed)              # ...but complete MAY omit it
    jsonschema.validate(trimmed, schema)


def test_partial_manifest_with_null_spec_update_plan_is_rejected():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SKILL_ROOT / "references" / "load-manifest.schema.json").read_text())
    manifest = lt.run_loadtest(
        profile=configured_profile(), budget_ceiling_usd=100.0,
        endpoint_class="non-production", adapter=None, generated_at=GENERATED_AT,
    )
    assert "spec_update_plan" not in manifest
    tampered = dict(manifest)
    tampered["spec_update_plan"] = None             # a null placeholder is never valid
    with pytest.raises(ManifestValidationError):
        lt.validate_load_manifest(tampered)
    with pytest.raises(Exception):
        jsonschema.validate(tampered, schema)


# --- masked secret / common redacted-secret strings ------------------------
def test_masked_secret_marker_in_manifest_is_rejected(tmp_path):
    manifest = _valid_complete_manifest()
    tampered = json.loads(json.dumps(manifest))
    tampered["profile_name"] = "checkout ****** smoke"
    with pytest.raises(lt.LoadTestPrivacyError):
        lt.write_load_manifest(tmp_path / "m.json", tampered)
    assert not (tmp_path / "m.json").exists()


@pytest.mark.parametrize("secretish", [
    "https://x.blob.core.windows.net/c?sig=abcd1234efgh5678ijkl&se=2026",
    "AccountKey=Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5;Endpoint=x",
    "SharedAccessSignature=verysecretsignaturevalue123",
    "-----BEGIN PRIVATE KEY-----abcd-----END PRIVATE KEY-----",
])
def test_common_redacted_and_secret_strings_are_rejected(tmp_path, secretish):
    manifest = _valid_complete_manifest()
    tampered = json.loads(json.dumps(manifest))
    tampered["adapter_name"] = secretish
    with pytest.raises(lt.LoadTestPrivacyError):
        lt.write_load_manifest(tmp_path / "m.json", tampered)
    assert not (tmp_path / "m.json").exists()


def test_privacy_scan_allows_opaque_metric_ids_and_redacted_sentinel():
    """No false positives on opaque IDs / our own redaction sentinel."""
    lt._assert_no_unsafe_content({
        "run_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "trace": "a1b2c3d4e5f6a7b8",
        "adapter_error": "[REDACTED]",
    })  # must not raise


def test_scrub_text_normalizes_masked_marker_and_secret_shapes_to_sentinel():
    assert ad.scrub_text("Authorization: ******") == "Authorization: [REDACTED]"
    assert "[REDACTED]" in ad.scrub_text("blob?sig=abcd1234efgh5678")
    assert "[REDACTED]" in ad.scrub_text("AccountKey=supersecretvalue;Endpoint=y")
    assert "******" not in ad.scrub_text("value=******")


# --- findings uniqueness (both validation layers) --------------------------
def test_findings_are_exactly_the_three_required_ids():
    manifest = _valid_complete_manifest()
    ids = sorted(finding["id"] for finding in manifest["findings"])
    assert ids == ["LOAD-001", "LOAD-002", "LOAD-003"]


@pytest.mark.parametrize("mutate", ["duplicate", "missing", "extra", "unknown"])
def test_findings_uniqueness_is_enforced_by_both_layers(mutate):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SKILL_ROOT / "references" / "load-manifest.schema.json").read_text())
    manifest = _valid_complete_manifest()
    tampered = json.loads(json.dumps(manifest))
    findings = tampered["findings"]
    if mutate == "duplicate":
        findings[2] = {"id": "LOAD-001", "status": "pass"}     # LOAD-003 now missing
    elif mutate == "missing":
        tampered["findings"] = findings[:2]                    # only two findings
    elif mutate == "extra":
        findings.append({"id": "LOAD-001", "status": "pass"})  # a fourth finding
    elif mutate == "unknown":
        findings[0] = {"id": "LOAD-042", "status": "pass"}     # unknown id
    with pytest.raises(ManifestValidationError):
        lt.validate_load_manifest(tampered)
    with pytest.raises(Exception):
        jsonschema.validate(tampered, schema)
