"""Offline documentation contracts, not runtime, deployment or attestation tests."""
import ast
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEEP = ROOT / "docs/agent-governance-deep-dive.md"
SPEC = ROOT / "docs/production-readiness-pages-spec.md"
CONTROL = ROOT / "skills/threadlight-govern/references/control-plane"
GATEWAY = ROOT / "skills/threadlight-govern/references/gateway"
DEPLOY = ROOT / "skills/threadlight-deploy/references/governance"


def read(path):
    assert path.is_file(), f"Missing documentation: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def plain(path):
    return " ".join(read(path).replace("**", "").split())


def require(text, markers):
    # Prose contracts should survive sentence capitalization; wire/schema checks
    # below deliberately remain case-sensitive and compare actual source fields.
    for marker in markers:
        assert marker.casefold() in text.casefold(), f"Missing contract: {marker}"


def fields(path, name):
    """Read actual schema field names without importing SDKs or starting services."""
    tree = ast.parse(read(path))
    model = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    return {node.target.id for node in model.body if isinstance(node, ast.AnnAssign)}


def example(path, name):
    match = re.search(
        rf"<!-- contract: {re.escape(name)} -->\s*```json\n(.*?)\n```", read(path), re.S
    )
    assert match, f"Missing named JSON example: {name}"
    return json.loads(match[1])


def test_deep_dive_defines_authority_and_trusted_computing_base():
    require(plain(DEEP), (
        "The model proposes; trusted components authorize effects.",
        "SAFE is the method", "ACS is the PDP", "Agent Hooks",
        "host/interceptor contract", "PEP", "AGT is the toolkit", "ASSERT is assurance",
        "chain-of-thought", "malicious trusted host code", "not all frameworks",
        "output guardrails", "network isolation", "trusted computing base",
        "Agent Identity", "human reviewer", "control-plane identity",
        "gateway identity", "downstream identity", "business writer", "publisher",
    ))


def test_deep_dive_links_actual_implementation_and_regression_sources():
    text = read(DEEP)
    paths = (
        DEPLOY / "generate.py", DEPLOY / "maf_gateway.py",
        DEPLOY / "maf-gateway-container.py", DEPLOY / "returns_mcp_backend.py",
        DEPLOY / "returns_reconcile.py", DEPLOY / "returns-mcp-demo.md",
        DEPLOY / "review-notification.bicep",
        GATEWAY / "dispatcher.py", GATEWAY / "receipts.py", GATEWAY / "server.py",
        CONTROL / "models.py", CONTROL / "app.py", CONTROL / "client.py",
        CONTROL / "review.py", CONTROL / "bootstrap.py", CONTROL / "hosted_lifecycle.py",
        ROOT / "examples/returns-triage-governed/src/agent/cosmos_effect.py",
        ROOT / "skills/threadlight-govern/tests/test_deferred_gateway_approval.py",
        ROOT / "skills/threadlight-deploy/tests/test_maf_gateway_client.py",
        ROOT / "skills/threadlight-deploy/tests/test_returns_read_audit.py",
        ROOT / "skills/threadlight-deploy/tests/test_returns_reconcile.py",
    )
    for path in paths:
        assert f"]({Path('..') / path.relative_to(ROOT)})" in text, path


def test_approval_and_gateway_diagrams_use_distinct_real_persisted_states():
    diagrams = re.findall(r"```mermaid\n(stateDiagram-v2.*?)\n```", read(DEEP), re.S)
    assert len(diagrams) == 2, "Document the two ledgers separately"
    expected = [
        ({"pending", "decided", "consumed"}, CONTROL / "app.py"),
        ({"awaiting_approval", "pending", "rejected", "completed"}, GATEWAY / "dispatcher.py"),
    ]
    for diagram, (states, source) in zip(diagrams, expected, strict=True):
        edges = re.findall(r"^\s*(\[\*\]|\w+)\s*-->\s*(\[\*\]|\w+)", diagram, re.M)
        observed = {state for pair in edges for state in pair} - {"[*]"}
        assert observed == states
        literals = {
            node.value for node in ast.walk(ast.parse(read(source)))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert states <= literals
    require(plain(DEEP), (
        "POST /approvals/resolve", "request", "resolve", "decide", "consume",
        "governance_operation_id", "not consent", "one-use", "CAS",
        "outcome_unknown is a reason code, not a stored state",
        "no automatic", "not exactly-once",
    ))


def test_sanitized_json_examples_follow_real_field_names():
    decision = example(DEEP, "returns-decision")
    assert set(decision) == fields(DEPLOY / "returns_mcp_backend.py", "Decision")
    assert decision["decision"] == "approve_refund"
    assert decision["expected_etag"].startswith('"') and decision["expected_etag"].endswith('"')
    request = example(DEEP, "approval-decision")
    assert set(request) == fields(CONTROL / "models.py", "DecideOperation")
    assert request["operation"] == "decide" and request["approved"] is True
    assert request["approving_role"] == "Approver"
    assert set(request["intent"]) == (
        fields(CONTROL / "models.py", "ApprovalRequest")
        | fields(CONTROL / "models.py", "ApprovalContext")
    )
    require(plain(DEEP), (
        "placeholders are not executable authority", "additionalProperties",
        "expected_etag", "if_match_etag", "X-Governance-Provenance",
        "config_digest", "native_policy_digest", "RS256", "RSA-3072",
        "no exported private key", "no demo signer",
    ))


def test_transport_and_telemetry_contract_is_not_a_blanket_header_exception():
    require(plain(DEEP), (
        "AuthorizedTransport.same_headers", "traceparent", "tracestate", "baggage",
        "TraceContextTextMapPropagator", "W3CBaggagePropagator",
        "exact active", "duplicate", "Authorization", "Idempotency-Key",
        "http11.send_request_headers.started", "http11.send_request_body.started",
        "credential", "retry", "pool", "TLS", "time.monotonic",
        "no SDK patches", "telemetry remains enabled",
    ))


def test_read_ack_native_traces_and_post_run_reconciliation_are_not_conflated():
    require(plain(DEEP), (
        "read_audit_container", "read_audit_id", "append_audit",
        "backend-acknowledged-before-return", "post-run-not-attestation",
        "case-read", "native-tool-call", "arguments_digest", "output_digest",
        "result_digest", "runner-activity", "owner", "/scope", "/case_id",
        "create-only", "no-overwrite", "14 tool calls", "8 native responses",
        "two-tool inventory", "unbound read", "without ACS",
        "not continuous attestation", "central audit ACK before",
    ))


def test_dated_matrix_keeps_scenarios_and_live_claims_separate():
    text = plain(DEEP)
    require(text, (
        "2026-09-14", "2026-09-13", "S1", "S2", "S3",
        "S1-MODEL-ALLOW", "S1-MCP-DENY", "S1-HUMAN-RESUME", "S1-COMPLETED-REPLAY",
        "not hosted", "pre-session", "project managed identity", "Login", "Pull",
        "per-caller blob completion", "unpacking", "snapshot", "root cause",
        "S3-HOSTED-ALLOW", "S3-HOSTED-DENY", "S3-PENDING-APPROVAL",
        "S3-EXPIRED-RESUME", "Error: Function failed.",
        "causal expiry guard was not independently isolated",
        "parent-owned", "governed-returns-validation.md",
    ))
    # These are already-public artifact hashes, never new environment identity claims.
    scenario = read(ROOT / "docs/governed-returns-validation.md")
    for digest in (
        "3e46f91052d3f22bcaa1897c394075caed767f54585be57725fa93b13096c69d",
        "c44746f2cafb96065cd9f18727e51f9774e469860c61bf08f196bc9359dafafb",
        "967ca2b3103d84980a08a1ec319b2549457daab08392adb1c98eb7943a04215a",
    ):
        assert digest in text and digest in scenario


@pytest.mark.parametrize("document", [DEEP, SPEC], ids=["deep-dive", "pages-spec"])
def test_september14_diagnostic_is_recorded_not_pending_or_authority_to_retry(document):
    text = plain(document)
    require(text, (
        "governed-returns-validation.md#september-14-one-unchanged-private-transient-control-retry",
        "ProvisioningError", "no native sessions", "no overnight recovery",
        "No second attempt", "new observed difference or justified correction",
    ))
    assert "TBD" not in text, "The September 14 diagnostic now has an observed result"


@pytest.mark.parametrize("document", [DEEP, SPEC], ids=["deep-dive", "pages-spec"])
def test_later_egress_authorization_is_separate_from_the_morning_control(document):
    require(plain(document), (
        "10:55", "version 5", "no customer network change",
        "not an A/B relaxation test", "shared NAT",
        "not hosted Internet egress proof",
        "governed-returns-validation.md#september-14-scoped-egress-inspection-with-no-network-change",
        "No second attempt was made under that earlier authorization",
    ))


@pytest.mark.parametrize("document", [DEEP, SPEC], ids=["deep-dive", "pages-spec"])
def test_private_basic_v7_is_model_proof_not_private_business_authority(document):
    require(plain(document), (
        "version 6", "version 7", "Billing Issue", "ContainerRegistry",
        "ManagedIdentity", "BASIC is not private governed business proof",
        "not a format-only causal proof", "mutable",
        "governed-returns-validation.md#september-14-private-basic-model-smoke-after-registry-binding-and-image-comparison",
    ))


@pytest.mark.parametrize("document", [DEEP, SPEC], ids=["deep-dive", "pages-spec"])
def test_private_governed_allow_deny_are_scoped_and_do_not_claim_human_completion(document):
    require(plain(document), (
        "private governed", "version 4", "allow and exact deny",
        "four calls", "two responses", "user was unavailable",
        "no new private pending intent", "not fully governed in every respect",
        "governed-returns-validation.md#september-14-private-governed-allow-and-exact-deny-with-fresh-authority",
    ))


def test_evidence_expiry_human_and_reproduction_limits_are_explicit():
    require(plain(DEEP), (
        "2026-09-14T10:26:48", "no automatic renewal",
        "resource retention", "authority expiry",
        "HUMAN APPROVED-RESUME", "positive replay", "EMAIL", "not proved",
        "Office 365", "Unauthenticated", "Disabled", "S1's grant",
        "OBO A-to-B", "per-user native MCP authentication",
        "SecurityControl=Ignore", "not a production compliance",
        "operator-owned", "not single-command", "no financial settlement",
        "offline", "LOCAL-14", "restoration", "reproducibility",
        "47 declared", "four undeclared", "governance_probe_noop",
    ))


@pytest.mark.parametrize("document", [DEEP, SPEC], ids=["deep-dive", "pages-spec"])
def test_distinct_signed_leases_require_fresh_checks_even_before_expiry(document):
    require(plain(document), (
        "2026-09-14T10:26:48.991423+00:00", "12:26 Italy",
        "2026-09-14T12:03:41.859581+00:00", "14:03 Italy",
        "even before either expiry", "fresh checks",
        "Historical successful receipts do not imply current executable authorization",
        "Neither expiry nor resource retention renews a grant or signed authority",
    ))


def test_pages_spec_is_proposed_and_preserves_actual_entrypoints():
    require(plain(SPEC), (
        "SPECIFICATION ONLY", "not deployed", "prototype path remains unchanged",
        "explicit opt-in", "before the effect boundary", "network isolation",
        "docs/index.html", "docs/funnel.html", "docs/production.html",
        "docs/production-readiness.md", "docs/_config.yml", "docs/assets/site.js",
        ".github/workflows/docs-blueprint.yml", ".github/workflows/pages-cache-bust.yml",
        "docs/ci/sync_cache_bust.py", "no full site rebuild",
        "not a site generator", "data-toc-id", "aria-current",
    ))
    # Existing route fragments must actually exist; proposed additions are separate.
    for filename, fragment in (
        ("index.html", "how-it-works"), ("funnel.html", "scene-funnel"),
        ("production.html", "checks"), ("production.html", "proof"),
        ("production.html", "ship"), ("production.html", "start"),
    ):
        assert f"{filename}#{fragment}" in read(SPEC)
        assert f'id="{fragment}"' in read(ROOT / "docs" / filename)


def test_pages_spec_keeps_generator_owned_artifact_names():
    tree = ast.parse(read(ROOT / "scripts/build_process_library.py"))
    assignment = next(
        node for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SKILL_ARTIFACTS" for target in node.targets)
    )
    artifacts = ast.literal_eval(assignment.value)
    text = plain(SPEC)
    for skill in (
        "threadlight-govern", "threadlight-production-ready", "threadlight-cicd",
        "threadlight-evals", "threadlight-redteam", "threadlight-consumption-iq",
    ):
        require(text, (skill, *artifacts[skill]))
    require(text, (
        "docs/assets/process-library.json", "scripts/build_process_library.py",
        "docs/assets/blueprint-logic.js", "run_skills", "generated-by-threadlight-design",
        "tests/governed-actions-manifest.json", ".threadlight/governance-live.json",
    ))


def test_pages_spec_supplies_copy_proof_labels_and_falsifiable_acceptance():
    require(plain(SPEC), (
        "Exact proposed copy", "Acceptance criteria",
        "The model proposes; trusted components authorize effects.",
        "enforced", "observed", "unbound", "unverified", "unsupported", "bypassable",
        "not-verified", "must-fix", "waived", "raw_score", "score_with_waivers",
        "would_fail_hard_gate", "verification_coverage", "captured_at",
        "LIVE-SCOPED", "LOCAL", "OFFLINE", "PROPOSED", "NOT PROVED",
        "agent-governance-deep-dive.md", "governed-returns-validation.md",
        "Office 365", "consent", "privacy", "two tools", "not certification",
        "tests/blueprint/public-links.test.js",
        "tests/blueprint/process-library-generator.test.js",
        "tests/playwright/tests/site.spec.mjs",
    ))


def test_pages_spec_replaces_specific_signoff_speed_score_and_governance_claims():
    require(plain(SPEC), (
        "The scorecard that signs.", "Every gap has a skill that closes it.",
        "ready in ~7m", "Foundry runs & governs it.", "92–100", "92/100",
        "ship with 2 waivers", "ship with two waivers",
        "Evidence for the people who sign.",
        "Every gap needs an owner and verified closure.",
        "Report generation time varies; completion is not readiness.",
        "Illustrative scorecard — review findings, coverage and dates.",
        "Illustrative review scenario — no go-live approval evidenced here.",
        "Historical scorecard example — not a promised score range.",
        "timestamped report-generation capture", "authenticated human decision record",
        "current scoped evidence", "all repeated captions",
    ))


@pytest.mark.parametrize("document", [DEEP, SPEC], ids=["deep-dive", "pages-spec"])
def test_document_relative_links_and_fragments_resolve(document):
    text = read(document)
    targets = re.findall(r"\]\(([^)\s]+)\)", text)
    assert targets, "Documentation must link its evidence"
    for target in targets:
        parsed = urlsplit(target)
        if parsed.scheme:
            assert parsed.scheme == "https", target
            continue
        assert not parsed.netloc and not parsed.path.startswith("/"), target
        resolved = (document.parent / unquote(parsed.path)).resolve() if parsed.path else document
        assert resolved.is_relative_to(ROOT) and resolved.exists(), target
        if not parsed.fragment:
            continue
        body = read(resolved)
        if resolved.suffix == ".html":
            assert re.search(rf"""\bid=["']{re.escape(parsed.fragment)}["']""", body), target
        elif resolved.suffix == ".md":
            headings = re.findall(r"^#{1,6} (.+)$", body, re.M)
            slugs = {re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-") for heading in headings}
            assert unquote(parsed.fragment) in slugs, target
        else:
            pytest.fail(f"Unvalidated source fragment: {target}")


@pytest.mark.parametrize("document", [DEEP, SPEC], ids=["deep-dive", "pages-spec"])
def test_document_uses_sanitized_examples_not_private_operational_data(document):
    text = read(document)
    assert "/Users/" not in text
    assert not re.search(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", text, re.I)
    assert not re.search(
        r"https://[a-z0-9-]+\.(?:azurecr\.io|openai\.azure\.com|vault\.azure\.net|"
        r"documents\.azure\.com|blob\.core\.windows\.net|azurecontainerapps\.io)", text, re.I
    )
    assert not re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    require(plain(document), ("no private",))
