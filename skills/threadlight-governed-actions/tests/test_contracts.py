"""Tests for threadlight-governed-actions contracts, canonicalization, and
the two Task-1 JSON Schemas (manifest + apply-plan) plus the finding catalog.

``jsonschema`` is a hard dependency of this test module, not an optional
one: schema conformance is the whole point of these tests, so a missing
``jsonschema`` install must fail collection loudly (a normal
``ModuleNotFoundError`` at import time) rather than silently downgrade
every schema-validation test into a no-op/skip that still reports green.
The RFC 3339 ``date-time`` format checker is likewise required, not
optional — see the module-level assertion below. Installing it (Task 15
will wire this into CI) is:
    pip install "jsonschema[format]"

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_contracts.py -q
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import stat
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path

import jsonschema
import pytest

import canonical
import contracts


REFERENCES = Path(__file__).resolve().parent.parent / "references"

# A single validator-construction helper used everywhere in this module so
# every schema check enables format assertions (Draft 2020-12 treats
# "format" as an annotation-only keyword unless a FormatChecker is
# attached) in addition to the enforceable "pattern" constraint each
# timestamp $def also carries.
_FORMAT_CHECKER = jsonschema.FormatChecker()

# Loud, not silent: without the ``date-time`` checker registered (which
# requires the ``jsonschema[format]`` extra, e.g. via ``rfc3339-validator``),
# every "format": "date-time" assertion below would silently become a no-op
# — the tests would keep reporting green while validating nothing about
# RFC 3339 shape via format checking (the "pattern" constraint on each
# timestamp $def still applies independently, but this module's format-
# checking coverage would quietly stop being real). Fail collection
# immediately instead of masking that gap.
assert "date-time" in _FORMAT_CHECKER.checkers, (
    "installed jsonschema lacks a 'date-time' format checker; install "
    '`pip install "jsonschema[format]"` (which pulls in rfc3339-validator) '
    "so format assertions in these tests actually validate rather than "
    "silently no-op"
)


def _validator_for(schema):
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


# ---------------------------------------------------------------------------
# Public constants (contracts.py)
# ---------------------------------------------------------------------------


def test_public_constants_match_spec():
    assert contracts.SCHEMA_VERSION == "1.0.0"
    assert contracts.ASSESSOR_VERSION == "0.1.0"
    assert contracts.SKILL_VERSION == "0.1.0"
    assert contracts.SUPPORTED_PHASES == ("design", "pre-deploy", "post-deploy")
    assert contracts.CONSEQUENCE_CLASSES == (
        "read",
        "write",
        "external-egress",
        "irreversible",
    )
    assert contracts.EXECUTION_MODES == (
        "interactive",
        "batch",
        "background",
        "subagent",
        "direct-tool",
        "provider-hosted-tool",
    )
    assert contracts.STATUSES == (
        "pass",
        "must-fix",
        "should-fix",
        "not-verified",
        "not-applicable",
    )
    assert contracts.VERDICTS == ("governed", "partial", "ungoverned")


# ---------------------------------------------------------------------------
# Published skill version (SKILL.md frontmatter)
# ---------------------------------------------------------------------------


SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"


def _skill_frontmatter() -> str:
    """Return the SKILL.md YAML frontmatter block (between the first two
    ``---`` fences), the same slice the Copilot CLI skill loader parses."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    return text.split("---", 2)[1]


def test_skill_md_frontmatter_declares_the_published_skill_name():
    assert "name: threadlight-governed-actions\n" in _skill_frontmatter()


def test_skill_md_version_matches_the_cli_assessor_version():
    """The published skill version, the skill constant, and the assessor
    version the CLI stamps into every artifact are one number.

    ``render.build_manifest``/``render.build_apply_plan`` write
    ``contracts.ASSESSOR_VERSION`` into ``assessor_version``, so a
    customer reading the evidence pack can only trace it back to a
    published skill version if these never drift. Importing the CLI entry
    module here (rather than re-reading the constant from ``contracts``
    alone) proves the value the CLI actually resolves is the published
    one.
    """
    import governed_actions
    import render

    assert contracts.SKILL_VERSION == "0.1.0"
    assert contracts.ASSESSOR_VERSION == contracts.SKILL_VERSION
    assert governed_actions.contracts.ASSESSOR_VERSION == contracts.ASSESSOR_VERSION
    assert render.contracts.ASSESSOR_VERSION == contracts.ASSESSOR_VERSION
    assert f'version: "{contracts.SKILL_VERSION}"' in _skill_frontmatter(), (
        f'SKILL.md frontmatter must declare version: "{contracts.SKILL_VERSION}"'
    )


# ---------------------------------------------------------------------------
# Published prose (CHANGELOG.md / SKILL.md) must never drift from the
# manifest schema this skill actually emits — Task 15 spec-review findings.
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CHANGELOG_MD = REPO_ROOT / "CHANGELOG.md"


def test_changelog_publishes_the_exact_manifest_schema_identifier():
    """CHANGELOG.md must cite the manifest ``schema`` const the skill
    actually emits (``render.MANIFEST_SCHEMA``), never the unpublished
    dotted spelling ``threadlight.governed-actions/v1``."""
    import render

    changelog = CHANGELOG_MD.read_text(encoding="utf-8")

    assert (
        "threadlight.governed-actions/v1" not in changelog
    ), "CHANGELOG.md must not cite the unpublished 'threadlight.governed-actions/v1' schema id"
    assert f"`{render.MANIFEST_SCHEMA}`" in changelog, (
        f"CHANGELOG.md must cite the exact emitted schema id `{render.MANIFEST_SCHEMA}`"
    )
    assert render.MANIFEST_SCHEMA == _manifest_schema()["properties"]["schema"]["const"]


def test_skill_md_never_claims_the_manifest_records_a_ghcp_internal_loop_field():
    """The ``changePlane`` def in the manifest schema is closed
    (``additionalProperties: false``) and has no
    ``ghcp_internal_loop_intercepted`` property, so SKILL.md must not claim
    the manifest "always records" that field. It may only describe the
    boundary: this assessor never claims/intercepts GitHub Copilot's
    internal loop and evaluates solely declared/static change-plane
    controls.
    """
    skill = SKILL_MD.read_text(encoding="utf-8")
    change_plane_props = set(_manifest_schema()["$defs"]["changePlane"]["properties"])

    assert "ghcp_internal_loop_intercepted" not in change_plane_props
    assert "ghcp_internal_loop_intercepted" not in skill, (
        "SKILL.md must not name a manifest output field that the schema does not emit"
    )
    assert "never claims, and never intercepts, GitHub Copilot's own internal loop" in skill
    assert "declared, static change-plane controls" in skill


def _manifest_schema():
    return json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )


def _apply_plan_schema():
    return json.loads(
        (REFERENCES / "governed-actions-apply-plan.schema.json").read_text(
            encoding="utf-8"
        )
    )


def test_manifest_schema_phase_enum_matches_contracts_supported_phases():
    schema = _manifest_schema()
    assert tuple(schema["$defs"]["phase"]["enum"]) == contracts.SUPPORTED_PHASES


def test_manifest_schema_status_enum_matches_contracts_statuses():
    schema = _manifest_schema()
    assert tuple(schema["$defs"]["status"]["enum"]) == contracts.STATUSES


def test_manifest_schema_consequence_enum_matches_contracts_consequence_classes():
    schema = _manifest_schema()
    assert (
        tuple(schema["$defs"]["consequence"]["enum"])
        == contracts.CONSEQUENCE_CLASSES
    )


def test_manifest_schema_execution_mode_enum_matches_contracts_execution_modes():
    schema = _manifest_schema()
    assert (
        tuple(schema["$defs"]["executionMode"]["enum"]) == contracts.EXECUTION_MODES
    )


def test_manifest_schema_summary_verdict_enum_matches_contracts_verdicts():
    schema = _manifest_schema()
    verdict_property = _find_summary_verdict_enum(schema)
    assert tuple(verdict_property) == contracts.VERDICTS


def _find_summary_verdict_enum(schema):
    return schema["$defs"]["summary"]["properties"]["verdict"]["enum"]


def test_apply_plan_schema_status_enum_matches_contracts_statuses():
    schema = _apply_plan_schema()
    assert tuple(schema["$defs"]["status"]["enum"]) == contracts.STATUSES


def test_unsafe_target_error_is_value_error():
    assert issubclass(contracts.UnsafeTargetError, ValueError)


def test_frozen_dataclasses_are_immutable():
    source = contracts.SourceRef(repository="o/r", commit="a" * 40, dirty=False)
    with pytest.raises(FrozenInstanceError):
        source.commit = "b" * 40  # type: ignore[misc]


def test_finding_defaults():
    finding = contracts.Finding(
        finding_id="ACT-001",
        status="not-verified",
        phase="design",
        plane="runtime",
        reason_code="no-inventory",
        summary="s",
        details="d",
    )
    assert finding.affected_actions == ()
    assert finding.affected_paths == ()
    assert finding.evidence_refs == ()
    assert finding.remediation_ids == ()
    assert finding.residual_risk_ref is None


def test_action_record_defaults():
    action = contracts.ActionRecord(
        action_id="a1",
        display_name="Send Email",
        aliases=(),
        owner=None,
        declaration_refs=(),
        implementation_refs=(),
        input_schema_sha256=None,
        output_schema_sha256=None,
        source="declared",
        consequence="external-egress",
        secondary_consequences=(),
        reversible=False,
        compensation_ref=None,
        execution_modes=("direct-tool",),
        provider_hosted=False,
        approval_required=None,
    )
    assert action.policy_ids == ()
    assert action.known_runtime_paths == ()
    assert action.inventory_status == "not-verified"


def test_assessment_result_defaults():
    result = contracts.AssessmentResult(
        source=contracts.SourceRef(repository="o/r", commit="a" * 40, dirty=False),
        actions=(),
        paths=(),
        probes=(),
        findings=(),
        evidence=(),
    )
    assert result.policy_hashes == ()
    assert result.pins == {}
    assert result.conformance_claims == ()
    assert result.conformance_reports == ()
    assert result.change_plane == {}
    assert result.residual_risks == ()


# ---------------------------------------------------------------------------
# canonical_bytes
# ---------------------------------------------------------------------------


def test_canonical_bytes_sorts_keys_and_uses_compact_separators():
    assert canonical.canonical_bytes({"z": 1, "a": ["x", True]}) == (
        b'{"a":["x",true],"z":1}'
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_canonical_bytes_rejects_non_finite_floats(bad):
    with pytest.raises(canonical.CanonicalizationError, match="finite"):
        canonical.canonical_bytes({"value": bad})


def test_canonical_bytes_rejects_non_finite_floats_nested_in_list():
    with pytest.raises(canonical.CanonicalizationError, match="finite"):
        canonical.canonical_bytes([1, 2, {"x": [float("nan")]}])


def test_canonical_bytes_is_utf8_and_ascii_preserving():
    assert canonical.canonical_bytes({"name": "café"}) == '{"name":"café"}'.encode(
        "utf-8"
    )


def test_canonical_bytes_rejects_unpaired_surrogate():
    """A lone surrogate code point (e.g. ``\\ud800``) is a value
    ``json.dumps(..., ensure_ascii=False)`` happily returns as a ``str``
    but that ``str`` cannot itself be encoded to UTF-8 with the strict
    error handler this function uses. That ``UnicodeEncodeError`` must
    be contained here and surfaced as the same
    :class:`canonical.CanonicalizationError` every other unencodable
    value raises -- never leaked to callers as an unrelated,
    uncontained ``UnicodeEncodeError``."""
    with pytest.raises(canonical.CanonicalizationError):
        canonical.canonical_bytes({"name": "\ud800"})


def test_canonical_bytes_rejects_unpaired_surrogate_nested_in_list():
    with pytest.raises(canonical.CanonicalizationError):
        canonical.canonical_bytes(["ok", {"x": ["\udc00"]}])


def test_canonical_bytes_rejects_unsupported_type():
    """A type json.dumps cannot serialize (e.g. a set) must surface as a
    CanonicalizationError, not an unrelated TypeError leaking straight out
    of json.dumps."""
    with pytest.raises(canonical.CanonicalizationError):
        canonical.canonical_bytes({"value": {1, 2, 3}})


def test_canonical_bytes_rejects_circular_reference():
    """A self-referencing container must surface as a
    CanonicalizationError, not the raw ValueError json.dumps raises for
    circular references."""
    circular: dict[str, object] = {}
    circular["self"] = circular
    with pytest.raises(canonical.CanonicalizationError):
        canonical.canonical_bytes(circular)


def test_canonical_bytes_rejects_circular_reference_nested_in_list():
    circular_list: list[object] = []
    circular_list.append(circular_list)
    with pytest.raises(canonical.CanonicalizationError):
        canonical.canonical_bytes({"items": circular_list})


# ---------------------------------------------------------------------------
# sha256_hex
# ---------------------------------------------------------------------------


def test_sha256_hex_matches_hashlib():
    data = b"hello world"
    assert canonical.sha256_hex(data) == hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# hash_files
# ---------------------------------------------------------------------------


def test_hash_files_binds_repo_relative_path_and_exact_bytes(tmp_path):
    content = b'{"effect":"deny"}\n'
    (tmp_path / "policy.json").write_bytes(content)

    result = canonical.hash_files(tmp_path, [Path("policy.json")])

    assert result["algorithm"] == "sha256"
    content_hash = hashlib.sha256(content).hexdigest()
    assert result["files"] == [
        {"path": "policy.json", "sha256": f"sha256:{content_hash}"}
    ]
    expected_set = hashlib.sha256(
        canonical.canonical_bytes(result["files"])
    ).hexdigest()
    assert result["set_sha256"] == f"sha256:{expected_set}"


def test_hash_files_rejects_paths_that_escape_root(tmp_path):
    # Both the assessment root and the escaping file must live inside this
    # test's own unique tmp_path -- never in the shared basetemp
    # (tmp_path.parent), which is reused across many tests in the same run
    # and would leak a stray "outside.json" into unrelated ground/upgrade
    # path-escape tests.
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    with pytest.raises(canonical.CanonicalizationError):
        canonical.hash_files(root, [outside])


def test_hash_files_set_sha256_is_invariant_to_input_order(tmp_path):
    """hash_files behaves like a *set* of files: the same files supplied in
    a different order must produce the same set_sha256 (and the same
    sorted files list), because the set of files hashed — not the order
    they were passed in — is what the digest binds."""
    (tmp_path / "a.json").write_bytes(b'{"a":1}')
    (tmp_path / "b.json").write_bytes(b'{"b":2}')

    forward = canonical.hash_files(tmp_path, [Path("a.json"), Path("b.json")])
    reverse = canonical.hash_files(tmp_path, [Path("b.json"), Path("a.json")])

    assert forward["files"] == reverse["files"]
    assert forward["set_sha256"] == reverse["set_sha256"]


def test_hash_files_dedupes_duplicate_input_paths(tmp_path):
    """Passing the same repository-relative path twice must not double-
    count it in the file list or change set_sha256 relative to passing it
    once — hash_files is a set of (path, bytes) pairs, not a list."""
    (tmp_path / "policy.json").write_bytes(b'{"effect":"deny"}\n')

    once = canonical.hash_files(tmp_path, [Path("policy.json")])
    duplicated = canonical.hash_files(
        tmp_path, [Path("policy.json"), Path("policy.json"), Path("policy.json")]
    )

    assert duplicated["files"] == once["files"]
    assert len(duplicated["files"]) == 1
    assert duplicated["set_sha256"] == once["set_sha256"]


def test_hash_files_dedupes_equivalent_path_spellings(tmp_path):
    """A ``./``-prefixed spelling of the same repository-relative path must
    collapse to the same single entry as the bare spelling."""
    (tmp_path / "policy.json").write_bytes(b'{"effect":"deny"}\n')

    result = canonical.hash_files(
        tmp_path, [Path("policy.json"), Path("./policy.json")]
    )

    assert len(result["files"]) == 1
    assert result["files"][0]["path"] == "policy.json"


def test_hash_files_raises_canonicalization_error_for_missing_file(tmp_path):
    """A read failure (the file simply doesn't exist) must surface as a
    :class:`canonical.CanonicalizationError` carrying the offending path,
    not a raw ``FileNotFoundError`` — hash_files' contract is that every
    failure mode is a ``CanonicalizationError``, so callers only ever need
    to catch one exception type."""
    with pytest.raises(canonical.CanonicalizationError, match="policy.json"):
        canonical.hash_files(tmp_path, [Path("policy.json")])


def test_hash_files_raises_canonicalization_error_for_directory_path(tmp_path):
    """Passing a directory (not a file) must also normalize to
    ``CanonicalizationError`` with path context, rather than leaking the
    raw ``IsADirectoryError``/``PermissionError`` os.read_bytes raises."""
    (tmp_path / "a-directory").mkdir()
    with pytest.raises(canonical.CanonicalizationError, match="a-directory"):
        canonical.hash_files(tmp_path, [Path("a-directory")])


# ---------------------------------------------------------------------------
# validate_payload_free_audit
# ---------------------------------------------------------------------------


COMPLETE_AUDIT_RECORD = {
    "schema": "threadlight-governed-actions-audit/v1",
    "event_id": "evt-0001",
    "timestamp": "2026-01-01T00:00:00Z",
    "source_commit": "a" * 40,
    "deployment_id": "dep-0001",
    "action_id": "send_email",
    "path_id": "path-0001",
    "policy_id": "policy-0001",
    "rule_id": "rule-0001",
    "policy_hash": f"sha256:{'0' * 64}",
    "action_hash": f"sha256:{'1' * 64}",
    "decision": "deny",
    "reason_code": "policy-denied",
    "approval_redemption_hash": f"sha256:{'2' * 64}",
    "correlation_id": "corr-0001",
    "interceptor_duration_ms": 12,
    "error_class": None,
    "delivery_status": "delivered",
}


def test_validate_payload_free_audit_accepts_complete_v1_record():
    canonical.validate_payload_free_audit(COMPLETE_AUDIT_RECORD)


@pytest.mark.parametrize(
    "banned_key",
    [
        "prompt",
        "messages",
        "arguments",
        "args",
        "input",
        "output",
        "result",
        "secret",
        "token",
        "authorization",
        "body",
        "payload",
    ],
)
def test_validate_payload_free_audit_rejects_banned_keys_at_top_level(banned_key):
    record = dict(COMPLETE_AUDIT_RECORD)
    record[banned_key] = "should not be here"
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


def test_validate_payload_free_audit_rejects_banned_keys_recursively():
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["context"] = {"nested": {"arguments": {"to": "user@example.com"}}}
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


def test_validate_payload_free_audit_rejects_banned_keys_in_lists():
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["context"] = [{"input": "secret prompt text"}]
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


def test_validate_payload_free_audit_permits_hash_suffixed_fields():
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["input_hash"] = f"sha256:{'3' * 64}"
    record["output_hash"] = f"sha256:{'4' * 64}"
    canonical.validate_payload_free_audit(record)


@pytest.mark.parametrize(
    "mixed_case_key",
    ["Prompt", "TOKEN", "Args", "ArGuMeNts", "Secret", "Body", "AUTHORIZATION"],
)
def test_validate_payload_free_audit_rejects_banned_keys_case_insensitively(
    mixed_case_key,
):
    """A banned key must be caught regardless of letter casing (mixed/upper),
    proving the comparison lower-cases the key rather than relying on the
    caller to write it in one specific case."""
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record[mixed_case_key] = "should not be here"
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


def test_validate_payload_free_audit_rejects_mixed_case_banned_keys_recursively():
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["context"] = {"Nested": {"ARGUMENTS": {"to": "user@example.com"}}}
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


@pytest.mark.parametrize(
    "hash_key",
    ["Input_Hash", "OUTPUT_HASH", "InputHash", "outputHash"],
)
def test_validate_payload_free_audit_permits_hash_suffixed_fields_any_case(hash_key):
    """Exact (not substring) matching means a differently-cased hash field
    such as ``Input_Hash``/``OUTPUT_HASH`` is never confused with the banned
    ``input``/``output`` keys themselves."""
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record[hash_key] = f"sha256:{'5' * 64}"
    canonical.validate_payload_free_audit(record)


@pytest.mark.parametrize(
    "banned_key",
    [
        "password",
        "passwd",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "credential",
        "credentials",
        "secrets",
        "cookie",
        "content",
        "message",
    ],
)
def test_validate_payload_free_audit_rejects_raw_secret_and_content_keys(banned_key):
    """Common raw secret/credential/content field names must be rejected
    just like the original prompt/argument/output taxonomy — a governed
    action audit trail must never carry a literal password, API key,
    session cookie, or free-text message/content body."""
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record[banned_key] = "should not be here"
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


@pytest.mark.parametrize(
    "mixed_case_key",
    [
        "Password",
        "PASSWD",
        "Api_Key",
        "APIKEY",
        "Access_Token",
        "REFRESH_TOKEN",
        "Credential",
        "Credentials",
        "SECRETS",
        "Cookie",
        "Content",
        "MESSAGE",
    ],
)
def test_validate_payload_free_audit_rejects_secret_and_content_keys_case_insensitively(
    mixed_case_key,
):
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record[mixed_case_key] = "should not be here"
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


def test_validate_payload_free_audit_rejects_new_banned_keys_recursively():
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["context"] = {"nested": {"api_key": "sk-should-not-be-here"}}
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


@pytest.mark.parametrize(
    "hash_key",
    [
        "password_hash",
        "Passwd_Hash",
        "API_KEY_HASH",
        "access_token_hash",
        "RefreshTokenHash",
        "credential_hash",
        "credentials_hash",
        "secrets_hash",
        "cookie_hash",
        "content_hash",
        "message_hash",
    ],
)
def test_validate_payload_free_audit_permits_hash_suffixed_new_banned_keys(hash_key):
    """Exact-match matching must keep applying to the newly added
    secret/content keys too: a derived hash field like ``password_hash`` or
    ``content_hash`` is a structural reference, not the raw secret/content
    itself, and must remain explicitly permitted."""
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record[hash_key] = f"sha256:{'6' * 64}"
    canonical.validate_payload_free_audit(record)


def test_validate_payload_free_audit_fails_closed_on_non_json_native_value():
    """A nested value that is not JSON-native (a set here, which is also
    unordered and therefore could never be hashed/canonicalized
    deterministically) must be rejected rather than silently skipped just
    because it isn't a dict/list/tuple the recursive walk knows how to
    descend into — an audit record must only ever contain content the
    validator can fully account for."""
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["context"] = {"tags": {"a", "b", "c"}}
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


def test_validate_payload_free_audit_fails_closed_on_non_json_native_value_in_list():
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["context"] = [object()]
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


@pytest.mark.parametrize("scalar", ["text", 1, 1.5, True, False, None])
def test_validate_payload_free_audit_permits_json_native_scalars(scalar):
    """Sanity check for the fail-closed rule above: every JSON-native
    scalar type must remain explicitly permitted, not accidentally swept
    into the new non-JSON-native rejection."""
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["context"] = {"value": scalar}
    canonical.validate_payload_free_audit(record)


# ---------------------------------------------------------------------------
# atomic_write_bytes / atomic_write_json
# ---------------------------------------------------------------------------


def test_atomic_write_bytes_writes_complete_content_and_leaves_no_tmp(tmp_path):
    destination = tmp_path / "manifest.json"
    data = b'{"hello":"world"}'
    canonical.atomic_write_bytes(destination, data)

    assert destination.read_bytes() == data
    leftovers = list(tmp_path.glob(".manifest.json.*.tmp"))
    assert leftovers == []


def test_atomic_write_bytes_creates_parent_directories(tmp_path):
    destination = tmp_path / "nested" / "dir" / "manifest.json"
    canonical.atomic_write_bytes(destination, b"data")
    assert destination.read_bytes() == b"data"


def test_atomic_write_json_round_trips_canonical_bytes(tmp_path):
    destination = tmp_path / "manifest.json"
    value = {"z": 1, "a": ["x", True]}
    canonical.atomic_write_json(destination, value)
    assert destination.read_bytes() == canonical.canonical_bytes(value)


def test_atomic_write_json_rejects_non_finite_and_leaves_no_tmp(tmp_path):
    destination = tmp_path / "manifest.json"
    with pytest.raises(canonical.CanonicalizationError):
        canonical.atomic_write_json(destination, {"value": float("nan")})
    assert not destination.exists()
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []


def test_atomic_write_bytes_creates_temp_file_in_destination_directory(
    tmp_path, monkeypatch
):
    """The NamedTemporaryFile must be created with dir=destination.parent
    (never the platform default temp dir), because os.replace is only
    atomic when source and destination share a filesystem."""
    real_named_temporary_file = tempfile.NamedTemporaryFile
    captured_dir = {}

    def spying_named_temporary_file(*args, **kwargs):
        captured_dir["dir"] = kwargs.get("dir")
        return real_named_temporary_file(*args, **kwargs)

    monkeypatch.setattr(
        canonical.tempfile, "NamedTemporaryFile", spying_named_temporary_file
    )

    destination = tmp_path / "manifest.json"
    canonical.atomic_write_bytes(destination, b"data")

    assert captured_dir["dir"] == destination.parent
    assert destination.read_bytes() == b"data"


def test_atomic_write_bytes_fsyncs_temp_file_then_parent_directory(
    tmp_path, monkeypatch
):
    """Proves both fsyncs actually happen (not merely present in source):
    the first fsync is on a regular file (the temp file, still open for
    write), the second is on the parent directory (durability for the
    rename itself)."""
    real_fsync = os.fsync
    fsync_call_is_dir = []

    def spying_fsync(fd):
        fsync_call_is_dir.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        return real_fsync(fd)

    monkeypatch.setattr(canonical.os, "fsync", spying_fsync)

    destination = tmp_path / "manifest.json"
    canonical.atomic_write_bytes(destination, b"data")

    assert fsync_call_is_dir == [False, True]
    assert destination.read_bytes() == b"data"


def test_atomic_write_bytes_mid_write_failure_preserves_destination_and_cleans_temp(
    tmp_path, monkeypatch
):
    """Injects a real OS-level failure (fsync on the temp file raising)
    strictly after the temp file has been created and written to, but
    before os.replace is ever called. Proves: (1) a pre-existing destination
    file is completely untouched, (2) the temp file created for this call is
    removed, (3) the original exception still propagates. This does not rely
    on canonical_bytes raising first — the failure is injected inside
    atomic_write_bytes's own write path."""
    destination = tmp_path / "manifest.json"
    original_content = b"original-content"
    destination.write_bytes(original_content)

    def failing_fsync(fd):
        raise OSError("simulated mid-write fsync failure")

    monkeypatch.setattr(canonical.os, "fsync", failing_fsync)

    with pytest.raises(OSError, match="simulated mid-write fsync failure"):
        canonical.atomic_write_bytes(destination, b"new-content-that-must-not-land")

    assert destination.read_bytes() == original_content
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []


def test_atomic_write_bytes_replace_failure_removes_only_its_own_temp(
    tmp_path, monkeypatch
):
    """Injects a failure in os.replace itself (the pre-replace boundary: the
    temp file is fully written+fsynced but never lands at the destination
    path). Proves: (1) a pre-existing destination survives untouched,
    (2) an unrelated file that happens to match the temp-file glob pattern
    is left alone (cleanup removes only the temp file this call created,
    not anything else matching the pattern), (3) the original exception
    propagates."""
    destination = tmp_path / "manifest.json"
    original_content = b"original-content"
    destination.write_bytes(original_content)

    unrelated_tmp = tmp_path / ".manifest.json.unrelated-leftover.tmp"
    unrelated_tmp.write_bytes(b"do-not-touch-me")

    def failing_replace(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(canonical.os, "replace", failing_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        canonical.atomic_write_bytes(destination, b"new-content-that-must-not-land")

    assert destination.read_bytes() == original_content
    leftovers = sorted(p.name for p in tmp_path.glob(".manifest.json.*.tmp"))
    assert leftovers == [".manifest.json.unrelated-leftover.tmp"]
    assert unrelated_tmp.read_bytes() == b"do-not-touch-me"


# ---------------------------------------------------------------------------
# JSON Schema fixtures
# ---------------------------------------------------------------------------


def _minimal_manifest():
    return {
        "schema": "threadlight-governed-actions-manifest/v1",
        "assessor": {
            "name": "threadlight-governed-actions",
            "version": "0.1.0",
            "adapter": "generic",
        },
        "phase": "design",
        "captured_at": "2026-01-01T00:00:00Z",
        "source": {
            "repository": "o/r",
            "commit": "a" * 40,
            "dirty": False,
        },
        "pins": {
            "dependencies": [],
            "specifications": [],
            "probe_suite": {"name": "governed-actions-probes", "version": "0.1.0"},
        },
        "policy_hashes": [],
        "action_inventory": [],
        "mediation_paths": [],
        "conformance": {
            "claims": [],
            "reports": [],
            "application_probes": [],
        },
        "change_plane": {
            "repository": "o/r",
            "workflows": [],
            "identities": [],
        },
        "findings": [],
        "evidence": [],
        "freshness": {
            "status": "fresh",
            "valid_for_hours": 24,
            "oldest_source_at": None,
            "expires_at": None,
        },
        "residual_risks": [],
        "summary": {
            "verdict": "ungoverned",
            "pass": [],
            "must_fix": [],
            "should_fix": [],
            "not_verified": [],
            "not_applicable": [],
        },
    }


def _minimal_apply_plan():
    return {
        "schema": "threadlight-governed-actions-apply-plan/v1",
        "assessor_version": "0.1.0",
        "source_commit": "a" * 40,
        "captured_at": "2026-01-01T00:00:00Z",
        "manifest_sha256": f"sha256:{'0' * 64}",
        "self_applying": False,
        "items": [],
    }


def _sha(digit):
    return f"sha256:{digit * 64}"


def _populated_manifest():
    """A manifest with every top-level collection non-empty, exercising
    every major ``$def`` (action, path, probe, conformanceClaim,
    conformanceReport, evidenceRef, finding, policyHash, changePlane*,
    residualRisk) at least once, so schema drift in a nested def is
    caught even when the minimal fixture's empty arrays would hide it."""
    document = _minimal_manifest()
    document["pins"]["dependencies"] = [{"name": "jsonschema", "version": "4.26.0"}]
    document["pins"]["specifications"] = [
        {"name": "governed-actions-manifest", "version": "1.0.0"}
    ]
    document["policy_hashes"] = [{"path": "policy.json", "sha256": _sha("1")}]
    document["action_inventory"] = [
        {
            "action_id": "send_email",
            "display_name": "Send Email",
            "aliases": ["email.send"],
            "owner": "platform-team",
            "declaration_refs": ["src/actions/send_email.py:12"],
            "implementation_refs": ["src/actions/send_email.py:40"],
            "input_schema_sha256": _sha("2"),
            "output_schema_sha256": _sha("3"),
            "source": "static-scan",
            "consequence": "external-egress",
            "secondary_consequences": ["write"],
            "reversible": False,
            "compensation_ref": None,
            "execution_modes": ["interactive", "batch"],
            "provider_hosted": False,
            "approval_required": True,
            "policy_ids": ["policy-1"],
            "known_runtime_paths": ["path-1"],
            "inventory_status": "pass",
        }
    ]
    document["mediation_paths"] = [
        {
            "path_id": "path-1",
            "action_id": "send_email",
            "mode": "interactive",
            "nodes": ["cli", "agent", "action"],
            "pre_action_seam": "policy-gate",
            "equivalent_control_ref": None,
            "covered": True,
            "discovered": True,
            "executed": True,
            "status": "pass",
            "evidence_refs": ["evidence-1"],
        }
    ]
    document["conformance"] = {
        "claims": [
            {
                "claim_id": "claim-1",
                "description": "hooks fire before send_email",
                "status": "pass",
                "evidence_refs": ["evidence-1"],
            }
        ],
        "reports": [
            {
                "report_id": "report-1",
                "tool": "governed-actions-probe-suite",
                "version": "0.1.0",
                "generated_at": "2026-01-01T00:00:00Z",
                "summary": "1 probe run, 1 pass",
                "evidence_refs": ["evidence-1"],
            }
        ],
        "application_probes": [
            {
                "probe_id": "probe-1",
                "action_id": "send_email",
                "path_id": "path-1",
                "mode": "interactive",
                "status": "pass",
                "reason_code": "hook-observed",
                "expected_sha256": _sha("4"),
                "observed_sha256": _sha("5"),
                "evidence_refs": ["evidence-1"],
            }
        ],
    }
    document["change_plane"]["workflows"] = [
        {"path": ".github/workflows/deploy.yml", "sha256": _sha("4")}
    ]
    document["change_plane"]["identities"] = [
        {"identity": "deploy-bot", "kind": "service-principal"}
    ]
    document["findings"] = [
        {
            "finding_id": "ACT-001",
            "status": "must-fix",
            "phase": "design",
            "plane": "runtime",
            "reason_code": "no-inventory",
            "summary": "no action inventory found",
            "details": "static scan found zero declared actions",
            "affected_actions": ["send_email"],
            "affected_paths": ["path-1"],
            "evidence_refs": ["evidence-1"],
            "remediation_ids": [],
            "residual_risk_ref": None,
        }
    ]
    document["evidence"] = [
        {
            "evidence_id": "evidence-1",
            "kind": "static-scan",
            "source": "src/actions/send_email.py",
            "sha256": _sha("5"),
            "collected_at": "2026-01-01T00:00:00Z",
            "freshness_seconds": 0,
            "live_verified": False,
            "phase": "design",
            "repository": "o/r",
            "source_commit": "a" * 40,
            "target_environment": None,
            "policy_set_sha256": _sha("6"),
        }
    ]
    document["residual_risks"] = [
        {
            "residual_risk_id": "risk-1",
            "finding_id": "ACT-001",
            "description": "no compensating control identified yet",
        }
    ]
    document["summary"] = {
        "verdict": "partial",
        "pass": [],
        "must_fix": ["ACT-001"],
        "should_fix": [],
        "not_verified": [],
        "not_applicable": [],
    }
    return document


def _validate(document, schema_path):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    _validator_for(schema).validate(document)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False


def test_manifest_schema_is_draft_2020_12_and_accepts_minimal_document():
    _validate(_minimal_manifest(), REFERENCES / "governed-actions-manifest.schema.json")


def test_manifest_schema_accepts_populated_document_exercising_every_major_def():
    _validate(_populated_manifest(), REFERENCES / "governed-actions-manifest.schema.json")


def test_manifest_schema_rejects_residual_risk_with_unknown_finding_id():
    """``residualRisk.finding_id`` must be bound to the fixed 18-ID finding
    catalog (via ``$defs/findingId``), not left as an arbitrary free-form
    string, so a residual risk can never silently reference a finding_id
    that doesn't exist in the taxonomy."""
    document = _populated_manifest()
    document["residual_risks"][0]["finding_id"] = "NOT-A-REAL-FINDING-ID"
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    with pytest.raises(jsonschema.exceptions.ValidationError):
        _validator_for(schema).validate(document)


def test_manifest_schema_accepts_residual_risk_with_catalog_finding_id():
    document = _populated_manifest()
    document["residual_risks"][0]["finding_id"] = "ACT-002"
    _validate(document, REFERENCES / "governed-actions-manifest.schema.json")


@pytest.mark.parametrize(
    "status_array",
    ["pass", "must_fix", "should_fix", "not_verified", "not_applicable"],
)
def test_manifest_schema_rejects_summary_status_array_with_unknown_finding_id(
    status_array,
):
    """Every ``summary.<status>`` array element must also be bound to the
    fixed 18-ID finding catalog via ``$defs/findingId`` — a summary bucket
    is a partition of finding IDs, not an arbitrary string list."""
    document = _populated_manifest()
    document["summary"][status_array] = ["NOT-A-REAL-FINDING-ID"]
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    with pytest.raises(jsonschema.exceptions.ValidationError):
        _validator_for(schema).validate(document)


@pytest.mark.parametrize(
    "status_array",
    ["pass", "must_fix", "should_fix", "not_verified", "not_applicable"],
)
def test_manifest_schema_accepts_summary_status_array_with_catalog_finding_id(
    status_array,
):
    document = _populated_manifest()
    document["summary"][status_array] = ["ACT-002"]
    _validate(document, REFERENCES / "governed-actions-manifest.schema.json")


@pytest.mark.parametrize(
    "collection,index,missing_key",
    [
        ("action_inventory", 0, "consequence"),
        ("mediation_paths", 0, "status"),
        ("mediation_paths", 0, "discovered"),
        ("mediation_paths", 0, "executed"),
        ("evidence", 0, "sha256"),
        ("findings", 0, "reason_code"),
    ],
)
def test_manifest_schema_rejects_nested_object_missing_required_property(
    collection, index, missing_key
):
    document = _populated_manifest()
    del document[collection][index][missing_key]
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    with pytest.raises(jsonschema.exceptions.ValidationError):
        _validator_for(schema).validate(document)


@pytest.mark.parametrize(
    "collection,index",
    [
        ("action_inventory", 0),
        ("mediation_paths", 0),
        ("evidence", 0),
        ("findings", 0),
    ],
)
def test_manifest_schema_rejects_nested_object_with_unknown_property(
    collection, index
):
    document = _populated_manifest()
    document[collection][index]["unexpected_extra_field"] = "nope"
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    with pytest.raises(jsonschema.exceptions.ValidationError):
        _validator_for(schema).validate(document)


def test_manifest_schema_rejects_unknown_root_property():
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    document = _minimal_manifest()
    document["unexpected_field"] = "nope"
    with pytest.raises(jsonschema.exceptions.ValidationError):
        _validator_for(schema).validate(document)


def test_manifest_schema_requires_all_listed_root_fields():
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(schema["required"]) == {
        "schema",
        "assessor",
        "phase",
        "captured_at",
        "source",
        "pins",
        "policy_hashes",
        "action_inventory",
        "mediation_paths",
        "conformance",
        "change_plane",
        "findings",
        "evidence",
        "freshness",
        "residual_risks",
        "summary",
    }


def test_manifest_schema_defines_required_defs():
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    for name in ("sha256", "status", "evidenceRef", "finding", "action", "path", "probe"):
        assert name in schema["$defs"], f"missing $defs.{name}"
    assert schema["$defs"]["sha256"]["pattern"] == "^sha256:[0-9a-f]{64}$"


@pytest.mark.parametrize(
    "filename",
    [
        "governed-actions-manifest.schema.json",
        "governed-actions-apply-plan.schema.json",
    ],
)
def test_timestamp_def_has_enforceable_rfc3339_pattern(filename):
    """The RFC 3339 constraint must be *enforceable* on its own — a regex
    "pattern" on the $def — rather than depending entirely on the
    validator having format assertions enabled, since "format" is only an
    annotation (a no-op) unless a FormatChecker is attached."""
    schema = json.loads((REFERENCES / filename).read_text(encoding="utf-8"))
    timestamp_def = schema["$defs"]["timestamp"]
    assert timestamp_def["format"] == "date-time"
    assert "pattern" in timestamp_def, (
        f"{filename}: $defs.timestamp must carry an enforceable 'pattern', "
        "not rely solely on the optional 'format' annotation"
    )


def test_installed_jsonschema_has_date_time_format_checker():
    """Documents (as a real, listed test — not just a module-level
    assertion at collection time) that this environment's ``jsonschema``
    actually enforces ``format: date-time``. If this ever regresses (e.g. a
    dependency change drops ``rfc3339-validator``), every
    ``_validator_for(...)`` format-assertion check in this module would
    silently become a no-op rather than failing loudly."""
    assert "date-time" in _FORMAT_CHECKER.checkers


@pytest.mark.parametrize(
    "bad_timestamp",
    [
        "2026-01-01",  # missing time-of-day
        "2026/01/01T00:00:00Z",  # wrong date separators
        "2026-01-01 00:00:00Z",  # space instead of 'T'
        "2026-01-01T00:00:00",  # missing UTC designator/offset
        "not-a-timestamp",
        "",
    ],
)
def test_manifest_schema_rejects_malformed_captured_at(bad_timestamp):
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    document = _minimal_manifest()
    document["captured_at"] = bad_timestamp
    with pytest.raises(jsonschema.exceptions.ValidationError):
        _validator_for(schema).validate(document)


@pytest.mark.parametrize(
    "good_timestamp",
    [
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00.123Z",
        "2026-01-01T00:00:00+02:00",
        "2026-01-01T00:00:00-05:30",
    ],
)
def test_manifest_schema_accepts_valid_rfc3339_captured_at(good_timestamp):
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    document = _minimal_manifest()
    document["captured_at"] = good_timestamp
    _validator_for(schema).validate(document)


def test_apply_plan_schema_rejects_malformed_captured_at():
    schema = json.loads(
        (REFERENCES / "governed-actions-apply-plan.schema.json").read_text(
            encoding="utf-8"
        )
    )
    document = _minimal_apply_plan()
    document["captured_at"] = "2026-01-01 00:00:00"
    with pytest.raises(jsonschema.exceptions.ValidationError):
        _validator_for(schema).validate(document)


def test_apply_plan_schema_is_draft_2020_12_and_accepts_minimal_document():
    _validate(
        _minimal_apply_plan(), REFERENCES / "governed-actions-apply-plan.schema.json"
    )


def test_apply_plan_schema_requires_all_listed_root_fields():
    schema = json.loads(
        (REFERENCES / "governed-actions-apply-plan.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(schema["required"]) == {
        "schema",
        "assessor_version",
        "source_commit",
        "captured_at",
        "manifest_sha256",
        "self_applying",
        "items",
    }


def test_apply_plan_schema_rejects_self_applying_true():
    schema = json.loads(
        (REFERENCES / "governed-actions-apply-plan.schema.json").read_text(
            encoding="utf-8"
        )
    )
    document = _minimal_apply_plan()
    document["self_applying"] = True
    with pytest.raises(jsonschema.exceptions.ValidationError):
        _validator_for(schema).validate(document)


def _validate_document(document, schema):
    _validator_for(schema).validate(document)


def test_apply_plan_schema_item_requires_all_listed_fields():
    schema = json.loads(
        (REFERENCES / "governed-actions-apply-plan.schema.json").read_text(
            encoding="utf-8"
        )
    )
    document = _minimal_apply_plan()
    document["items"] = [
        {
            "finding_id": "ACT-001",
            "status": "must-fix",
            "plane": "runtime",
            "affected_actions": ["send_email"],
            "affected_paths": [],
            "remediation_kind": "repo-edit",
            "evidence_required": [],
            "owner": None,
            "depends_on": [],
        }
    ]
    _validate_document(document, schema)

    item_def = schema["$defs"]["item"]
    assert set(item_def["required"]) == {
        "finding_id",
        "status",
        "plane",
        "affected_actions",
        "affected_paths",
        "remediation_kind",
        "evidence_required",
        "owner",
        "depends_on",
    }
    assert set(item_def["properties"]["remediation_kind"]["enum"]) == {
        "repo-edit",
        "sibling-skill",
        "manual",
        "deferred-to-pipeline",
    }
    assert item_def["additionalProperties"] is False


def test_no_schema_property_key_collides_with_the_runtime_banned_key_set():
    """Structural check, not a text substring scan: walk every schema node's
    actual ``properties`` keys (via :func:`_iter_schema_nodes`, reused below
    for the additionalProperties sweep) and assert none of them collides
    with :data:`canonical._BANNED_KEYS` — the exact same set
    ``validate_payload_free_audit`` enforces at runtime. Checking against
    the real runtime set (instead of a second hardcoded string list here)
    means this test cannot silently drift out of sync whenever the banned
    -key set changes."""
    for filename in (
        "governed-actions-manifest.schema.json",
        "governed-actions-apply-plan.schema.json",
    ):
        schema = json.loads((REFERENCES / filename).read_text(encoding="utf-8"))
        for node in _iter_schema_nodes(schema):
            properties = node.get("properties")
            if not isinstance(properties, dict):
                continue
            for key in properties:
                assert key.lower() not in canonical._BANNED_KEYS, (
                    f"{filename} declares banned property {key!r} "
                    f"(collides with canonical._BANNED_KEYS)"
                )


def _iter_schema_nodes(node):
    """Yield every dict/list node reachable from *node* (including itself),
    recursing into dict values and list items so every ``$defs`` entry and
    every nested ``properties``/``items`` sub-schema is visited, not just the
    document root."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_schema_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_schema_nodes(item)


@pytest.mark.parametrize(
    "filename",
    [
        "governed-actions-manifest.schema.json",
        "governed-actions-apply-plan.schema.json",
    ],
)
def test_every_object_typed_schema_node_forbids_additional_properties(filename):
    """Regression test for the whole schema tree, not only the root and the
    apply-plan's ``item`` $def: every sub-schema (in ``$defs`` or inline)
    whose ``type`` is ``"object"`` must declare
    ``"additionalProperties": false``, so no nested object anywhere in either
    schema can silently accept an unlisted (and potentially payload-bearing)
    property."""
    schema = json.loads((REFERENCES / filename).read_text(encoding="utf-8"))
    object_nodes = [
        node for node in _iter_schema_nodes(schema) if node.get("type") == "object"
    ]
    # Sanity check the walker itself is actually recursing into $defs (not
    # just inspecting the root): both schemas have at least one nested
    # object $def in addition to the root object itself, and the larger
    # manifest schema has many more.
    minimum_expected = 10 if "manifest" in filename else 2
    assert len(object_nodes) >= minimum_expected, (
        f"{filename}: expected at least {minimum_expected} object-typed nodes "
        f"across $defs, found {len(object_nodes)} — the schema walker may not "
        "be recursing"
    )
    for node in object_nodes:
        assert node.get("additionalProperties") is False, (
            f"{filename}: object-typed schema node is missing "
            f"'additionalProperties: false': {json.dumps(node, sort_keys=True)[:200]}"
        )


# ---------------------------------------------------------------------------
# finding catalog
# ---------------------------------------------------------------------------

EXPECTED_FINDING_IDS = {
    "ACT-001",
    "ACT-002",
    "MED-001",
    "MED-002",
    "MED-003",
    "ENF-001",
    "ENF-002",
    "APR-001",
    "OUT-001",
    "AUD-001",
    "PIN-001",
    "GHCP-001",
    "GHCP-002",
    "GHCP-003",
    "GHCP-004",
    "GHCP-005",
    "GHCP-006",
    "OPS-001",
}


def test_finding_catalog_has_exactly_18_ids_no_duplicates():
    catalog = json.loads(
        (REFERENCES / "finding-catalog.json").read_text(encoding="utf-8")
    )
    findings = catalog["findings"]
    ids = [entry["finding_id"] for entry in findings]
    assert len(ids) == 18
    assert len(set(ids)) == 18, "duplicate finding_id detected"
    assert set(ids) == EXPECTED_FINDING_IDS


def test_finding_catalog_entries_have_required_fields():
    catalog = json.loads(
        (REFERENCES / "finding-catalog.json").read_text(encoding="utf-8")
    )
    for entry in catalog["findings"]:
        assert entry["severity"] in ("must-fix", "should-fix")
        assert isinstance(entry["gate"], bool)
        assert entry["plane"] in ("runtime", "change", "both")
        assert entry["summary"]
        assert entry["remediation"]


def test_finding_catalog_gate_equals_severity_is_must_fix():
    """The catalog gate flag is a derived value, not an independently
    editable one: an entry blocks release (``gate: true``) if and only if
    its severity is ``must-fix``. Drifting these apart would let a
    should-fix finding silently start gating, or a must-fix finding
    silently stop gating."""
    catalog = json.loads(
        (REFERENCES / "finding-catalog.json").read_text(encoding="utf-8")
    )
    for entry in catalog["findings"]:
        assert entry["gate"] == (entry["severity"] == "must-fix"), entry["finding_id"]


def test_finding_catalog_ids_match_manifest_schema_finding_id_enum():
    catalog = json.loads(
        (REFERENCES / "finding-catalog.json").read_text(encoding="utf-8")
    )
    catalog_ids = {entry["finding_id"] for entry in catalog["findings"]}
    schema = _manifest_schema()
    schema_ids = set(schema["$defs"]["findingId"]["enum"])
    assert catalog_ids == EXPECTED_FINDING_IDS
    assert schema_ids == EXPECTED_FINDING_IDS


def test_finding_catalog_ids_match_apply_plan_schema_finding_id_enum():
    catalog = json.loads(
        (REFERENCES / "finding-catalog.json").read_text(encoding="utf-8")
    )
    catalog_ids = {entry["finding_id"] for entry in catalog["findings"]}
    schema = _apply_plan_schema()
    schema_ids = set(schema["$defs"]["findingId"]["enum"])
    assert catalog_ids == EXPECTED_FINDING_IDS
    assert schema_ids == EXPECTED_FINDING_IDS
