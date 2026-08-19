"""
Read-only Azure source adapters for the cost-actuals reconciliation pipeline
— the thin, injectable CLI boundary that feeds `cost_actuals.py`'s pure,
offline parsers with real evidence.

See `docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`
§8.1 (window/daily-granularity contract), §8.2 (interaction query surface),
§11 (CLI shape, workspace identity resolution) and §11.1 (rate limiting) for
the RFC this module implements.

## This module *is* the Azure boundary — and nothing else is

`cost_actuals.py` and `token_evidence.py` are deliberately pure: they parse
shapes an Azure call already returned and never touch the network. This
module is the only place in `threadlight-consumption-iq` allowed to reach
Azure, and it does exactly one thing — fetch raw evidence documents. It does
not parse, aggregate, or interpret them: `collect_sources` returns the raw
page/metric/query documents so the existing offline parsers (and Task 11's
token attribution) stay exhaustively testable without credentials.

Every command is issued through an injected `Runner` receiving a fully-formed
`argv` **list**, never a shell string. The shell is never involved, nothing
is interpolated into a command line, and a non-zero exit is inspected and
classified here rather than raised as a stack trace carrying the command
line. Tests inject a fake runner and therefore never call Azure.

## Read-only, always

The complete command surface is:

- `az account show` — identity assertion;
- `az rest --method post` against the Cost Management **Query** endpoint (a
  POST-shaped *read*: the API takes its query in a request body, it does not
  mutate anything);
- `az monitor metrics list`;
- `az monitor log-analytics workspace show`;
- `az monitor log-analytics query`.

Nothing creates, updates, or deletes anything. There is no authentication
fallback either: if the ambient `az` login is not already the right
subscription, the run fails rather than "helpfully" logging in or switching
context on an operator's behalf.

## `AZURE_CONFIG_DIR` is a precondition, not a nicety

Every `az` invocation is gated on a non-empty `AZURE_CONFIG_DIR`. Concurrent
sessions against different tenants share one machine, and an unisolated
`az` config directory is exactly how a query lands in the wrong customer's
subscription. Failing before the first call is the only safe default; the
guard is checked in each entry point rather than once at import so it cannot
be bypassed by calling a helper directly.

## Fail closed on cost, degrade on everything else

Cost Management is the authoritative total (RFC §9.1), so every failure
there raises `ActualsSourceError` and aborts the bundle. Token metrics and
interaction counts are attribution evidence: their failure produces a
distinct warning and a `None`, and the cost pages are still returned. That
asymmetry is the whole point — a missing token document must never suppress
a total the operator can act on, and a broken total must never be silently
replaced by a partial one.

## `nextLink` is untrusted input

A pagination link is a URL handed to us by a remote service and then fed
straight back into an authenticated request. It is validated against the
request it continues — HTTPS only, host exactly `management.azure.com` (no
port, no userinfo), and the same subscription/resource-group/Query path —
and a repeated link is rejected rather than followed into an infinite loop.
Anything else would let a compromised or buggy response redirect an
authenticated call to a foreign host or downgrade it to plaintext.

## Errors never carry the request body or a token

`ActualsSourceError` messages carry a bounded, whitespace-collapsed slice of
stderr with JWT/bearer material redacted and the serialized request body
scrubbed out. Error text ends up in logs and support bundles; the query body
is noise there and a token is a credential leak.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date, datetime
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit


class ActualsSourceError(RuntimeError):
    """A required read-only Azure source could not be collected or does not
    validate. Raised for mandatory (Cost Management) evidence; optional
    evidence degrades into a warning instead."""


#: A runner receives a fully-formed ``argv`` list (``["az", ...]``) and
#: returns the completed process. Injected everywhere so tests never spawn a
#: process and never call Azure.
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

# Pinned Cost Management Query API contract (RFC §8, §9.7).
COST_API_VERSION = "2025-03-01"

ARM_HOST = "management.azure.com"

# The only query parameter Cost Management may add to a `nextLink`: an
# opaque continuation cursor.
_SKIPTOKEN_KEY = "$skiptoken"

# Bounded exponential backoff for observed 429/5xx only (RFC §11.1). The
# `Retry-After` header is deliberately not read: `az rest` does not surface
# response headers, so honoring it would be a fiction only a fake could test.
RETRY_DELAYS_SECONDS = (2, 4, 8)

# A defensive ceiling on pagination. Cost Management pages a daily,
# single-resource-group window in a handful of responses; anything beyond
# this is a loop or a service bug, not evidence.
MAX_COST_PAGES = 50

# Bounded so a multi-megabyte CLI error cannot flood a log or a report.
MAX_STDERR_CHARS = 400

# Transient classification reads a *prefix* of each stream. A throttling or
# gateway banner is the first thing `az` prints; scanning an unbounded blob
# for three digits only adds CPU burn on every attempt of every retry.
MAX_CLASSIFY_CHARS = 8000

# `az monitor metrics list` maps to the Metrics `List` REST API, whose `top`
# parameter documents: "the maximum number of records to retrieve — valid
# only if `$filter` is specified. Defaults to 10." This query is always
# filtered, so leaving `top` unset silently truncates a week of hourly,
# multi-metric, multi-deployment series to ten records — which looks exactly
# like a quiet account rather than like missing evidence.
TOKEN_METRICS_MAX_RECORDS = "10000"

_DEFAULT_TIMEOUT_SECONDS = 120

# `re.ASCII` throughout: without it `\d`/`\w` also match non-ASCII digits and
# letters, so a homoglyph subscription ID or resource-group name could pass a
# check that was meant to be plain-ASCII only.
_GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.ASCII | re.IGNORECASE,
)
# Azure resource-group grammar: alphanumerics, underscore, parentheses,
# hyphen and period, 1-90 chars, and may not end in a period. Deliberately
# ASCII-only (Azure itself allows some Unicode letters) because this value is
# interpolated into a URL we then authenticate against — narrower is safer,
# and a name outside this set is rejected loudly rather than escaped quietly.
_RESOURCE_GROUP_RE = re.compile(r"[A-Za-z0-9_()\-.]{1,90}", re.ASCII)
_PROVIDER_NAMESPACE_RE = re.compile(r"[A-Za-z0-9.]{1,64}", re.ASCII)
_RESOURCE_SEGMENT_RE = re.compile(r"[A-Za-z0-9._()\-]{1,128}", re.ASCII)

# Redacted from any stderr we surface. JWTs are the realistic leak vector in
# an `az` error (an echoed Authorization header); the bearer form catches
# opaque tokens too.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]*)?")
_BEARER_RE = re.compile(r"(?i)bearer\s+\S+")

# Keys that only ever appear in *our* request body, used to recognize an
# echoed (possibly reserialized) body inside an error payload.
_BODY_MARKERS = ("PreTaxCost", "granularity", "timePeriod", "grouping", "totalCost")

# Transient classification is best effort by construction: `az rest` reports
# failures as formatted text, so an HTTP status can only be recognized, not
# read. Recognized codes and their canonical phrasings are matched; anything
# unrecognized is treated as non-transient and surfaces immediately rather
# than burning three sleeps on a permission error.
#
# The boundaries exclude letters, digits *and* hyphens on both sides so a
# status code is only recognized as a standalone token. Error prose is full
# of identifiers that merely contain these digits — a correlation GUID
# `b429ff31-...`, a deployment named `aoai-500-prod`, an operation id
# `8503abcd` — and retrying those costs three sleeps and three more
# authenticated calls before surfacing the same permanent error.
_TRANSIENT_STATUS_RE = re.compile(
    r"(?<![0-9A-Za-z\-])(?:429|500|502|503|504)(?![0-9A-Za-z\-])", re.ASCII
)
_TRANSIENT_PHRASES = (
    "too many requests",
    "toomanyrequests",
    "throttl",
    "internal server error",
    "internalservererror",
    "service unavailable",
    "serviceunavailable",
    "bad gateway",
    "badgateway",
    "gateway timeout",
    "gatewaytimeout",
    "server timeout",
    "servertimeout",
)


# ---------------------------------------------------------------------------
# Default runner
# ---------------------------------------------------------------------------

def _default_runner(args: list[str]) -> "subprocess.CompletedProcess[str]":
    """Run `az` with no shell, bounded time, and no raise-on-exit-code.

    Raising on a non-zero exit is deliberately avoided: `CalledProcessError`
    renders the full command line (including the request body) into its
    message, which is exactly the payload this module promises never to
    surface. Exit codes are classified by the callers instead.
    """
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise ActualsSourceError(
            "the Azure CLI (`az`) is not installed or not on PATH"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ActualsSourceError(
            f"`az {args[1] if len(args) > 1 else ''}` timed out after "
            f"{_DEFAULT_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise ActualsSourceError("could not execute the Azure CLI (`az`)") from exc


def _resolve_runner(runner: Optional[Runner]) -> Runner:
    return runner if runner is not None else _default_runner


# ---------------------------------------------------------------------------
# Guards and validation
# ---------------------------------------------------------------------------

def _require_azure_config_dir() -> str:
    """Tenant isolation precondition — checked before *every* `az` call."""
    value = os.environ.get("AZURE_CONFIG_DIR")
    if not isinstance(value, str) or not value.strip():
        raise ActualsSourceError(
            "AZURE_CONFIG_DIR is not set; refusing to run `az` against a shared "
            "CLI config directory (per-tenant isolation is required)"
        )
    return value


def _validate_subscription_id(value: object) -> str:
    if not isinstance(value, str) or not _GUID_RE.fullmatch(value):
        raise ActualsSourceError("subscription id is not a well-formed GUID")
    return value


def _validate_resource_group(value: object) -> str:
    if (
        not isinstance(value, str)
        or not _RESOURCE_GROUP_RE.fullmatch(value)
        or value.endswith(".")
    ):
        raise ActualsSourceError("resource group is not a well-formed Azure name")
    return value


def _validate_window(start: object, end: object) -> tuple[date, date]:
    """Require plain `date` boundaries with `start < end`.

    `datetime` is rejected rather than coerced: it is a `date` subclass, so
    accepting it would silently drop a time-of-day (and possibly a non-UTC
    tzinfo) from a window whose whole contract is UTC midnight boundaries.
    """
    for label, value in (("start", start), ("end", end)):
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ActualsSourceError(f"{label} must be a datetime.date (UTC day)")
    if not start < end:  # type: ignore[operator]
        raise ActualsSourceError("window start must be strictly before window end")
    return start, end  # type: ignore[return-value]


def _parse_resource_id(value: object) -> dict[str, Any]:
    """Validate a management-plane ARM resource ID and split it.

    Rejects anything that is not an absolute
    `/subscriptions/{guid}/resourceGroups/{rg}/providers/{ns}/{type}/{name}...`
    path: a URL, a bare name, a workspace GUID, whitespace/newlines, `..`
    traversal, and empty or shell-significant segments.
    """
    if not isinstance(value, str) or not value:
        raise ActualsSourceError("resource id is not a string")
    if value.strip() != value or any(ch.isspace() for ch in value):
        raise ActualsSourceError("resource id contains whitespace")
    if ".." in value:
        raise ActualsSourceError("resource id contains a path traversal segment")

    parts = value.split("/")
    if len(parts) < 9 or parts[0] != "":
        raise ActualsSourceError("resource id is not a management-plane ARM id")
    if parts[1].casefold() != "subscriptions" or parts[3].casefold() != "resourcegroups":
        raise ActualsSourceError("resource id is not a management-plane ARM id")
    if parts[5].casefold() != "providers":
        raise ActualsSourceError("resource id is not a management-plane ARM id")

    subscription_id = _validate_subscription_id(parts[2])
    resource_group = _validate_resource_group(parts[4])
    if not _PROVIDER_NAMESPACE_RE.fullmatch(parts[6]):
        raise ActualsSourceError("resource id has a malformed provider namespace")

    tail = parts[7:]
    if len(tail) < 2 or len(tail) % 2 != 0:
        raise ActualsSourceError("resource id has an incomplete type/name pair")
    for segment in tail:
        if not _RESOURCE_SEGMENT_RE.fullmatch(segment):
            raise ActualsSourceError("resource id has a malformed segment")

    return {
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "namespace": parts[6],
        "segments": tail,
    }


# ---------------------------------------------------------------------------
# Error text hygiene
# ---------------------------------------------------------------------------

def _strip_echoed_json_objects(text: str, markers: tuple[str, ...]) -> str:
    """Remove any embedded JSON object that carries a request-body marker.

    A literal substring replacement is not enough on its own: a service (or
    the CLI) may echo the request body back *reserialized* — different key
    order, different separators, pretty-printed — so the bytes we sent are
    not the bytes that come back. This walks brace depth instead and drops
    whole objects that mention a body-only key, which leaves genuinely
    useful error JSON (`{"error": {"code": ...}}`) intact.
    """
    if not markers or "{" not in text:
        return text
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != "{":
            out.append(char)
            index += 1
            continue
        depth = 0
        end = index
        while end < length:
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        candidate = text[index:end]
        if any(marker in candidate for marker in markers):
            out.append("[request body omitted]")
        else:
            out.append(candidate)
        index = end
    return "".join(out)


def _sanitize_stderr(text: object, *, scrub: tuple[str, ...] = ()) -> str:
    """Bounded, credential-free stderr suitable for a log or a report."""
    if not isinstance(text, str) or not text.strip():
        return ""
    cleaned = text
    for secret in scrub:
        if secret:
            cleaned = cleaned.replace(secret, "[request body omitted]")
    cleaned = _strip_echoed_json_objects(cleaned, _BODY_MARKERS)
    cleaned = _JWT_RE.sub("[redacted]", cleaned)
    cleaned = _BEARER_RE.sub("Bearer [redacted]", cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > MAX_STDERR_CHARS:
        cleaned = cleaned[:MAX_STDERR_CHARS] + "..."
    return cleaned


def _is_transient(*streams: object) -> bool:
    """Classify a failure from a bounded prefix of stderr *and* stdout.

    `az` does not put its failure text on one predictable stream — some
    error paths print the service response to stdout and only a summary to
    stderr — so both are inspected, each bounded to `MAX_CLASSIFY_CHARS`.
    """
    for stream in streams:
        if not isinstance(stream, str) or not stream:
            continue
        head = stream[:MAX_CLASSIFY_CHARS]
        if _TRANSIENT_STATUS_RE.search(head):
            return True
        lowered = head.casefold()
        if any(phrase in lowered for phrase in _TRANSIENT_PHRASES):
            return True
    return False


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def assert_azure_context(
    subscription_id: str,
    *,
    runner: Optional[Runner] = None,
) -> dict[str, str]:
    """Assert the ambient `az` login is already the requested subscription.

    There is no fallback: this never runs `az login`, never runs
    `az account set`, and never silently proceeds against whatever happens
    to be active. Comparison is case-insensitive because a GUID's hex casing
    carries no meaning, but it is exact otherwise.
    """
    _require_azure_config_dir()
    requested = _validate_subscription_id(subscription_id)
    run = _resolve_runner(runner)

    args = [
        "az", "account", "show",
        "--query", "{id:id,tenantId:tenantId}",
        "-o", "json",
    ]
    completed = _invoke(run, args, context="az account show")
    if completed.returncode != 0:
        raise ActualsSourceError(
            "az account show failed (is the CLI logged in for this "
            f"AZURE_CONFIG_DIR?): {_sanitize_stderr(completed.stderr)}"
        )

    doc = _load_json_object(completed.stdout, context="az account show")
    active = doc.get("id")
    tenant = doc.get("tenantId")
    if not isinstance(active, str) or not _GUID_RE.fullmatch(active):
        raise ActualsSourceError("az account show returned no usable subscription id")
    if not isinstance(tenant, str) or not tenant.strip():
        raise ActualsSourceError("az account show returned no usable tenant id")
    if active.casefold() != requested.casefold():
        raise ActualsSourceError(
            "active az subscription does not match the requested subscription; "
            "refusing to query (no automatic subscription switch)"
        )
    return {"subscription_id": active, "tenant_id": tenant}


def _invoke(
    run: Runner,
    args: list[str],
    *,
    context: str,
) -> "subprocess.CompletedProcess[str]":
    """Call an injected runner, mapping transport failures to our error type."""
    try:
        return run(list(args))
    except ActualsSourceError:
        raise
    except Exception as exc:  # noqa: BLE001 - transport failures are opaque
        raise ActualsSourceError(f"{context} could not be executed") from exc


def _load_json_object(stdout: object, *, context: str) -> dict[str, Any]:
    doc = _load_json(stdout, context=context)
    if not isinstance(doc, dict):
        raise ActualsSourceError(f"{context} returned a non-object JSON document")
    return doc


def _load_json(stdout: object, *, context: str) -> Any:
    if not isinstance(stdout, str) or not stdout.strip():
        raise ActualsSourceError(f"{context} returned an empty response")
    try:
        return json.loads(stdout)
    except (ValueError, TypeError) as exc:
        raise ActualsSourceError(f"{context} returned malformed JSON") from exc


# ---------------------------------------------------------------------------
# Cost Management
# ---------------------------------------------------------------------------

def _utc_midnight(value: date) -> str:
    return f"{value.isoformat()}T00:00:00Z"


def cost_query_body(start: date, end: date) -> dict[str, Any]:
    """Build the `2025-03-01` `Usage` Query request body (RFC §8.1, §9.7).

    `UsageDate` is *not* a grouping dimension: `Daily` granularity emits it
    as its own column, and requesting it as a dimension would be a different
    (and rejected) query shape. A fresh dict is returned on every call so a
    caller mutating a body cannot poison the next request.
    """
    start, end = _validate_window(start, end)
    return {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": {"from": _utc_midnight(start), "to": _utc_midnight(end)},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ResourceId"},
                {"type": "Dimension", "name": "ResourceType"},
                {"type": "Dimension", "name": "ServiceName"},
            ],
        },
    }


def _cost_query_url(subscription_id: str, resource_group: str) -> str:
    return (
        f"https://{ARM_HOST}/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CostManagement/query"
        f"?api-version={COST_API_VERSION}"
    )


def _expected_query_path(subscription_id: str, resource_group: str) -> str:
    return (
        f"/subscriptions/{subscription_id}/resourcegroups/{resource_group}"
        "/providers/microsoft.costmanagement/query"
    ).casefold()


def _validate_next_link(
    link: object,
    *,
    subscription_id: str,
    resource_group: str,
    seen: set[str],
) -> str:
    """Validate a service-supplied pagination URL before authenticating to it.

    The query string is validated too, not just the host and path: a
    `nextLink` is a *continuation* of the request we issued, so it must
    carry the same pinned `api-version` and add nothing but the opaque
    `$skiptoken`. A link that quietly downgrades the API version or bolts on
    an extra parameter changes the shape of the evidence mid-pagination —
    the remaining pages would still parse, and the total would still be
    wrong.
    """
    if not isinstance(link, str) or isinstance(link, bool) or not link.strip():
        raise ActualsSourceError("Cost Management nextLink is not a usable URL")
    if link in seen:
        raise ActualsSourceError("Cost Management nextLink repeats a page already read")

    parts = urlsplit(link)
    if parts.scheme != "https":
        raise ActualsSourceError("Cost Management nextLink is not HTTPS")
    if parts.netloc.casefold() != ARM_HOST:
        raise ActualsSourceError("Cost Management nextLink points at a foreign host")
    if ".." in parts.path:
        raise ActualsSourceError("Cost Management nextLink path contains traversal")
    if parts.path.casefold() != _expected_query_path(subscription_id, resource_group):
        raise ActualsSourceError(
            "Cost Management nextLink leaves the requested scope or endpoint"
        )
    _validate_next_link_query(parts.query)
    return link


def _validate_next_link_query(query: str) -> None:
    """Require exactly the pinned API version, plus at most a `$skiptoken`."""
    # `keep_blank_values` so `?$skiptoken=` is seen and rejected rather than
    # silently dropped and treated as "no skiptoken at all".
    fields = parse_qs(query, keep_blank_values=True, strict_parsing=False)

    versions = fields.pop("api-version", None)
    if versions != [COST_API_VERSION]:
        raise ActualsSourceError(
            "Cost Management nextLink does not carry the pinned api-version"
        )

    skiptokens = fields.pop(_SKIPTOKEN_KEY, None)
    if skiptokens is not None and (
        len(skiptokens) != 1 or not skiptokens[0].strip()
    ):
        raise ActualsSourceError("Cost Management nextLink has an unusable skiptoken")
    if fields:
        raise ActualsSourceError(
            "Cost Management nextLink carries unexpected query parameters"
        )


def _validate_max_pages(value: object) -> int:
    """A positive `int` page ceiling — `bool` is not a page count.

    Validated before any `az` call so a caller passing `0` (or `True`,
    which is an `int` in Python and would otherwise mean "one page") fails
    on its own terms rather than after an authenticated round trip.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ActualsSourceError("max_pages must be a positive integer")
    return value


def fetch_cost_pages(
    subscription_id: str,
    resource_group: str,
    start: date,
    end: date,
    *,
    runner: Optional[Runner] = None,
    sleep: Optional[Callable[[float], Any]] = None,
    max_pages: int = MAX_COST_PAGES,
) -> list[dict[str, Any]]:
    """Fetch every Cost Management `Usage` page for the resource-group scope.

    Mandatory evidence: any failure raises `ActualsSourceError` rather than
    returning a short page list, because a silently truncated page set is a
    silently wrong total.

    Identity is asserted here rather than left to the caller. This is a
    public entry point, and a version that trusts its caller to have run
    `assert_azure_context` first is one direct call away from billing data
    for whatever subscription happens to be active in the ambient login.
    """
    _, pages = _assert_and_fetch_cost_pages(
        subscription_id,
        resource_group,
        start,
        end,
        runner=runner,
        sleep=sleep,
        max_pages=max_pages,
    )
    return pages


def _assert_and_fetch_cost_pages(
    subscription_id: str,
    resource_group: str,
    start: date,
    end: date,
    *,
    runner: Optional[Runner],
    sleep: Optional[Callable[[float], Any]],
    max_pages: int,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Assert identity, then paginate — returning both.

    `collect_sources` needs the asserted context in its bundle *and* must
    not pay for a second `az account show` to get it, so the assertion
    happens exactly once, here, and its result is handed back rather than
    re-derived.
    """
    _require_azure_config_dir()
    subscription_id = _validate_subscription_id(subscription_id)
    resource_group = _validate_resource_group(resource_group)
    max_pages = _validate_max_pages(max_pages)
    body = json.dumps(cost_query_body(start, end), separators=(",", ":"), sort_keys=True)
    run = _resolve_runner(runner)

    context = assert_azure_context(subscription_id, runner=run)

    url = _cost_query_url(subscription_id, resource_group)
    seen: set[str] = set()
    pages: list[dict[str, Any]] = []

    while True:
        page = _fetch_cost_page(run, url, body, sleep=sleep)
        pages.append(page)
        seen.add(url)

        properties = page.get("properties")
        next_link = properties.get("nextLink") if isinstance(properties, dict) else None
        if next_link is None:
            return context, pages
        if len(pages) >= max_pages:
            raise ActualsSourceError(
                f"Cost Management pagination exceeded {max_pages} pages"
            )
        url = _validate_next_link(
            next_link,
            subscription_id=subscription_id,
            resource_group=resource_group,
            seen=seen,
        )


def _fetch_cost_page(
    run: Runner,
    url: str,
    body: str,
    *,
    sleep: Optional[Callable[[float], Any]],
) -> dict[str, Any]:
    args = [
        "az", "rest",
        "--method", "post",
        "--url", url,
        "--body", body,
        "--output", "json",
    ]
    napper = sleep if sleep is not None else _sleep_default
    attempts = len(RETRY_DELAYS_SECONDS) + 1

    for attempt in range(attempts):
        completed = _invoke(run, args, context="Cost Management query")
        if completed.returncode == 0:
            return _load_json_object(completed.stdout, context="Cost Management query")

        detail = _sanitize_stderr(completed.stderr, scrub=(body,))
        if not _is_transient(completed.stderr, completed.stdout):
            raise ActualsSourceError(f"Cost Management query failed: {detail}")
        if attempt == attempts - 1:
            raise ActualsSourceError(
                "Cost Management query still throttled or unavailable after "
                f"{attempts} attempts: {detail}"
            )
        napper(RETRY_DELAYS_SECONDS[attempt])

    raise ActualsSourceError("Cost Management query failed")  # pragma: no cover


def _sleep_default(seconds: float) -> None:
    import time

    time.sleep(seconds)


# ---------------------------------------------------------------------------
# Azure Monitor token metrics
# ---------------------------------------------------------------------------

def fetch_token_metrics(
    monitor_resource_id: str,
    subscription_id: str,
    start: date,
    end: date,
    *,
    runner: Optional[Runner] = None,
) -> dict[str, Any]:
    """Fetch the raw `az monitor metrics list` token document.

    Returned verbatim: this module never parses token counters (Task 11 and
    `token_evidence.py` own that), it only proves which account the series
    came from. The resource must live in the subscription being reconciled,
    so an accidental cross-subscription resource ID is a loud error rather
    than token evidence quietly attributed to the wrong account.

    `--top` is not optional here. The command maps to the Azure Monitor
    Metrics `List` API, whose `top` parameter is documented as "the maximum
    number of records to retrieve — valid only if `$filter` is specified.
    Defaults to 10." This query always passes `--filter`, so without an
    explicit cap a week of hourly, three-metric, multi-deployment series
    comes back truncated to ten records — evidence that parses cleanly and
    undercounts silently.
    """
    _require_azure_config_dir()
    subscription_id = _validate_subscription_id(subscription_id)
    parsed = _parse_resource_id(monitor_resource_id)
    if parsed["subscription_id"].casefold() != subscription_id.casefold():
        raise ActualsSourceError(
            "token metrics resource lives in a different subscription than the "
            "reconciled scope"
        )
    start, end = _validate_window(start, end)

    args = [
        "az", "monitor", "metrics", "list",
        "--resource", monitor_resource_id,
        "--metrics", "InputTokens", "OutputTokens", "CachedInputTokens",
        "--start-time", _utc_midnight(start),
        "--end-time", _utc_midnight(end),
        "--interval", "PT1H",
        "--aggregation", "Total",
        "--filter", "ModelDeploymentName eq '*' and ModelName eq '*'",
        "--top", TOKEN_METRICS_MAX_RECORDS,
        "-o", "json",
    ]
    completed = _invoke(_resolve_runner(runner), args, context="token metrics query")
    if completed.returncode != 0:
        raise ActualsSourceError(
            "token metrics query failed: " + _sanitize_stderr(completed.stderr)
        )
    return _load_json_object(completed.stdout, context="token metrics query")


# ---------------------------------------------------------------------------
# Log Analytics workspace + interaction query
# ---------------------------------------------------------------------------

def resolve_workspace_customer_id(
    resource_id: str,
    runner: Optional[Runner] = None,
) -> Optional[str]:
    """Resolve an ARM workspace ID to the `customerId` GUID the query needs.

    Never raises. Interaction evidence is optional (RFC §11): a blank,
    malformed, non-GUID, failed, or unavailable resolution degrades the
    interaction status only and must never take down a Cost Management total
    that already succeeded.
    """
    try:
        _require_azure_config_dir()
        _parse_resource_id(resource_id)
    except ActualsSourceError:
        return None

    args = [
        "az", "monitor", "log-analytics", "workspace", "show",
        "--ids", resource_id,
        "--query", "customerId",
        "-o", "tsv",
    ]
    try:
        completed = _resolve_runner(runner)(list(args))
    except Exception:  # noqa: BLE001 - optional evidence never raises
        return None

    if getattr(completed, "returncode", 1) != 0:
        return None
    stdout = getattr(completed, "stdout", None)
    if not isinstance(stdout, str):
        return None
    candidate = stdout.strip()
    if not _GUID_RE.fullmatch(candidate):
        return None
    return candidate


def _usable_kql(kql: object) -> bool:
    """A KQL string the CLI can actually carry: non-empty, NUL-free text."""
    return isinstance(kql, str) and bool(kql.strip()) and "\x00" not in kql


def fetch_interaction_result(
    workspace_resource_id: str,
    kql: str,
    *,
    runner: Optional[Runner] = None,
) -> Optional[Any]:
    """Run the caller-built success KQL against a workspace, by ARM ID.

    The public surface accepts only an ARM resource ID; the workspace
    `customerId` is resolved internally and is never an API input, so an
    operator cannot accidentally paste a GUID from an unrelated workspace
    into a query that would then silently succeed against it.

    An unusable KQL string short-circuits *before* the resolution call: an
    empty or non-string query is a caller bug, and spending an Azure round
    trip to discover it would only make that bug look like a service
    failure.
    """
    if not _usable_kql(kql):
        return None
    customer_id = resolve_workspace_customer_id(workspace_resource_id, runner=runner)
    if customer_id is None:
        return None
    return _query_workspace(customer_id, kql, runner=runner)


def _query_workspace(
    customer_id: str,
    kql: object,
    *,
    runner: Optional[Runner] = None,
) -> Optional[Any]:
    """Internal: run KQL against an already-resolved `customerId`.

    Private on purpose — see `fetch_interaction_result` for why a workspace
    GUID is never part of the public surface.
    """
    if not _usable_kql(kql):
        return None
    args = [
        "az", "monitor", "log-analytics", "query",
        "--workspace", customer_id,
        "--analytics-query", kql,
        "--output", "json",
    ]
    try:
        completed = _resolve_runner(runner)(list(args))
    except Exception:  # noqa: BLE001 - optional evidence never raises
        return None
    if getattr(completed, "returncode", 1) != 0:
        return None
    try:
        doc = _load_json(getattr(completed, "stdout", None), context="interaction query")
        return _normalize_log_analytics_result(doc)
    except ActualsSourceError:
        return None


# ---------------------------------------------------------------------------
# Log Analytics CLI shape normalization
# ---------------------------------------------------------------------------

#: The CLI tags every flattened row with the table it came from.
_TABLE_NAME_KEY = "TableName"

#: The `summarize` projection `build_success_kql` emits. Used only to
#: canonicalize an *empty* result, where the CLI's flattening leaves no row
#: to recover column names from.
_INTERACTION_SUMMARY_COLUMNS = ("total_interactions", "successful_interactions")

#: Every cell the CLI emits is `str(value)`, so the canonical document
#: declares its columns as strings rather than inventing a Kusto type.
_CLI_COLUMN_TYPE = "string"


def _normalize_log_analytics_result(doc: object) -> dict[str, Any]:
    """Convert `az monitor log-analytics query -o json` output into the
    canonical `{"tables": [{"name", "columns", "rows"}]}` document that
    `cost_actuals.parse_interaction_counts` accepts.

    The CLI does not print the service's `tables[]` response. The
    `log-analytics` extension overrides the command's output: in
    `Azure/azure-cli-extensions`,
    `src/log-analytics/azext_loganalytics/custom.py`, `Query._output` walks
    `result['tables']` and, for each row of each table, emits one
    `OrderedDict` whose first key is `TableName` and whose remaining keys
    are the column names mapped to `str(value)`. Every table is concatenated
    into a single flat list, so what reaches stdout is
    `[{"TableName": ..., "<column>": "<stringified cell>", ...}, ...]`.

    `parse_interaction_counts` parses the *service* shape and explicitly
    refuses a list of dicts, so the raw CLI list can never be evidence.
    Normalizing here — the one place that knows which CLI produced these
    bytes — is what lets that parser stay pure and offline-testable.

    Rules:

    - a `dict` that already carries a non-empty `tables` list is returned
      untouched (defensive: a future CLI, or an `az rest` transport, may
      return the service shape directly);
    - an empty list is the flattening of a `summarize` that matched
      nothing. There are no rows to recover column names from, so it
      canonicalizes to a `PrimaryResult` table declaring this query's two
      summary columns with zero rows — a *real observation of zero*, which
      the parser reads as `(0, 0)`;
    - otherwise rows are grouped by `TableName`, preserving first-seen
      column order (excluding the tag) and first-seen table order;
    - anything malformed or ambiguous raises rather than guessing: a row
      that is not an object, a missing/blank/non-string `TableName`, a row
      with no columns at all, duplicate column names differing only in
      case, or rows of the same table disagreeing on their column set.
      Interaction evidence is optional, so the caller turns that into a
      skipped source and a warning — never into a wrong number.
    """
    if isinstance(doc, dict):
        tables = doc.get("tables")
        if not isinstance(tables, list) or not tables:
            raise ActualsSourceError(
                "interaction query returned an object that is not a tables document"
            )
        return doc

    if not isinstance(doc, list):
        raise ActualsSourceError("interaction query returned an unrecognized document")

    if not doc:
        return {
            "tables": [
                {
                    "name": "PrimaryResult",
                    "columns": [
                        {"name": name, "type": _CLI_COLUMN_TYPE}
                        for name in _INTERACTION_SUMMARY_COLUMNS
                    ],
                    "rows": [],
                }
            ]
        }

    grouped: dict[str, dict[str, Any]] = {}
    for entry in doc:
        if not isinstance(entry, dict):
            raise ActualsSourceError(
                "interaction query returned a row that is not an object"
            )
        name = entry.get(_TABLE_NAME_KEY)
        if not isinstance(name, str) or not name.strip():
            raise ActualsSourceError(
                "interaction query returned a row without a usable TableName"
            )

        columns = [key for key in entry if key != _TABLE_NAME_KEY]
        if not columns:
            raise ActualsSourceError(
                "interaction query returned a row with no columns"
            )
        folded = [key.casefold() for key in columns]
        if len(set(folded)) != len(folded):
            raise ActualsSourceError(
                "interaction query returned duplicate column names in one row"
            )

        bucket = grouped.setdefault(name, {"columns": columns, "folded": folded, "rows": []})
        if set(folded) != set(bucket["folded"]):
            raise ActualsSourceError(
                "interaction query returned rows with inconsistent columns for "
                "the same table"
            )
        cells = {key.casefold(): value for key, value in entry.items()}
        bucket["rows"].append([cells[key] for key in bucket["folded"]])

    return {
        "tables": [
            {
                "name": name,
                "columns": [
                    {"name": column, "type": _CLI_COLUMN_TYPE}
                    for column in table["columns"]
                ],
                "rows": table["rows"],
            }
            for name, table in grouped.items()
        ]
    }


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

def collect_sources(
    subscription_id: str,
    resource_group: str,
    start: date,
    end: date,
    *,
    monitor_resource_id: Optional[str] = None,
    workspace_resource_id: Optional[str] = None,
    kql: Optional[str] = None,
    runner: Optional[Runner] = None,
    sleep: Optional[Callable[[float], Any]] = None,
) -> dict[str, Any]:
    """Collect every read-only source into one raw evidence bundle.

    Order is deliberate: identity is asserted before any query is issued (by
    `fetch_cost_pages` itself — exactly once, not twice), the mandatory Cost
    Management pages come next (a failure aborts the whole bundle), and only
    then are the optional token and interaction sources attempted. Optional
    failures append a distinct warning and leave their slot `None` — the
    cost pages already collected are always retained.

    That retention is the point, and it extends to *input* validation: a
    malformed or out-of-scope `monitor_resource_id` / `workspace_resource_id`
    is a caller mistake about attribution evidence, so it degrades to a
    warning inside the optional branch instead of aborting a total that has
    already been fetched successfully.

    Both optional resources are additionally required to live in the
    subscription being reconciled. A foreign Azure Monitor account would
    attribute another system's tokens to this bundle, and a foreign
    workspace would divide this scope's cost by another system's interaction
    count — both produce a plausible number that is quietly about two
    unrelated systems. The scope rule lives here, in the bundle that knows
    what "the reconciled subscription" means;
    `resolve_workspace_customer_id` stays a generic helper.

    `token_source_resource_id` is recorded only when the token query
    actually succeeded, so Task 11 can bind the token series to the account
    identity it came from (PAYG vs PTU) without re-deriving it, and never
    attributes a series to a resource that produced nothing.
    """
    _require_azure_config_dir()
    subscription_id = _validate_subscription_id(subscription_id)
    resource_group = _validate_resource_group(resource_group)
    start, end = _validate_window(start, end)

    run = _resolve_runner(runner)
    warnings: list[str] = []

    context, cost_pages = _assert_and_fetch_cost_pages(
        subscription_id,
        resource_group,
        start,
        end,
        runner=run,
        sleep=sleep,
        max_pages=MAX_COST_PAGES,
    )

    token_doc: Optional[dict[str, Any]] = None
    token_source_resource_id: Optional[str] = None
    if monitor_resource_id is not None:
        try:
            _require_same_subscription(
                monitor_resource_id, subscription_id, label="token metrics resource"
            )
            token_doc = fetch_token_metrics(
                monitor_resource_id, subscription_id, start, end, runner=run
            )
            token_source_resource_id = monitor_resource_id
        except ActualsSourceError as exc:
            warnings.append(f"token metrics unavailable: {exc}")

    interaction_result: Optional[Any] = None
    if workspace_resource_id is not None and kql is not None:
        try:
            _require_same_subscription(
                workspace_resource_id, subscription_id, label="workspace"
            )
            if not _usable_kql(kql):
                raise ActualsSourceError("interaction query is not a usable KQL string")
        except ActualsSourceError as exc:
            warnings.append(f"interaction evidence skipped (workspace): {exc}")
        else:
            customer_id = resolve_workspace_customer_id(workspace_resource_id, runner=run)
            if customer_id is None:
                warnings.append(
                    "workspace identity could not be resolved from the supplied "
                    "ARM resource id; interaction evidence skipped"
                )
            else:
                interaction_result = _query_workspace(
                    customer_id, kql, runner=run
                )
                if interaction_result is None:
                    warnings.append(
                        "interaction query returned no usable result; interaction "
                        "evidence skipped"
                    )

    return {
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "azure_context": context,
        "cost_pages": cost_pages,
        "token_doc": token_doc,
        "token_source_resource_id": token_source_resource_id,
        "interaction_result": interaction_result,
        "warnings": warnings,
    }


def _require_same_subscription(
    resource_id: object,
    subscription_id: str,
    *,
    label: str,
) -> dict[str, Any]:
    """Parse an optional ARM resource id and pin it to the reconciled scope."""
    parsed = _parse_resource_id(resource_id)
    if parsed["subscription_id"].casefold() != subscription_id.casefold():
        raise ActualsSourceError(
            f"{label} lives in a different subscription than the reconciled scope"
        )
    return parsed
