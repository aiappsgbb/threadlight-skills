#!/usr/bin/env python3
"""connect.py — the CONNECT leg: swap a mocked Foundry tool for a real one.

`threadlight-design` / `threadlight-demo-data-factory` scaffold pilots against
**mocked** tools (a JSON sample-data file standing in for a real backend, per
`AGENTS.md` / `specs/SPEC.md` § "Foundry tools required"). Sooner or later a
real endpoint replaces that mock. This is the evidence-based leg that makes
that swap safe: it never trusts an endpoint just because it *responds* — it
extracts the data contract the tool source actually reads, generates
executable conformance tests, checks field-level conformance against a real
sample, and only calls the swap `real-verified` once conformance passes *and*
OBO (on-behalf-of) user-scoped evidence *and* required-role revalidation are
both in hand.

Customer-specific field **mapping** (e.g. renaming `cust_id` → `customer_id`)
is explicitly out of scope — this leg proves *conformance*, it does not
transform payloads. OBO token exchange itself is scaffolding only; the actual
Entra Agent Identity / OAuth-on-behalf-of implementation is owned by the
upstream `entra-agent-id` catalog skill (see SKILL.md "See also").

Phases (also the CLI's conceptual pipeline, run in one invocation):

    inspect          -> read tool source + mock sample
    contract         -> extract_contract(): fields actually READ, never sample keys
    generate-tests   -> write_conformance_tests(): executable pytest module
    verify           -> check_conformance(): field-level diff against a real sample
    plan             -> build_apply_plan(): file-by-file plan (always built, no I/O)
    apply            -> _commit_verified_apply(): one rollback-aware transaction
                         over SPEC.md + mcp-config.json + connect-manifest.json
                         (only when --apply AND target_state == real-verified)
    emit             -> write_connect_manifest(): schema-validated, atomic
                         (only on the non-apply / unverified path; on a verified
                         apply the manifest is committed inside the transaction)

State machine — exactly four states: `mock`, `real-unverified`,
`real-verified`, `real-drift`. `integration_state` is the *persisted* current
state (read from a prior manifest; defaults to `mock`) and only ever advances
on a *successful* `--apply`. `target_state` is what `transition_integration()`
computes purely from evidence, independent of whether `--apply` was passed —
so a dry run (`--apply` omitted) can report `target_state: real-verified`
while `integration_state` stays `mock`, with a nonempty apply plan describing
exactly what publishing would change.

Publishing (or re-publishing) a `real-verified` swap requires required-role
revalidation against the **current** agent identity, not a stale grant — see
`transition_integration(..., current_agent_identity=...)`.

stdlib-only. No network calls; no endpoint is ever invoked by this script —
callers supply the "real" sample/response evidence themselves (e.g. captured
via `entra-agent-id` + a manual test call). Malformed evidence never writes
anything, so a prior valid `connect-manifest.json` / SPEC.md / mcp-config.json
is preserved untouched. On a verified `--apply` the FINAL manifest is built and
fully schema-validated *before* any file is mutated, then SPEC.md, the MCP
config, and the manifest are committed as one transaction: an in-process
failure after any of the three `os.replace` calls rolls every already-applied
file back to its captured prior bytes/mode/existence, so the trio never
diverges. The one thing this cannot defend against is a hard crash / power loss
between two individually-atomic replaces (see `_commit_transaction`); if the
compensating rollback itself fails, a `ConnectInconsistentStateError` names the
unreconciled path(s) rather than claiming success.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

# ---------------------------------------------------------------------------
# Shared envelope (skills/_shared/manifest.py) — insert repo root on sys.path
# so `skills._shared.manifest` resolves as an implicit namespace package both
# in-repo and when this script is invoked standalone.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills._shared.manifest import (  # noqa: E402
    ManifestValidationError,
    _validate_iso8601_timestamp,
    atomic_write_json,
    build_envelope,
    validate_envelope,
)

TOOL_VERSION = "0.1.0"
DATA_CONTRACT_SCHEMA = "threadlight-connect-data-contract/v1"
CONNECT_MANIFEST_SCHEMA = "threadlight-connect-manifest/v1"

VALID_STATES = ("mock", "real-unverified", "real-verified", "real-drift")

DEFAULT_SPEC_PATH = "specs/SPEC.md"
DEFAULT_MCP_CONFIG_PATH = "infra/mcp-config.json"
DEFAULT_MANIFEST_PATH = "specs/connect-manifest.json"

_MISSING = object()

_FORBIDDEN_KEY_MARKERS = (
    "token", "secret", "password", "credential", "authorization",
    "api_key", "apikey", "connection_string", "access_key",
)

# Conservative mock-endpoint marker — deliberately IDENTICAL to
# threadlight-safe-check's `MOCK_ENDPOINT_MARKER` (see that skill's safe_check.py
# G9.7). It matches `mock` only as a delimited token (`mock`, `mocked`,
# `mockserver`, or `mock` bounded by non-alphanumerics such as `mock.example` /
# `erp-mock` / `local mock`), NEVER as a substring of an unrelated word like
# `mockingbird` or `smock`. Duplicated here (not cross-imported) to avoid
# coupling two independently-shipped skills; a parity test pins the two patterns
# to identical behaviour on the mock/mocked/mockserver/mockingbird corpus.
MOCK_ENDPOINT_MARKER = re.compile(
    r"(?<![A-Za-z0-9])(?:mockserver|mocked|mock)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Query-parameter names that would smuggle a credential / SAS / token into a
# real endpoint URL. Names are compared after lowercasing and removing `-` / `_`
# separators, so spelling variants such as `subscription-key`,
# `subscription_key`, and `SubscriptionKey` have identical semantics.
_SECRET_QUERY_PARAM_NAMES = frozenset({
    "sig", "sas", "sharedaccesssignature", "signature", "token", "accesstoken",
    "idtoken", "refreshtoken", "apikey", "key", "code", "secret",
    "clientsecret", "password", "pwd", "credential", "authorization", "auth",
    "accountkey", "xfunctionskey", "awsaccesskeyid", "subscriptionkey",
    "accesskey", "sharedaccesskey",
})
_FORBIDDEN_QUERY_NAME_MARKERS = tuple(
    marker.replace("-", "").replace("_", "") for marker in _FORBIDDEN_KEY_MARKERS
)

# Kept byte-for-behaviour equivalent to safe-check's canonical
# `_endpoint_is_provably_mock` field corpus.
_SAFE_CHECK_ENDPOINT_FIELDS = ("url", "host", "name", "endpoint")

# Server-entry fields that describe a *mock* transport and are mutually
# exclusive with a real HTTPS `url` binding. Removed from a preserved server
# entry ONLY when present, so unrelated fields (`type`, `headers`, custom keys)
# survive the swap untouched. A key that carries the delimited mock marker
# (e.g. `mock_url`) is stripped too (see `_update_mcp_config`).
_MOCK_TRANSPORT_FIELDS = ("command", "args")


class ConnectEvidenceError(ValueError):
    """Raised when OBO / required-role evidence is not the expected shape.

    Deliberately distinct from "evidence is honestly absent/false" (which is
    a normal `real-unverified` finding, not an error) — this is for evidence
    that is structurally malformed and cannot be evaluated at all. Raising
    here happens *before* any file is touched, so a malformed-evidence run
    never disturbs a prior valid manifest/SPEC/mcp-config.
    """


class ConnectApplyError(RuntimeError):
    """Raised when committing the SPEC.md + mcp-config.json + connect-manifest.json
    swap fails.

    `_commit_transaction()` stages a temp file for every destination before
    replacing any, and captures each destination's prior existence, bytes, and
    mode up front. If a replace fails *after* an earlier one already succeeded,
    every already-replaced destination is rolled back to its captured prior bytes
    (or removed if it did not previously exist) before this is raised — so a
    recoverable failure leaves the prior SPEC.md / mcp-config.json / manifest on
    disk byte-for-byte, never a half-applied swap.

    Honest limitation: each `os.replace` is individually atomic, but the set of
    replaces is not a single filesystem transaction. This defends against
    in-process failures (an `os.replace` error, an injected fault); it cannot
    defend against a hard crash / power loss / SIGKILL in the narrow window
    *between* two replaces. If the compensating rollback itself fails, the
    stronger `ConnectInconsistentStateError` is raised instead and success is
    never reported.
    """


class ConnectInconsistentStateError(ConnectApplyError):
    """Raised when an apply failed AND the compensating rollback also failed,
    leaving SPEC.md / mcp-config.json / connect-manifest.json possibly divergent
    on disk.

    The message names the exact destination path(s) that could not be
    restored. It subclasses `ConnectApplyError` so existing handlers still
    catch it, but it deliberately does NOT claim "no files were changed": the
    caller must treat the identified paths as being in an unknown, possibly
    partially-applied state and reconcile them by hand.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Real-endpoint contract — validate the operator-supplied endpoint BEFORE any
# write, and only ever persist it in the MCP config (never in the connect
# manifest / evidence). A malformed / mock / credential-bearing endpoint raises
# ConnectEvidenceError with nothing written, so INT-002 can never pass on a bad
# binding.
# ---------------------------------------------------------------------------
_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _endpoint_is_mock(endpoint) -> bool:
    """True when a string carries the conservative, delimited mock marker.

    Mirrors threadlight-safe-check's provably-mock predicate exactly: `mock`,
    `mocked`, `mockserver`, and delimited `mock` count; substrings such as
    `mockingbird` / `smock` never do. Non-strings are never mock.
    """
    return isinstance(endpoint, str) and bool(MOCK_ENDPOINT_MARKER.search(endpoint))


def _server_is_provably_mock(server) -> bool:
    """Match safe-check's canonical mock predicate over all endpoint fields."""
    if not isinstance(server, dict):
        return False
    for key in _SAFE_CHECK_ENDPOINT_FIELDS:
        value = server.get(key)
        if isinstance(value, str) and MOCK_ENDPOINT_MARKER.search(value):
            return True
    return False


def _normalize_query_param_name(raw_name: str) -> str:
    return re.sub(r"[-_]+", "", unquote(raw_name).strip().lower())


def _reject_secret_query(query: str) -> None:
    """Reject a URL query that carries any credential / SAS / token parameter.

    Matches a param name that is a known secret name OR contains a forbidden
    marker substring. A SAS URL always includes `sig=`, so this catches it
    without over-flagging generic short params.
    """
    if not query:
        return
    for pair in query.split("&"):
        if not pair:
            continue
        raw_name = pair.split("=", 1)[0]
        name = _normalize_query_param_name(raw_name)
        if not name:
            continue
        if name in _SECRET_QUERY_PARAM_NAMES or any(
            marker in name for marker in _FORBIDDEN_QUERY_NAME_MARKERS
        ):
            raise ConnectEvidenceError(
                "real_endpoint must not carry secret/SAS/token query parameters "
                f"(offending parameter {name!r})"
            )


def _validate_real_endpoint(endpoint):
    """Validate an operator-supplied real endpoint and return it unchanged.

    Enforced, all BEFORE any file is touched (raising ConnectEvidenceError):
      * a non-empty string with no whitespace / control characters;
      * an `https` URL (or `http` ONLY for localhost / 127.0.0.1 / ::1) with a
        hostname;
      * no embedded userinfo (username / password / `user:pass@host`);
      * no URL fragment;
      * no credential / SAS / token query parameter;
      * not the scaffolded mock (the same conservative marker safe-check uses).

    The returned value is the exact validated ("normalized") string — the apply
    transaction persists it verbatim into the MCP config and later re-reads it
    for an exact-equality postcondition.
    """
    if not isinstance(endpoint, str) or not endpoint:
        raise ConnectEvidenceError("real_endpoint must be a non-empty string")
    for ch in endpoint:
        if ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise ConnectEvidenceError(
                "real_endpoint must not contain whitespace or control characters"
            )
    try:
        parts = urlsplit(endpoint)
        scheme = (parts.scheme or "").lower()
        hostname = parts.hostname
        username = parts.username
        password = parts.password
        _ = parts.port  # forces port validation (raises ValueError if malformed)
    except ValueError as exc:
        raise ConnectEvidenceError(f"real_endpoint is not a valid URL: {exc}") from exc
    if not hostname:
        raise ConnectEvidenceError("real_endpoint must include a hostname")
    if scheme == "https":
        pass
    elif scheme == "http":
        if hostname.lower() not in _LOCAL_HTTP_HOSTS:
            raise ConnectEvidenceError(
                "real_endpoint must use https (http is allowed only for "
                "localhost / 127.0.0.1)"
            )
    else:
        raise ConnectEvidenceError("real_endpoint must use the https scheme")
    if username or password or "@" in parts.netloc:
        raise ConnectEvidenceError(
            "real_endpoint must not embed userinfo/credentials (user:pass@host)"
        )
    if parts.fragment:
        raise ConnectEvidenceError("real_endpoint must not contain a URL fragment")
    _reject_secret_query(parts.query)
    if _endpoint_is_mock(endpoint):
        raise ConnectEvidenceError(
            "real_endpoint must be a real endpoint, not the scaffolded mock"
        )
    return endpoint


def _effective_mcp_endpoint(mcp_data, tool_name: str):
    """Extract the effective bound endpoint for *tool_name* from an MCP config:
    ``servers[tool_name]["url"]`` when present and well-shaped, else ``None``.
    Total (never raises) so it is safe in both the predicted-binding pre-check
    and the post-apply re-read.
    """
    if not isinstance(mcp_data, dict):
        return None
    servers = mcp_data.get("servers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get(tool_name)
    if not isinstance(entry, dict):
        return None
    url = entry.get("url")
    return url if isinstance(url, str) else None


def _effective_mcp_server(mcp_data, tool_name: str):
    """Return a well-shaped server entry, or ``None`` for malformed input."""
    if not isinstance(mcp_data, dict):
        return None
    servers = mcp_data.get("servers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get(tool_name)
    return entry if isinstance(entry, dict) else None


def _safe_identifier(name: str) -> str:
    ident = re.sub(r"\W", "_", name).strip("_") or "tool"
    if ident[0].isdigit():
        ident = f"tool_{ident}"
    return ident


def _docstring_safe(text) -> str:
    """Render an arbitrary string safe to embed inside a generated triple-quoted
    docstring. Collapses all whitespace/newlines to single spaces and
    neutralizes backslashes and double quotes so a hostile value (triple
    quotes, embedded newline, trailing code) can never terminate the generated
    module's docstring and spill into executable module code. Purely for human
    display; the machine-read literals in the generated module always go
    through `repr()`, never this.
    """
    collapsed = " ".join(str(text).split())
    return collapsed.replace("\\", "_").replace('"', "'")


# ---------------------------------------------------------------------------
# Type / cardinality inference (shared by extraction, conformance checking,
# and the generated conformance test module — kept intentionally tiny and
# duplicated in the generated source so the generated test has zero runtime
# dependency on this package).
# ---------------------------------------------------------------------------
def _infer_type(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return None


def _infer_cardinality(value):
    if value is None:
        return None
    if isinstance(value, list):
        return "array"
    return "single"


def _type_compatible(expected_type, actual_value) -> bool:
    """Whether an observed value satisfies an expected contract type, allowing
    ONLY lossless numeric widening:

    - an integer actual satisfies an expected ``number`` (int is a valid
      number);
    - a float actual satisfies an expected ``integer`` only when it is
      mathematically integral (``1.0`` conforms; ``1.5`` still drifts —
      accepting it would lose information);
    - every other type must match exactly.

    Booleans are never numeric here (``_infer_type`` maps ``bool`` to
    ``boolean``), so a bool never widens into ``integer``/``number``.
    """
    actual_type = _infer_type(actual_value) or "unknown"
    if actual_type == expected_type:
        return True
    if expected_type == "number" and actual_type == "integer":
        return True
    if expected_type == "integer" and actual_type == "number":
        return float(actual_value).is_integer()
    return False


# ---------------------------------------------------------------------------
# Phase: inspect + contract — extract fields actually READ by the tool source
# ---------------------------------------------------------------------------
def _parse_tool_module(tool_source: str) -> ast.Module:
    """Parse a tool source snippet. Accepts a full function def OR a bare
    statement/expression (e.g. just the `return {...}` line) by wrapping the
    latter in a dummy function body so `ast.parse` succeeds either way.
    """
    try:
        return ast.parse(tool_source)
    except SyntaxError:
        indented = "\n".join(
            ("    " + line if line.strip() else line)
            for line in tool_source.splitlines()
        )
        wrapped = "def _threadlight_connect_tool():\n" + indented + "\n"
        return ast.parse(wrapped)


def _subscript_key(node: ast.Subscript):
    slice_node = node.slice
    index_type = getattr(ast, "Index", None)  # removed in newer ast; guard
    if index_type is not None and isinstance(slice_node, index_type):
        slice_node = slice_node.value
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        return slice_node.value
    return None


def _extract_read_fields(tool_source: str) -> dict:
    """Return {field_name: required} for every field the tool source actually
    reads off *some* source object — `obj['field']` (required: raises if
    absent) or `obj.get('field', ...)` (optional). Any name never read (e.g.
    an `internal` column present only in the sample) is excluded entirely —
    this is what keeps the contract from leaking un-read sample fields.
    A field read via subscript anywhere wins over an optional `.get()` read
    elsewhere (required is the stronger claim).
    """
    tree = _parse_tool_module(tool_source)
    required_fields: set[str] = set()
    optional_fields: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            key = _subscript_key(node)
            if key is not None:
                required_fields.add(key)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            optional_fields.add(node.args[0].value)

    fields = {name: True for name in required_fields}
    for name in optional_fields:
        fields.setdefault(name, False)
    return fields


def _representative_record(sample):
    if isinstance(sample, list):
        return sample[0] if sample else {}
    if isinstance(sample, dict):
        return sample
    return {}


def extract_contract(tool_name: str, tool_source: str, sample, generated_at=None) -> dict:
    """Build a data contract of ONLY the fields the tool source reads.

    Customer-specific field *mapping* stays out of scope: this records the
    source field names as read, their inferred type/cardinality (from the
    sample, when evidence exists — otherwise left `None`), and requiredness.
    """
    read_fields = _extract_read_fields(tool_source)
    record = _representative_record(sample)

    fields = []
    for name in sorted(read_fields):
        required = read_fields[name]
        value = record.get(name, _MISSING) if isinstance(record, dict) else _MISSING
        if value is _MISSING:
            field_type, cardinality = None, None
        else:
            field_type = _infer_type(value)
            cardinality = _infer_cardinality(value)
        fields.append(
            {
                "name": name,
                "required": required,
                "type": field_type,
                "cardinality": cardinality,
            }
        )

    return {
        "schema": DATA_CONTRACT_SCHEMA,
        "tool_name": tool_name,
        "generated_at": generated_at or _now_iso(),
        "fields": fields,
    }


# ---------------------------------------------------------------------------
# Phase: generate-tests — an executable, dependency-free pytest module
# ---------------------------------------------------------------------------
def generate_conformance_test_source(tool_name: str, contract: dict, default_sample_path=None) -> str:
    default_sample_path = default_sample_path or f"tests/fixtures/{tool_name}-real-sample.json"
    ident = _safe_identifier(tool_name)
    # Display-only, injection-proof renderings for the generated docstring. The
    # machine-read literals below always go through repr()/safe identifiers,
    # never raw interpolation — so a hostile tool_name (triple quotes, newline,
    # code) can never break out of the docstring into executable module code.
    tool_name_display = _docstring_safe(tool_name)
    default_sample_path_display = _docstring_safe(default_sample_path)
    fields_literal = repr(
        [
            {"name": f["name"], "required": f["required"], "type": f["type"]}
            for f in contract["fields"]
        ]
    )
    return f'''"""Auto-generated by threadlight-connect scripts/connect.py.

DO NOT EDIT BY HAND — regenerate via the connect leg's generate-tests phase.
Executable pytest module: asserts a captured real "{tool_name_display}" tool
response conforms to the extracted data contract (fields, types,
requiredness). Reads the real response from CONNECT_REAL_SAMPLE_PATH
(default: {default_sample_path_display}).
No network call is made here — this only checks evidence already captured.
"""
import json
import os

CONTRACT_FIELDS = {fields_literal}
REAL_SAMPLE_PATH = os.environ.get(
    "CONNECT_REAL_SAMPLE_PATH", {default_sample_path!r}
)


def _infer_type(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return None


def _type_compatible(expected_type, actual_value):
    # Lossless numeric widening only: an integer satisfies an expected number;
    # an integral float (1.0) satisfies an expected integer; a non-integral
    # float (1.5) still drifts. bool is never numeric here.
    actual_type = _infer_type(actual_value) or "unknown"
    if actual_type == expected_type:
        return True
    if expected_type == "number" and actual_type == "integer":
        return True
    if expected_type == "integer" and actual_type == "number":
        return float(actual_value).is_integer()
    return False


def _extract_items(real_response):
    # Mirror the runtime extractor exactly: accept the wrapped
    # {{"items": [...]}} response OR a bare list of records; anything else has
    # no records to check.
    if isinstance(real_response, dict):
        items = real_response.get("items", [])
    elif isinstance(real_response, list):
        items = real_response
    else:
        items = []
    if not isinstance(items, list):
        items = []
    return items


def check_conformance(real_response):
    items = _extract_items(real_response)
    differences = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            # A non-object record cannot satisfy any field contract — not even
            # an optional-only one. One item-level difference, then move on.
            actual_type = _infer_type(item) or "unknown"
            differences.append(
                {{"field": "$", "expected": "object", "actual": actual_type, "path": "$.items[{{}}]".format(index)}}
            )
            continue
        for field in CONTRACT_FIELDS:
            name = field["name"]
            required = field["required"]
            expected_type = field.get("type") or "unknown"
            expected = "{{}}|{{}}".format(expected_type, "required" if required else "optional")
            path = "$.items[{{}}].{{}}".format(index, name)
            if name not in item:
                if required:
                    differences.append(
                        {{"field": name, "expected": expected, "actual": "missing", "path": path}}
                    )
                continue
            if field.get("type") is not None and not _type_compatible(field["type"], item[name]):
                actual_type = _infer_type(item[name]) or "unknown"
                differences.append(
                    {{"field": name, "expected": expected, "actual": actual_type, "path": path}}
                )
    return differences


def test_{ident}_conformance():
    with open(REAL_SAMPLE_PATH, "r", encoding="utf-8") as fh:
        real_response = json.load(fh)
    items = _extract_items(real_response)
    assert items, (
        "no real items captured in " + str(REAL_SAMPLE_PATH) + " — conformance "
        "cannot be verified against an empty sample (insufficient evidence)"
    )
    differences = check_conformance(real_response)
    assert not differences, "mock-to-real conformance differences: {{}}".format(differences)
'''


def _default_new_file_mode() -> int:
    """A predictable, non-executable permission mode for a *newly created*
    file, honoring the process umask (``0o666 & ~umask`` — ``0o644`` under the
    usual ``022``). The base is ``0o666`` so the result is never executable.
    """
    current_umask = os.umask(0)
    os.umask(current_umask)
    return 0o666 & ~current_umask


def _mode_for_destination(path: Path) -> int:
    """Permission bits to stamp on a freshly staged temp *before* it replaces
    ``path``: an existing destination keeps its EXACT current mode; a new file
    gets the predictable non-executable default. Applying the mode on the
    forward write (not only on rollback) keeps a restrictive prior file
    restrictive after the swap, and a world-readable one world-readable —
    ``os.replace`` otherwise leaks the temp's private ``0o600``.
    """
    if path.exists():
        return path.stat().st_mode & 0o7777
    return _default_new_file_mode()


def _write_temp_file(path: Path, content: str, mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_path, _default_new_file_mode() if mode is None else mode)
    return temp_path


def _write_temp_bytes(path: Path, content: bytes, mode: int | None = None) -> Path:
    """Stage exact bytes in the destination's own directory (so a follow-up
    `os.replace` is atomic). Used by rollback to restore prior file contents
    verbatim. The target mode is stamped on the temp before it is returned, so
    the replaced file carries the intended permissions with no window at the
    temp's private default.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_path, _default_new_file_mode() if mode is None else mode)
    return temp_path


def _atomic_write_text(path: Path, content: str) -> None:
    temp_path = None
    try:
        temp_path = _write_temp_file(path, content, _mode_for_destination(path))
        os.replace(temp_path, path)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def write_conformance_tests(project_root, tool_name: str, contract: dict, dest_rel_path=None) -> str:
    """Write the generated conformance test module into the generated
    project. This is an independent test-scaffolding artifact (not a
    production config write) so it is produced on every run, `--apply` or
    not — it is what `pytest`-runs against captured real evidence.
    """
    dest_rel_path = dest_rel_path or (
        f"tests/threadlight_connect/test_{_safe_identifier(tool_name)}_conformance.py"
    )
    dest_full = Path(project_root) / dest_rel_path
    source = generate_conformance_test_source(tool_name, contract)
    _atomic_write_text(dest_full, source)
    return dest_rel_path


# ---------------------------------------------------------------------------
# Phase: verify — field-level conformance against a captured real sample
# ---------------------------------------------------------------------------
def check_conformance(contract: dict, real_response) -> dict:
    """Diff a captured real response against the contract. `real_response`
    is `{"items": [...]}` (or a bare list of records). Every field-level
    difference has the exact shape `{field, expected, actual, path}`, e.g.
    `status/string|required/missing/$.items[0].status`.

    An empty or missing `items` list is NOT a pass. With no real records to
    check, conformance is *unevaluated*: `evaluated` is False and `passed` is
    False. An unevaluated result is insufficient evidence and must target
    `real-unverified` — never a vacuous `real-verified`, and distinct from
    `real-drift` (which means records WERE checked and found to diverge).
    `item_count` reports how many records were actually checked.

    A record that is not a JSON object is itself a conformance failure: it emits
    a single `{field: "$", expected: "object", actual: <type>, path:
    "$.items[i]"}` difference and forces `passed` False. A non-object row can
    therefore never vacuously pass, even for an optional-only contract.
    """
    if isinstance(real_response, dict):
        items = real_response.get("items", [])
    elif isinstance(real_response, list):
        items = real_response
    else:
        items = []
    if not isinstance(items, list):
        items = []

    differences = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            # A record that is not an object cannot satisfy any field contract —
            # not even an optional-only one. Emit a single structured difference
            # at the item path and move on, so a scalar/array/null row can never
            # vacuously pass by "having no required fields to miss".
            differences.append(
                {
                    "field": "$",
                    "expected": "object",
                    "actual": _infer_type(item) or "unknown",
                    "path": f"$.items[{index}]",
                }
            )
            continue
        record = item
        for field in contract.get("fields", []):
            name = field["name"]
            required = field["required"]
            expected_type = field.get("type") or "unknown"
            expected = f"{expected_type}|{'required' if required else 'optional'}"
            path = f"$.items[{index}].{name}"
            if name not in record:
                if required:
                    differences.append(
                        {"field": name, "expected": expected, "actual": "missing", "path": path}
                    )
                continue
            actual_type = _infer_type(record[name]) or "unknown"
            if field.get("type") is not None and not _type_compatible(
                field["type"], record[name]
            ):
                differences.append(
                    {"field": name, "expected": expected, "actual": actual_type, "path": path}
                )

    evaluated = len(items) > 0
    return {
        "passed": evaluated and not differences,
        "evaluated": evaluated,
        "item_count": len(items),
        "differences": differences,
    }


# ---------------------------------------------------------------------------
# Evidence validation — OBO (on-behalf-of) + required-role revalidation.
# Malformed shapes raise (abort before any write); an honestly-absent/false
# evidence value is a normal `real-unverified` finding, not an error.
# ---------------------------------------------------------------------------
def _require_bool_flag(evidence: dict, key: str, label: str, *, default: bool = False) -> bool:
    """Read a boolean flag, distinguishing an ABSENT key from a supplied value.

    An absent key takes ``default`` (a normal ``real-unverified`` finding, not an
    error). A *supplied* value must be an actual ``bool`` — a string, number,
    ``None``, or any other shape is malformed and raises. There is no truthy/
    falsy or ``None``->``False`` coercion: the flag is only ever the boolean the
    caller actually recorded.
    """
    if key not in evidence:
        return default
    value = evidence[key]
    if not isinstance(value, bool):
        raise ConnectEvidenceError(f"{label} must be a boolean")
    return value


def _require_role_list(evidence: dict, key: str, label: str) -> list:
    """Read a role-name list, distinguishing an ABSENT key from a supplied value.

    An absent key yields ``[]`` (honestly no roles claimed). A *supplied* value
    must be a genuine list of non-empty, unique strings — never coerced with
    ``or []``, so a falsey non-list (``''``, ``0``, ``{}``, ``False``, ``None``)
    is malformed and raises rather than silently becoming empty. Every item must
    be a non-empty ``str`` (a ``bool`` is not a ``str`` and is rejected; scalars,
    objects, and empty strings are rejected), and duplicate names are rejected so
    the recorded evidence is canonical.
    """
    if key not in evidence:
        return []
    value = evidence[key]
    if not isinstance(value, list):
        raise ConnectEvidenceError(f"{label} must be a list of non-empty strings")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or isinstance(item, bool) or not item:
            raise ConnectEvidenceError(f"{label} must be a list of non-empty strings")
        if item in seen:
            raise ConnectEvidenceError(f"{label} must not contain duplicate role names")
        seen.add(item)
    return value


def _validate_agent_identity(evidence: dict) -> str | None:
    """Read ``agent_identity``: absent or a supplied ``null`` -> ``None`` (a
    normal unverified finding). A supplied value must be a NON-EMPTY string —
    an empty string or any non-string shape is malformed and raises.
    """
    if "agent_identity" not in evidence:
        return None
    value = evidence["agent_identity"]
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConnectEvidenceError(
            "role_evidence.agent_identity must be a non-empty string or null"
        )
    return value


def _validate_obo_evidence(evidence) -> dict:
    if evidence is None:
        return {"present": False, "user_scoped": False}
    if not isinstance(evidence, dict):
        raise ConnectEvidenceError("obo_evidence must be an object (dict) or null")
    return {
        "present": _require_bool_flag(evidence, "present", "obo_evidence.present"),
        "user_scoped": _require_bool_flag(evidence, "user_scoped", "obo_evidence.user_scoped"),
    }


def _validate_role_evidence(evidence) -> dict:
    if evidence is None:
        return {
            "revalidated": False,
            "required_roles": [],
            "validated_roles": [],
            "agent_identity": None,
        }
    if not isinstance(evidence, dict):
        raise ConnectEvidenceError("role_evidence must be an object (dict) or null")

    revalidated = _require_bool_flag(evidence, "revalidated", "role_evidence.revalidated")
    required_roles = _require_role_list(evidence, "required_roles", "role_evidence.required_roles")
    # `granted_roles` is an accepted alias for `validated_roles`; a supplied
    # `validated_roles` wins. Both absent -> [] (distinct from a malformed value).
    if "validated_roles" in evidence:
        validated_roles = _require_role_list(evidence, "validated_roles", "role_evidence.validated_roles")
    elif "granted_roles" in evidence:
        validated_roles = _require_role_list(evidence, "granted_roles", "role_evidence.granted_roles")
    else:
        validated_roles = []
    agent_identity = _validate_agent_identity(evidence)

    return {
        "revalidated": revalidated,
        "required_roles": required_roles,
        "validated_roles": validated_roles,
        "agent_identity": agent_identity,
    }


def _obo_ok(normalized_obo: dict) -> bool:
    return normalized_obo["present"] is True and normalized_obo["user_scoped"] is True


def _roles_ok(normalized_role: dict, current_agent_identity) -> bool:
    """Required-role revalidation is verified ONLY when it is tied to the exact
    CURRENT agent identity — it can never be opt-in. Verifying requires (1) a
    caller-supplied `current_agent_identity` and (2) role evidence that records
    exactly that identity. A missing current identity, or evidence whose
    recorded identity is absent or does not match, is unverified — a stale
    grant from a previous publish never carries over.
    """
    if normalized_role["revalidated"] is not True:
        return False
    required = set(normalized_role["required_roles"])
    validated = set(normalized_role["validated_roles"])
    if not required.issubset(validated):
        return False
    # Current identity is mandatory: revalidation cannot be verified against a
    # missing identity, and the evidence must name exactly the current one.
    if current_agent_identity is None:
        return False
    if normalized_role["agent_identity"] != current_agent_identity:
        return False
    return True


def transition_integration(conformance: dict, obo_evidence, role_evidence, *, current_agent_identity=None) -> str:
    """Pure function computing target_state from evidence:

    - conformance could not be evaluated (no real items captured)
      => real-unverified (insufficient evidence — never a vacuous pass, and
      distinct from real-drift)
    - field-level conformance failure/differences => real-drift
    - conformance passed but OBO user-scoped evidence missing/false, or
      required roles not revalidated against the CURRENT agent identity
      (a missing current identity, or a stale/mismatched one) => real-unverified
    - real-verified only after conformance passes AND OBO is user-scoped AND
      required roles are revalidated against the supplied current identity

    Raises ConnectEvidenceError (not a state) for malformed evidence shapes —
    callers must not persist anything when this raises.
    """
    normalized_obo = _validate_obo_evidence(obo_evidence)
    normalized_role = _validate_role_evidence(role_evidence)

    # Insufficient evidence (nothing real to check) can NEVER be a pass — it is
    # unverified, checked BEFORE the drift branch so an empty response is not
    # mistaken for evaluated-and-clean.
    if not conformance.get("evaluated", True):
        return "real-unverified"
    if not conformance.get("passed", False) or conformance.get("differences"):
        return "real-drift"
    if not _obo_ok(normalized_obo) or not _roles_ok(normalized_role, current_agent_identity):
        return "real-unverified"
    return "real-verified"


# ---------------------------------------------------------------------------
# Phase: plan — always built, pure (read-only), no writes
# ---------------------------------------------------------------------------
def build_apply_plan(
    project_root,
    spec_path,
    mcp_config_path,
    tool_name: str,
    *,
    real_endpoint_present: bool = False,
) -> list:
    """Build the file-by-file apply plan (read-only; always computed).

    The MCP-config step's description reflects whether a validated real endpoint
    is in hand: with one it names the target path/action (never the URL, so no
    credential can leak into the manifest); without one it says the endpoint
    must be supplied before ``--apply`` — so a dry run without an endpoint still
    plans, but says exactly what is missing.
    """
    if real_endpoint_present:
        mcp_description = (
            f"Point the {tool_name} MCP server entry at the validated real endpoint"
        )
    else:
        mcp_description = (
            f"Point the {tool_name} MCP server entry at the real endpoint "
            "(supply --real-endpoint before --apply)"
        )
    root = Path(project_root)
    plan = []
    for rel_path, description in (
        (spec_path, f"Record {tool_name} as a real, evidence-verified integration"),
        (mcp_config_path, mcp_description),
    ):
        action = "update" if (root / rel_path).exists() else "create"
        plan.append({"path": str(rel_path), "action": action, "description": description})
    return plan


# ---------------------------------------------------------------------------
# Phase: apply — atomic, transactional across SPEC.md + mcp-config.json
# ---------------------------------------------------------------------------
SPEC_SECTION_HEADING = "## Real integrations (threadlight-connect)"


def _spec_marker(tool_name: str) -> str:
    return f"<!-- threadlight-connect:{tool_name} -->"


def _render_spec_block(tool_name: str, contract: dict, generated_at: str) -> str:
    lines = [_spec_marker(tool_name), f"### {tool_name} — real, evidence-verified"]
    lines.append(f"- verified_at: {generated_at}")
    lines.append("- contract fields:")
    for field in contract["fields"]:
        req = "required" if field["required"] else "optional"
        ftype = field["type"] or "unknown"
        lines.append(f"  - `{field['name']}` ({ftype}, {req})")
    lines.append(_spec_marker(tool_name) + " END")
    return "\n".join(lines)


def _update_spec_text(existing_text: str, tool_name: str, contract: dict, generated_at: str) -> str:
    block = _render_spec_block(tool_name, contract, generated_at)
    begin, end = _spec_marker(tool_name), _spec_marker(tool_name) + " END"
    if begin in existing_text and end in existing_text:
        start_idx = existing_text.index(begin)
        end_idx = existing_text.index(end) + len(end)
        return existing_text[:start_idx] + block + existing_text[end_idx:]
    prefix = existing_text.rstrip("\n")
    if prefix and SPEC_SECTION_HEADING not in prefix:
        prefix += "\n\n" + SPEC_SECTION_HEADING
    elif not prefix:
        prefix = SPEC_SECTION_HEADING
    return prefix + "\n\n" + block + "\n"


def _coerce_config_mapping(value, label: str) -> dict:
    """Return a JSON-object config sub-mapping to mutate, failing CLOSED on a
    malformed shape.

    Absent (``None``) -> a fresh ``{}`` (start clean). An existing JSON object
    is returned verbatim (its unrelated keys are preserved by the caller). Any
    other shape (list, string, number, …) is malformed: raise
    ``ConnectEvidenceError`` before any write rather than silently coercing or
    overwriting it.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ConnectEvidenceError(
        f"existing mcp-config {label!r} must be a JSON object; got "
        f"{type(value).__name__} — refusing to overwrite"
    )


def _update_mcp_config(
    existing_data,
    tool_name: str,
    contract: dict,
    generated_at: str,
    real_endpoint: str,
) -> dict:
    """Bind ``servers[tool_name].url`` to the validated real endpoint AND record
    the integration verification metadata, preserving all unrelated config.

    * Unrelated top-level keys and unrelated `servers` / `integrations` entries
      are copied through untouched.
    * The tool's own server entry keeps its safe unrelated fields (`type`,
      `headers`, custom keys); its `url` is (re)pointed at the real endpoint,
      stale `host` / `endpoint` aliases and stdio `command` / `args` are
      dropped, and a descriptive `name` is retained only when it is not mock.
    * A malformed shape fails CLOSED: `servers` / `integrations` that are not
      JSON objects, or an existing `servers[tool_name]` that is not a JSON
      object, raise ``ConnectEvidenceError`` rather than being silently
      overwritten.

    The endpoint is persisted ONLY here (never in the connect manifest /
    evidence).
    """
    data = dict(existing_data) if isinstance(existing_data, dict) else {}

    integrations = dict(_coerce_config_mapping(data.get("integrations"), "integrations"))
    integrations[tool_name] = {
        "state": "real-verified",
        "verified_at": generated_at,
        "fields": [f["name"] for f in contract["fields"]],
    }
    data["integrations"] = integrations

    servers = dict(_coerce_config_mapping(data.get("servers"), "servers"))
    prior_entry = servers.get(tool_name, _MISSING)
    if prior_entry is _MISSING:
        entry: dict = {}
    elif isinstance(prior_entry, dict):
        entry = dict(prior_entry)  # preserve safe unrelated server fields
    else:
        raise ConnectEvidenceError(
            f"existing mcp-config servers[{tool_name!r}] must be a JSON object; "
            f"got {type(prior_entry).__name__} — refusing to overwrite"
        )
    # Drop mutually-exclusive mock/stdio transport fields ONLY when present, so a
    # real HTTPS `url` is never left contradicted by a leftover mock transport.
    for mock_field in _MOCK_TRANSPORT_FIELDS:
        entry.pop(mock_field, None)
    for key in [k for k in entry if isinstance(k, str) and _endpoint_is_mock(k)]:
        entry.pop(key, None)
    entry.pop("host", None)
    entry.pop("endpoint", None)
    if _endpoint_is_mock(entry.get("name")):
        entry.pop("name", None)
    entry["url"] = real_endpoint
    servers[tool_name] = entry
    data["servers"] = servers
    return data


def _capture_prior(path: Path) -> dict:
    """Snapshot a destination's prior state for transactional rollback: whether
    it existed, its exact bytes, and its mode. Read once, up front, before any
    replace runs, so rollback can restore it verbatim.
    """
    if path.exists():
        return {"existed": True, "bytes": path.read_bytes(), "mode": path.stat().st_mode}
    return {"existed": False, "bytes": None, "mode": None}


def _restore_prior(path: Path, prior: dict) -> None:
    """Roll a destination back to its captured prior state: rewrite the exact
    prior bytes via a same-directory atomic temp + `os.replace` (restoring the
    prior mode by stamping it on the temp file before the replace), or remove
    the file if it did not previously exist. Raises if the restore itself
    cannot complete (a failed rollback).
    """
    if not prior["existed"]:
        path.unlink(missing_ok=True)
        return
    restore_mode = (prior["mode"] & 0o7777) if prior["mode"] is not None else None
    temp_path = None
    try:
        temp_path = _write_temp_bytes(path, prior["bytes"], restore_mode)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _load_existing_mcp_config(mcp_full: Path, mcp_config_path) -> dict:
    if not mcp_full.exists():
        return {}
    raw_mcp = mcp_full.read_text(encoding="utf-8")
    try:
        existing_mcp = json.loads(raw_mcp)
    except json.JSONDecodeError as exc:
        raise ConnectEvidenceError(
            f"existing mcp-config at {mcp_config_path} is not valid JSON "
            f"({exc}); refusing to overwrite — repair or remove it first"
        ) from exc
    if not isinstance(existing_mcp, dict):
        raise ConnectEvidenceError(
            f"existing mcp-config at {mcp_config_path} must be a JSON object; "
            f"got {type(existing_mcp).__name__} — refusing to overwrite"
        )
    return existing_mcp


def _serialize_manifest(manifest: dict) -> str:
    """Serialize a manifest to the exact bytes the shared `atomic_write_json`
    would emit (sorted keys, 2-space indent, no NaN/Infinity, trailing newline),
    so a manifest committed inside the apply transaction is byte-identical to one
    emitted on the non-apply path.
    """
    return json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _commit_transaction(targets: list, postcondition=None) -> None:
    """Commit a set of file writes as one rollback-aware transaction.

    `targets` is an ordered list of `(destination: Path, new_text: str)`. A temp
    file is staged for EVERY destination (stamped with the mode the destination
    should end up with — an existing file keeps its exact prior mode; a brand-new
    one gets the umask-honoring non-executable default) before ANY destination is
    replaced, and each destination's prior existence/bytes/mode is captured up
    front. If a replace fails after an earlier one already landed, every
    already-committed destination is rolled back to its captured prior bytes/mode
    (or removed if it did not previously exist) and `ConnectApplyError` is raised
    — every destination survives at its prior bytes/mode/existence.

    An optional `postcondition` callable runs AFTER all replaces succeed but is
    treated as part of the transaction: if it raises, every committed
    destination is rolled back exactly as a failed replace would be (so a failed
    post-apply invariant never leaves a half-applied swap on disk).

    Honest limitation: each `os.replace` is individually atomic, but the set of
    replaces is not a single filesystem transaction, so this cannot defend
    against a hard crash / power loss / SIGKILL in the window *between* two
    replaces. If a compensating rollback itself fails, the stronger
    `ConnectInconsistentStateError` — naming the unreconciled path(s) — is raised
    and success is never reported.
    """
    priors = {dest: _capture_prior(dest) for dest, _ in targets}

    # Stage a temp for every destination before replacing ANY of them.
    staged: list = []  # (destination, temp_path)
    try:
        for dest, text in targets:
            prior = priors[dest]
            desired_mode = (
                (prior["mode"] & 0o7777) if prior["existed"] else _default_new_file_mode()
            )
            staged.append((dest, _write_temp_file(dest, text, desired_mode)))
    except BaseException as exc:
        for _, temp_path in staged:
            temp_path.unlink(missing_ok=True)
        raise ConnectApplyError(f"failed to stage transactional write: {exc}") from exc

    # Commit one destination at a time, tracking which landed so a later failure
    # (a replace error OR a failed postcondition) can roll the earlier ones back.
    replaced: list = []
    pending = dict(staged)  # destination -> temp_path not yet replaced
    try:
        for dest, temp_path in staged:
            os.replace(temp_path, dest)
            replaced.append(dest)
            del pending[dest]
        if postcondition is not None:
            # Part of the transaction: a failing invariant rolls all three back.
            postcondition()
    except BaseException as exc:
        # Discard temp files for destinations we never reached.
        for temp_path in pending.values():
            temp_path.unlink(missing_ok=True)
        # Roll every already-committed destination back to its prior state.
        unreconciled = []
        for dest in reversed(replaced):
            try:
                _restore_prior(dest, priors[dest])
            except BaseException:
                unreconciled.append(dest)
        if unreconciled:
            names = ", ".join(str(p) for p in unreconciled)
            raise ConnectInconsistentStateError(
                "apply failed and rollback could not restore "
                f"{names}: SPEC.md / mcp-config.json / connect-manifest.json may be "
                "divergent on disk and must be reconciled by hand"
            ) from exc
        raise ConnectApplyError(f"failed to apply transactional write: {exc}") from exc


def _commit_verified_apply(
    root: Path,
    spec_path,
    mcp_config_path,
    manifest_full: Path,
    tool_name: str,
    contract: dict,
    generated_at: str,
    new_mcp_data: dict,
    manifest: dict,
    real_endpoint: str,
) -> None:
    """Commit the mock-to-real swap — SPEC.md, mcp-config.json, AND the connect
    manifest — as one rollback-aware transaction.

    `new_mcp_data` is the already-validated updated MCP config (built and its
    predicted binding checked by the caller *before* any file is mutated). The
    three new file contents are assembled up front, then handed to
    `_commit_transaction`, so a staging/replace failure on any of the three
    leaves all three at their prior bytes/modes/existence. `atomic_write_json`
    is deliberately NOT used for the manifest here — emitting it after the config
    commit would put it outside the transaction and could leave SPEC/mcp updated
    while the manifest lagged.

    A post-apply postcondition re-reads mcp-config.json from disk and asserts its
    effective endpoint still equals the validated real endpoint (and is not a
    mock); if it does not, the whole transaction is rolled back (or, if rollback
    also fails, ConnectInconsistentStateError is raised) so success is never
    reported on a divergent persisted binding.
    """
    spec_full = root / spec_path
    mcp_full = root / mcp_config_path

    existing_spec = spec_full.read_text(encoding="utf-8") if spec_full.exists() else ""
    new_spec_text = _update_spec_text(existing_spec, tool_name, contract, generated_at)
    new_mcp_text = json.dumps(new_mcp_data, indent=2, sort_keys=True) + "\n"
    new_manifest_text = _serialize_manifest(manifest)

    def _verify_persisted_binding() -> None:
        try:
            persisted = json.loads(mcp_full.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConnectApplyError(
                f"post-apply re-read of {mcp_config_path} failed: {exc}"
            ) from exc
        effective = _effective_mcp_endpoint(persisted, tool_name)
        server = _effective_mcp_server(persisted, tool_name)
        if effective != real_endpoint or _server_is_provably_mock(server):
            raise ConnectApplyError(
                "post-apply verification failed: persisted MCP endpoint for "
                f"{tool_name} does not match the validated real endpoint"
            )

    _commit_transaction(
        [
            (spec_full, new_spec_text),
            (mcp_full, new_mcp_text),
            (manifest_full, new_manifest_text),
        ],
        postcondition=_verify_persisted_binding,
    )


# ---------------------------------------------------------------------------
# Phase: emit — schema-validated, atomic manifest write (shared envelope)
# ---------------------------------------------------------------------------
def _contains_forbidden_keys(obj) -> bool:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and any(marker in key.lower() for marker in _FORBIDDEN_KEY_MARKERS):
                return True
            if _contains_forbidden_keys(value):
                return True
        return False
    if isinstance(obj, list):
        return any(_contains_forbidden_keys(item) for item in obj)
    return False


def load_current_state(manifest_full_path) -> str:
    """Read the *persisted* integration_state from a prior manifest, if any.
    Defaults to `mock` — the starting state for every integration — but ONLY
    when there is no prior manifest at all. A prior manifest that exists yet
    cannot be read or parsed is NOT silently reset to `mock`: that would
    discard a real prior integration_state (e.g. downgrade a live
    `real-verified` back to `mock`) and mask on-disk corruption. Raise a clean
    `ConnectEvidenceError` before any write so the bytes on disk survive
    untouched for the operator to repair.

    A prior manifest that IS a JSON object but is *missing* `integration_state`,
    or carries a value outside the four valid states, is likewise never quietly
    treated as `mock` — that would fabricate a starting state from a corrupt or
    tampered record. Both raise `ConnectEvidenceError` (before any write) rather
    than guessing.
    """
    path = Path(manifest_full_path)
    if not path.exists():
        return "mock"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConnectEvidenceError(
            f"prior connect manifest at {path} cannot be read ({exc}); refusing "
            "to reset integration state — repair or remove it first"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectEvidenceError(
            f"prior connect manifest at {path} is not valid JSON ({exc}); "
            "refusing to reset integration state to mock — repair or remove it first"
        ) from exc
    if not isinstance(data, dict):
        raise ConnectEvidenceError(
            f"prior connect manifest at {path} must be a JSON object; got "
            f"{type(data).__name__} — refusing to reset integration state"
        )
    if "integration_state" not in data:
        raise ConnectEvidenceError(
            f"prior connect manifest at {path} is missing 'integration_state'; "
            "refusing to fabricate a starting state — repair or remove it first"
        )
    state = data["integration_state"]
    if state not in VALID_STATES:
        raise ConnectEvidenceError(
            f"prior connect manifest at {path} has invalid integration_state "
            f"{state!r} (expected one of {', '.join(VALID_STATES)}); refusing to "
            "reset it — repair or remove it first"
        )
    return state


# The connect leg emits EXACTLY these four findings, one each, in this order —
# the stable live-leg gap-evidence contract threadlight-production-ready projects
# 1:1 onto its INT-001..004 targets (see that skill's _leg_finding). The IDs are
# fixed and never dynamic: field-level conformance detail stays in
# ``conformance.differences`` rather than being fanned out into per-field finding
# IDs, so a consumer always sees the same four IDs regardless of how the real
# response diverged.
INT_FINDING_IDS = ("INT-001", "INT-002", "INT-003", "INT-004")


def _int_001_conformance(conformance: dict) -> dict:
    """INT-001 — contract conformance evidence.

    unevaluated (no real records captured) -> not-verified (insufficient
    evidence, never a vacuous pass); any field-level difference / non-pass ->
    must-fix (the detail lives in ``conformance.differences``); an evaluated,
    clean pass -> pass.
    """
    if not conformance.get("evaluated", False):
        return {
            "id": "INT-001",
            "status": "not-verified",
            "detail": (
                "conformance unevaluated — the captured real response had no "
                "items to check; capture a non-empty real sample to verify the "
                "contract"
            ),
        }
    differences = conformance.get("differences") or []
    if differences or not conformance.get("passed", False):
        return {
            "id": "INT-001",
            "status": "must-fix",
            "detail": (
                f"real response diverged from the mock contract in "
                f"{len(differences)} field(s); see conformance.differences"
            ),
        }
    return {
        "id": "INT-001",
        "status": "pass",
        "detail": (
            f"real response conforms to the mock contract across "
            f"{conformance.get('item_count', 0)} record(s)"
        ),
    }


def _int_002_binding(target_state: str, integration_state: str) -> dict:
    """INT-002 — runtime binding / mock->real state.

    Keyed on BOTH this run's evidence-computed ``target_state`` AND the
    ``integration_state`` this manifest actually persists (which stays ``mock``
    on a dry run and only advances to ``real-verified`` inside a *successful*
    ``--apply`` transaction). The binding is only ever ``pass`` once the swap is
    both proven by evidence AND persisted — never on evidence alone.

      * ``real-drift`` target -> must-fix, regardless of ``--apply`` or any
        previously persisted state (the runtime must never bind to a drifting
        endpoint);
      * ``real-verified`` target AND a persisted ``real-verified`` binding ->
        pass (evidence supports the swap and ``--apply`` has persisted it);
      * ``real-verified`` target but the binding is NOT persisted (a dry run —
        ``integration_state`` still ``mock``) -> not-verified: the evidence
        supports the swap but ``--apply`` has not persisted the binding;
      * mock / real-unverified target -> not-verified (the real, non-mock
        binding is not yet verified).
    """
    if target_state == "real-drift":
        return {
            "id": "INT-002",
            "status": "must-fix",
            "detail": (
                "real endpoint drifted from the mock contract (real-drift); the "
                "runtime must not bind to a drifting endpoint"
            ),
        }
    if target_state == "real-verified":
        if integration_state == "real-verified":
            return {
                "id": "INT-002",
                "status": "pass",
                "detail": (
                    "binding persisted at real-verified (mock fully swapped and "
                    "applied)"
                ),
            }
        return {
            "id": "INT-002",
            "status": "not-verified",
            "detail": (
                "evidence supports the swap to real-verified, but --apply has "
                "not persisted the binding"
            ),
        }
    return {
        "id": "INT-002",
        "status": "not-verified",
        "detail": (
            f"integration still {target_state} — real (non-mock) binding not yet verified"
        ),
    }


def _int_003_identity(normalized_obo: dict) -> dict:
    """INT-003 — identity / OBO evidence.

    present AND user-scoped -> pass; anything else (absent, or present but not
    user-scoped) -> not-verified. There is no explicit-OBO-failure signal in the
    evidence shape, so an explicit must-fix is not representable here.
    """
    if _obo_ok(normalized_obo):
        return {
            "id": "INT-003",
            "status": "pass",
            "detail": "OBO evidence is present and user-scoped",
        }
    return {
        "id": "INT-003",
        "status": "not-verified",
        "detail": "OBO user-scoped identity evidence is missing or incomplete",
    }


def _int_004_roles(normalized_role: dict, current_agent_identity) -> dict:
    """INT-004 — required-role revalidation / apply evidence.

    revalidated against the CURRENT agent identity with every required role
    granted -> pass; revalidation ran but a required role is missing (an
    explicit failure) -> must-fix; otherwise (never revalidated, no current
    identity, or a stale/mismatched identity) -> not-verified.
    """
    if _roles_ok(normalized_role, current_agent_identity):
        return {
            "id": "INT-004",
            "status": "pass",
            "detail": "required roles revalidated against the current agent identity",
        }
    required = set(normalized_role.get("required_roles") or [])
    validated = set(normalized_role.get("validated_roles") or [])
    if normalized_role.get("revalidated") is True and required and not required.issubset(validated):
        return {
            "id": "INT-004",
            "status": "must-fix",
            "detail": (
                "role revalidation ran but the current identity is missing "
                "required role(s)"
            ),
        }
    return {
        "id": "INT-004",
        "status": "not-verified",
        "detail": (
            "required-role revalidation against the current agent identity is "
            "missing or stale"
        ),
    }


def _build_findings(
    conformance: dict,
    target_state: str,
    integration_state: str,
    normalized_obo: dict,
    normalized_role: dict,
    current_agent_identity,
) -> list:
    """Emit the four stable INT-001..004 findings, exactly one of each, in order.

    ``integration_state`` is the state THIS manifest persists (``real-verified``
    only inside a successful ``--apply`` transaction, otherwise the prior
    persisted state) — INT-002 keys its ``pass`` on it so a dry run never claims
    an applied binding.

    Detailed field-level conformance differences stay in
    ``conformance.differences`` — never expanded into dynamic finding IDs — so
    the findings array is always exactly ``INT_FINDING_IDS``.
    """
    return [
        _int_001_conformance(conformance),
        _int_002_binding(target_state, integration_state),
        _int_003_identity(normalized_obo),
        _int_004_roles(normalized_role, current_agent_identity),
    ]


def _manifest_status(real_response) -> str:
    if isinstance(real_response, dict):
        items = real_response.get("items", [])
    elif isinstance(real_response, list):
        items = real_response
    else:
        items = []
    return "complete" if items else "partial"


def build_connect_manifest(
    *,
    tool_name,
    integration_state,
    target_state,
    contract,
    conformance,
    evidence_summary,
    apply_plan,
    changed_paths,
    apply,
    status,
    findings,
    generated_at,
    evidence_captured_at=None,
    valid_for_hours=24,
    endpoint_configured=False,
    endpoint_verified=False,
) -> dict:
    # `endpoint_configured` / `endpoint_verified` are SAFE booleans only — the
    # real endpoint URL itself is never persisted in the manifest (it lives only
    # in mcp-config.json). `endpoint_configured` records that a validated real
    # endpoint was supplied this run; `endpoint_verified` records that the
    # verified binding was persisted by a successful --apply.
    evidence_summary = {
        **evidence_summary,
        "endpoint_configured": bool(endpoint_configured),
        "endpoint_verified": bool(endpoint_verified),
    }
    payload = {
        "tool_name": tool_name,
        "integration_state": integration_state,
        "target_state": target_state,
        "contract": contract,
        "conformance": conformance,
        "evidence_summary": evidence_summary,
        "apply_plan": apply_plan,
        "changed_paths": changed_paths,
        "apply": apply,
    }
    return build_envelope(
        schema=CONNECT_MANIFEST_SCHEMA,
        tool_version=TOOL_VERSION,
        status=status,
        generated_at=generated_at,
        valid_for_hours=valid_for_hours,
        source_oldest_at=evidence_captured_at,
        findings=findings,
        payload=payload,
    )


def _reject_unknown_keys(obj, allowed: set, label: str) -> None:
    """Enforce the schema's ``additionalProperties: false`` for one controlled
    object: raise if *obj* carries any key outside *allowed*. Kept stdlib-only
    (no `jsonschema` runtime dependency) so validation stays consistent with
    the rest of the repo.
    """
    if not isinstance(obj, dict):
        return
    unknown = set(obj).difference(allowed)
    if unknown:
        raise ManifestValidationError(
            f"{label} has unknown key(s): " + ", ".join(sorted(unknown))
        )


_MANIFEST_TOP_LEVEL_KEYS = {
    "schema", "tool_version", "generated_at", "freshness", "status", "findings",
    "tool_name", "integration_state", "target_state", "contract", "conformance",
    "evidence_summary", "apply_plan", "changed_paths", "apply",
}
_CONFORMANCE_KEYS = {"passed", "evaluated", "item_count", "differences"}
_DIFFERENCE_KEYS = {"field", "expected", "actual", "path"}
_EVIDENCE_SUMMARY_KEYS = {
    "obo_present", "obo_user_scoped", "roles_revalidated", "required_roles",
    "endpoint_configured", "endpoint_verified",
}
_CONTRACT_KEYS = {"schema", "tool_name", "generated_at", "fields"}
_CONTRACT_FIELD_KEYS = {"name", "required", "type", "cardinality"}
_FINDING_REQUIRED_KEYS = {"id", "status"}
_APPLY_PLAN_ITEM_REQUIRED_KEYS = {"path", "action", "description"}

# The findings array is a fixed tuple: exactly INT-001..INT-004, one each, in
# order. This mirrors the schema's tuple `items` + minItems/maxItems 4 +
# additionalItems:false, so the hand validator and Draft7Validator reject the
# same shapes (missing IDs, extra findings, wrong order, dynamic IDs).
_FINDING_ID_SEQUENCE = list(INT_FINDING_IDS)

# Value-domain enums mirrored 1:1 from the schemas so the hand validator
# rejects exactly what jsonschema would (see tests/test_connect.py parity).
_FINDING_STATUS_ENUM = {"pass", "must-fix", "should-fix", "not-verified"}
_APPLY_ACTION_ENUM = {"create", "update"}
_CONTRACT_TYPE_ENUM = {"string", "integer", "number", "boolean", "array", "object"}
_CONTRACT_CARDINALITY_ENUM = {"single", "array"}


def _require_object_keys(value, required: set, label: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{label} must be an object")
    missing = required.difference(value)
    if missing:
        raise ManifestValidationError(
            f"{label} missing required key(s): " + ", ".join(sorted(missing))
        )
    return value


# ---------------------------------------------------------------------------
# Typed leaf validators — stdlib-only mirrors of the JSON-Schema primitive
# constraints (type / enum / minimum / minLength / nullable). Each names the
# offending path so a malformed manifest points straight at the bad field, and
# each rejects exactly what jsonschema's Draft-07 validator rejects: booleans
# never satisfy integer/number, unknown enum members and out-of-range minimums
# are refused, and `["string", "null"]` unions accept only a string or null.
# ---------------------------------------------------------------------------
def _require_string(value, label: str, *, min_length: int = 0):
    if not isinstance(value, str) or len(value) < min_length:
        suffix = "a non-empty string" if min_length else "a string"
        raise ManifestValidationError(f"{label} must be {suffix}")
    return value


def _require_boolean(value, label: str):
    if not isinstance(value, bool):
        raise ManifestValidationError(f"{label} must be a boolean")
    return value


def _require_integer(value, label: str, *, minimum=None):
    # Draft-07 integer semantics: an integer is any number with a zero
    # fractional part, so a float like 1.0 IS a valid integer (1 and 1.0 are the
    # same JSON value) while 1.5 is not. A bool is never an integer. This mirrors
    # what jsonschema's Draft7Validator accepts for {"type": "integer"}.
    if isinstance(value, bool):
        raise ManifestValidationError(f"{label} must be an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        number = value
    else:
        raise ManifestValidationError(f"{label} must be an integer")
    if minimum is not None and number < minimum:
        raise ManifestValidationError(f"{label} must be >= {minimum}")
    return value


def _require_array(value, label: str) -> list:
    if not isinstance(value, list):
        raise ManifestValidationError(f"{label} must be an array")
    return value


def _require_enum(value, allowed: set, label: str):
    try:
        member = value in allowed
    except TypeError:  # unhashable (e.g. a list/dict) is never an enum member
        member = False
    if not member:
        raise ManifestValidationError(
            f"{label} must be one of {', '.join(sorted(allowed))}; got {value!r}"
        )
    return value


def _require_nullable_enum(value, allowed: set, label: str):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestValidationError(f"{label} must be a string or null")
    return _require_enum(value, allowed, label)


def validate_connect_manifest(manifest: dict) -> None:
    """Hand-rolled schema check (mirrors references/connect-manifest.schema.json
    and the referenced data-contract.schema.json) layered on top of the shared
    envelope's own validation — stdlib-only, no `jsonschema` runtime dependency,
    consistent with the rest of the repo. It enforces the schemas' full value
    domain, not just object shapes: every leaf's declared JSON type
    (object/array/string/boolean/integer/number — a bool is never an
    integer/number), every ``const`` and ``enum``, every ``minimum`` /
    ``minLength``, nullable ``["string", "null"]`` unions, required keys, and
    ``additionalProperties: false`` for the top level and each controlled nested
    object (`freshness` via the envelope, `conformance`, every `differences`
    item, `evidence_summary`, the `contract` and its `fields` items). Array item
    types are checked for `findings`, `differences`, `required_roles`,
    `apply_plan`, `changed_paths`, and `contract.fields`. Timestamp/string
    fields reuse the shared envelope helpers. An ``integer`` leaf follows Draft-07
    semantics — an integral float such as ``1.0`` is accepted, ``1.5`` is not.
    Every message names the offending path, and array elements carry their index
    (e.g. ``findings[1].status``, ``contract.fields[0].type``) so a malformed row
    points straight at itself. A test-only jsonschema parity suite pins this to
    the schemas.
    """
    validate_envelope(manifest)

    required_keys = {
        "tool_name", "integration_state", "target_state", "contract",
        "conformance", "evidence_summary", "apply_plan", "changed_paths", "apply",
    }
    missing = required_keys.difference(manifest)
    if missing:
        raise ManifestValidationError(
            "connect manifest missing keys: " + ", ".join(sorted(missing))
        )

    _reject_unknown_keys(manifest, _MANIFEST_TOP_LEVEL_KEYS, "connect manifest")

    if manifest["schema"] != CONNECT_MANIFEST_SCHEMA:
        raise ManifestValidationError(
            f"schema must be {CONNECT_MANIFEST_SCHEMA!r}"
        )
    _require_string(manifest["tool_name"], "tool_name", min_length=1)

    for key in ("integration_state", "target_state"):
        if manifest[key] not in VALID_STATES:
            raise ManifestValidationError(f"unknown {key}: {manifest[key]!r}")

    contract = _require_object_keys(manifest["contract"], _CONTRACT_KEYS, "contract")
    _reject_unknown_keys(contract, _CONTRACT_KEYS, "contract")
    if contract["schema"] != DATA_CONTRACT_SCHEMA:
        raise ManifestValidationError(
            f"contract.schema must be {DATA_CONTRACT_SCHEMA!r}"
        )
    _require_string(contract["tool_name"], "contract.tool_name", min_length=1)
    _require_string(contract["generated_at"], "contract.generated_at", min_length=1)
    _validate_iso8601_timestamp(contract["generated_at"], "contract.generated_at")
    contract_fields = _require_array(contract["fields"], "contract.fields")
    for index, field in enumerate(contract_fields):
        label = f"contract.fields[{index}]"
        field = _require_object_keys(field, _CONTRACT_FIELD_KEYS, label)
        _reject_unknown_keys(field, _CONTRACT_FIELD_KEYS, label)
        _require_string(field["name"], f"{label}.name", min_length=1)
        _require_boolean(field["required"], f"{label}.required")
        _require_nullable_enum(field["type"], _CONTRACT_TYPE_ENUM, f"{label}.type")
        _require_nullable_enum(
            field["cardinality"], _CONTRACT_CARDINALITY_ENUM, f"{label}.cardinality"
        )

    findings = _require_array(manifest["findings"], "findings")
    for index, finding in enumerate(findings):
        label = f"findings[{index}]"
        finding = _require_object_keys(finding, _FINDING_REQUIRED_KEYS, label)
        _require_string(finding["id"], f"{label}.id")
        _require_enum(finding["status"], _FINDING_STATUS_ENUM, f"{label}.status")
    # Strict tuple: exactly INT-001..INT-004, one each, in order (mirrors the
    # schema). Field-level conformance detail lives in conformance.differences,
    # never in dynamic finding IDs, so the set is always these four.
    finding_ids = [finding["id"] for finding in findings]
    if finding_ids != _FINDING_ID_SEQUENCE:
        raise ManifestValidationError(
            "findings must be exactly "
            + ", ".join(_FINDING_ID_SEQUENCE)
            + " (one each, in order); got "
            + (", ".join(finding_ids) if finding_ids else "[]")
        )

    conformance = manifest["conformance"]
    conformance = _require_object_keys(conformance, _CONFORMANCE_KEYS, "conformance")
    _reject_unknown_keys(conformance, _CONFORMANCE_KEYS, "conformance")
    _require_boolean(conformance["passed"], "conformance.passed")
    _require_boolean(conformance["evaluated"], "conformance.evaluated")
    _require_integer(conformance["item_count"], "conformance.item_count", minimum=0)
    differences = _require_array(conformance["differences"], "conformance.differences")
    for index, diff in enumerate(differences):
        label = f"conformance.differences[{index}]"
        diff = _require_object_keys(diff, _DIFFERENCE_KEYS, label)
        _reject_unknown_keys(diff, _DIFFERENCE_KEYS, label)
        _require_string(diff["field"], f"{label}.field")
        _require_string(diff["expected"], f"{label}.expected")
        _require_string(diff["actual"], f"{label}.actual")
        _require_string(diff["path"], f"{label}.path")

    evidence_summary = _require_object_keys(
        manifest["evidence_summary"], _EVIDENCE_SUMMARY_KEYS, "evidence_summary"
    )
    _reject_unknown_keys(evidence_summary, _EVIDENCE_SUMMARY_KEYS, "evidence_summary")
    _require_boolean(evidence_summary["obo_present"], "evidence_summary.obo_present")
    _require_boolean(evidence_summary["obo_user_scoped"], "evidence_summary.obo_user_scoped")
    _require_boolean(
        evidence_summary["roles_revalidated"], "evidence_summary.roles_revalidated"
    )
    _require_boolean(
        evidence_summary["endpoint_configured"], "evidence_summary.endpoint_configured"
    )
    _require_boolean(
        evidence_summary["endpoint_verified"], "evidence_summary.endpoint_verified"
    )
    required_roles = _require_array(
        evidence_summary["required_roles"], "evidence_summary.required_roles"
    )
    for index, role in enumerate(required_roles):
        _require_string(role, f"evidence_summary.required_roles[{index}]")

    apply_plan = _require_array(manifest["apply_plan"], "apply_plan")
    for index, item in enumerate(apply_plan):
        label = f"apply_plan[{index}]"
        item = _require_object_keys(item, _APPLY_PLAN_ITEM_REQUIRED_KEYS, label)
        _require_string(item["path"], f"{label}.path")
        _require_enum(item["action"], _APPLY_ACTION_ENUM, f"{label}.action")
        _require_string(item["description"], f"{label}.description")

    changed_paths = _require_array(manifest["changed_paths"], "changed_paths")
    for index, path_value in enumerate(changed_paths):
        _require_string(path_value, f"changed_paths[{index}]")

    _require_boolean(manifest["apply"], "apply")

    if _contains_forbidden_keys(manifest):
        raise ManifestValidationError(
            "connect manifest must not contain credential/token/secret-shaped keys"
        )


def write_connect_manifest(path, manifest: dict) -> None:
    validate_connect_manifest(manifest)
    atomic_write_json(path, manifest)


# ---------------------------------------------------------------------------
# Orchestration — wires every phase together for one CLI/API invocation
# ---------------------------------------------------------------------------
def _validate_evidence_captured_at(value):
    """Validate an optional caller-supplied evidence-capture timestamp EARLY —
    before any file is written — so a malformed value aborts the run cleanly
    without disturbing a prior manifest/config. `None` (unknown) is allowed and
    is recorded as such in `freshness.source_oldest_at`; the manifest never
    borrows `generated_at` to fake source freshness.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConnectEvidenceError(
            "evidence_captured_at must be an ISO-8601 timestamp string or null"
        )
    try:
        _validate_iso8601_timestamp(value, "evidence_captured_at", nullable=True)
    except ManifestValidationError as exc:
        raise ConnectEvidenceError(str(exc)) from exc
    return value


def run_connect(
    *,
    project_root,
    tool_name: str,
    tool_source: str,
    sample,
    real_response,
    obo_evidence=None,
    role_evidence=None,
    apply: bool = False,
    real_endpoint=None,
    spec_path=DEFAULT_SPEC_PATH,
    mcp_config_path=DEFAULT_MCP_CONFIG_PATH,
    manifest_path=DEFAULT_MANIFEST_PATH,
    current_agent_identity=None,
    generated_at=None,
    evidence_captured_at=None,
    valid_for_hours=24,
) -> dict:
    generated_at = generated_at or _now_iso()
    # Fail fast on a malformed capture timestamp, before any artifact is
    # written (the manifest — where source_oldest_at lands — is emitted only
    # after write_conformance_tests / the apply transaction).
    evidence_captured_at = _validate_evidence_captured_at(evidence_captured_at)
    root = Path(project_root)

    contract = extract_contract(tool_name, tool_source, sample, generated_at=generated_at)
    conformance = check_conformance(contract, real_response)

    # Evidence validation gate — raises BEFORE any write when malformed, so a
    # malformed-evidence run never disturbs a prior valid manifest/config.
    target_state = transition_integration(
        conformance, obo_evidence, role_evidence, current_agent_identity=current_agent_identity
    )

    manifest_full_path = root / manifest_path
    # Reads the persisted state and raises (before any write) on a corrupt prior
    # manifest, or one missing/with an invalid integration_state.
    integration_state = load_current_state(manifest_full_path)

    apply_verified = apply and target_state == "real-verified"

    # Real-endpoint contract. The endpoint is OPTIONAL for dry-run evidence
    # assessment but MANDATORY to apply a verified real binding. A supplied
    # endpoint is validated whenever present (so a mock / credential-bearing /
    # malformed endpoint is rejected before any write); a missing endpoint on a
    # verified apply is a controlled ConnectEvidenceError so INT-002 can never
    # pass on an unbound swap. The validated URL is persisted ONLY in
    # mcp-config.json, never in the manifest/evidence.
    normalized_endpoint = None
    if real_endpoint is not None:
        normalized_endpoint = _validate_real_endpoint(real_endpoint)
    if apply_verified and normalized_endpoint is None:
        raise ConnectEvidenceError(
            "real_endpoint is required to apply a verified real binding "
            "(--real-endpoint https://...); refusing to persist a mock->real swap "
            "without the real endpoint"
        )

    existing_mcp = None
    new_mcp_data = None
    if apply_verified:
        # Validate the apply destination before generating any artifact. This
        # keeps the CLI's "(nothing written)" guarantee literal when an existing
        # MCP config is corrupt or is not a JSON object, and the parsed value is
        # reused when the transaction builds the new config.
        existing_mcp = _load_existing_mcp_config(root / mcp_config_path, mcp_config_path)
        # Build the updated config (fails CLOSED on a malformed servers/tool
        # shape) and verify the PREDICTED effective endpoint equals the
        # validated real endpoint and is not mock — all before any file is
        # touched, so a bad binding aborts with nothing written.
        new_mcp_data = _update_mcp_config(
            existing_mcp, tool_name, contract, generated_at, normalized_endpoint
        )
        predicted = _effective_mcp_endpoint(new_mcp_data, tool_name)
        predicted_server = _effective_mcp_server(new_mcp_data, tool_name)
        if (
            predicted != normalized_endpoint
            or _server_is_provably_mock(predicted_server)
        ):
            raise ConnectApplyError(
                "predicted MCP binding does not resolve to the validated real "
                f"endpoint for {tool_name} — refusing to apply (nothing written)"
            )

    apply_plan = build_apply_plan(
        root, spec_path, mcp_config_path, tool_name,
        real_endpoint_present=normalized_endpoint is not None,
    )

    # Deterministic PLANNED changed_paths: a verified apply rewrites exactly the
    # two production config files (never the manifest or the conformance test),
    # computed up front so the final manifest can record them BEFORE any write.
    changed_paths: list = (
        [str(spec_path), str(mcp_config_path)] if apply_verified else []
    )
    # On a successful verified apply the persisted state advances to
    # real-verified; the manifest is built with that planned post-state, and the
    # all-or-nothing transaction below guarantees it is only ever persisted if
    # all three files land together.
    manifest_integration_state = "real-verified" if apply_verified else integration_state

    normalized_obo = _validate_obo_evidence(obo_evidence)
    normalized_role = _validate_role_evidence(role_evidence)
    evidence_summary = {
        "obo_present": normalized_obo["present"],
        "obo_user_scoped": normalized_obo["user_scoped"],
        "roles_revalidated": normalized_role["revalidated"],
        "required_roles": sorted(normalized_role["required_roles"]),
    }

    manifest = build_connect_manifest(
        tool_name=tool_name,
        integration_state=manifest_integration_state,
        target_state=target_state,
        contract=contract,
        conformance=conformance,
        evidence_summary=evidence_summary,
        apply_plan=apply_plan,
        changed_paths=changed_paths,
        apply=apply,
        status=_manifest_status(real_response),
        findings=_build_findings(
            conformance,
            target_state,
            manifest_integration_state,
            normalized_obo,
            normalized_role,
            current_agent_identity,
        ),
        generated_at=generated_at,
        evidence_captured_at=evidence_captured_at,
        valid_for_hours=valid_for_hours,
        endpoint_configured=normalized_endpoint is not None,
        endpoint_verified=apply_verified,
    )

    if apply_verified:
        # Fully validate the FINAL manifest BEFORE mutating any file — the
        # conformance-test scaffold below or the transactional three. A
        # validation failure here aborts with nothing written.
        validate_connect_manifest(manifest)

    # The conformance-test scaffold is a regenerated artifact, written on every
    # run regardless of --apply.
    test_rel_path = write_conformance_tests(root, tool_name, contract)

    if apply_verified:
        # SPEC.md + mcp-config.json + the connect manifest are committed as ONE
        # rollback-aware transaction. An in-process failure (or a failed
        # post-apply endpoint-binding re-read) rolls every already-applied file
        # back to its prior bytes/mode/existence and raises ConnectApplyError
        # (or, if a rollback also fails, ConnectInconsistentStateError) — so the
        # three never diverge and the prior manifest survives. No
        # atomic_write_json runs after the commit.
        _commit_verified_apply(
            root, spec_path, mcp_config_path, manifest_full_path,
            tool_name, contract, generated_at, new_mcp_data, manifest,
            normalized_endpoint,
        )
        integration_state = "real-verified"
    else:
        # apply=False / failed / unverified: the evidence manifest is validated
        # and emitted atomically on its own (nothing else is mutated).
        write_connect_manifest(manifest_full_path, manifest)

    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "contract": contract,
        "conformance": conformance,
        "integration_state": integration_state,
        "target_state": target_state,
        "apply_plan": apply_plan,
        "changed_paths": changed_paths,
        "test_path": test_rel_path,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_json_arg(path):
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="connect.py",
        description="threadlight-connect — evidence-based mock-to-real integration swap.",
    )
    parser.add_argument("--project-root", default=".", help="generated project root")
    parser.add_argument("--tool-name", required=True)
    parser.add_argument("--tool-source-file", required=True, help="path to the tool's Python source")
    parser.add_argument("--sample-file", required=True, help="JSON mock sample record the tool source reads")
    parser.add_argument(
        "--real-response-file", required=True,
        help='captured real response, JSON {"items": [...]} (or a bare list)',
    )
    parser.add_argument("--obo-evidence-file", default=None, help="JSON OBO evidence (present/user_scoped)")
    parser.add_argument("--role-evidence-file", default=None, help="JSON required-role revalidation evidence")
    parser.add_argument("--current-agent-identity", default=None)
    parser.add_argument(
        "--real-endpoint", default=None,
        help=(
            "the real MCP endpoint URL to bind servers[<tool-name>].url to. "
            "Optional for a dry run (evidence assessment only); REQUIRED with "
            "--apply once the swap verifies. Must be an https URL (http only for "
            "localhost/127.0.0.1) with no embedded credentials/SAS/token and no "
            "mock marker. Persisted only in mcp-config.json — never in the "
            "connect manifest."
        ),
    )
    parser.add_argument(
        "--evidence-captured-at", default=None,
        help=(
            "ISO-8601 timestamp the real evidence was captured at; recorded as "
            "freshness.source_oldest_at. Omit when unknown (recorded as null — "
            "never faked from the run's own generated_at)."
        ),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="write SPEC + mcp-config when fully verified (default: dry-run plan only)",
    )
    parser.add_argument("--spec-path", default=DEFAULT_SPEC_PATH)
    parser.add_argument("--mcp-config-path", default=DEFAULT_MCP_CONFIG_PATH)
    parser.add_argument("--manifest-path", default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--json", action="store_true", help="print the manifest JSON to stdout")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        result = run_connect(
            project_root=args.project_root,
            tool_name=args.tool_name,
            tool_source=Path(args.tool_source_file).read_text(encoding="utf-8"),
            sample=_load_json_arg(args.sample_file),
            real_response=_load_json_arg(args.real_response_file),
            obo_evidence=_load_json_arg(args.obo_evidence_file),
            role_evidence=_load_json_arg(args.role_evidence_file),
            apply=args.apply,
            real_endpoint=args.real_endpoint,
            spec_path=args.spec_path,
            mcp_config_path=args.mcp_config_path,
            manifest_path=args.manifest_path,
            current_agent_identity=args.current_agent_identity,
            evidence_captured_at=args.evidence_captured_at,
        )
    except ConnectEvidenceError as exc:
        print(f"error: malformed evidence — {exc} (nothing written)", file=sys.stderr)
        return 2
    except ConnectInconsistentStateError as exc:
        # Rollback failed: SPEC.md / mcp-config.json may be divergent. Report
        # the unreconciled paths honestly — do NOT claim nothing changed.
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except (json.JSONDecodeError, SyntaxError) as exc:
        # Unparseable CLI input — malformed JSON in a --*-file argument, or a
        # tool source that isn't valid Python. Surface a clean one-line error
        # (no traceback), a stable nonzero code, and write nothing: these are
        # raised during parsing, before any artifact is produced.
        print(f"error: could not parse input — {exc} (nothing written)", file=sys.stderr)
        return 5
    except (ConnectApplyError, ManifestValidationError, OSError) as exc:
        # A recoverable apply failure rolls the transaction back, a manifest
        # validation failure aborts before mutating the three integration files,
        # and any bare OSError here comes from outside the transaction — so
        # SPEC.md / mcp-config.json / the connect manifest are all at their prior
        # bytes. Claim ONLY that (a regenerated conformance-test scaffold may
        # have been rewritten); never the inaccurate blanket "no files changed".
        print(
            f"error: {exc} (SPEC.md, mcp-config.json, and the connect manifest "
            "were left unchanged)",
            file=sys.stderr,
        )
        return 3

    if args.json:
        print(json.dumps(result["manifest"], indent=2, sort_keys=True))
        return 0

    print(f"integration_state: {result['integration_state']}")
    print(f"target_state: {result['target_state']}")
    print(f"conformance passed: {result['conformance']['passed']}")
    for diff in result["conformance"]["differences"]:
        print(f"  drift: {diff['field']} expected={diff['expected']} actual={diff['actual']} path={diff['path']}")
    print("apply plan:")
    for step in result["apply_plan"]:
        print(f"  [{step['action']}] {step['path']}: {step['description']}")
    if result["changed_paths"]:
        print("changed paths:")
        for changed in result["changed_paths"]:
            print(f"  - {changed}")
    print(f"conformance tests written: {result['test_path']}")
    print(f"manifest written: {result['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
