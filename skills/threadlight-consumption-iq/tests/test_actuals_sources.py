"""
Tests for actuals_sources.py — the read-only Azure CLI adapter layer that
feeds `cost_actuals.py`'s pure parsers (RFC §8.2, §11, §11.1).

See `docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`
§8.1 (window/daily-granularity contract), §8.2 (interaction query surface),
§11 (CLI shape / workspace identity resolution) and §11.1 (rate limiting).

Core contract under test:
  - **Tests never call Azure.** An autouse fixture replaces
    `subprocess.run` with a hard failure, so any code path that reaches a
    real process is a test failure, not a network call.
  - Every Azure interaction goes through an *injected* runner receiving a
    fully-formed `argv` list (`["az", ...]`) — never a shell string, never
    a joined command line.
  - Only read-only commands are ever issued: `az account show`, the Cost
    Management `Usage` query (a POST-shaped read), `az monitor metrics
    list`, `az monitor log-analytics workspace show`, and
    `az monitor log-analytics query`.
  - `AZURE_CONFIG_DIR` must be set and non-empty *before* any `az` call:
    tenant isolation is a precondition, not a best effort.
  - Cost Management evidence is mandatory and fails closed
    (`ActualsSourceError`). Token metrics and interaction evidence are
    optional and degrade into distinct warnings while the cost pages are
    retained.
  - Workspace identity is always resolved from an ARM resource ID
    internally; a workspace `customerId` GUID is never accepted as API
    input.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import actuals_sources  # noqa: E402
from actuals_sources import (  # noqa: E402
    COST_API_VERSION,
    ActualsSourceError,
    Runner,
    assert_azure_context,
    collect_sources,
    cost_query_body,
    fetch_cost_pages,
    fetch_interaction_result,
    fetch_token_metrics,
    resolve_workspace_customer_id,
)

SUB = "11111111-2222-3333-4444-555555555555"
OTHER_SUB = "99999999-8888-7777-6666-555555555555"
TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
RG = "rg-threadlight-pilot"
START = date(2026, 8, 1)
END = date(2026, 8, 8)
WORKSPACE_ID = (
    f"/subscriptions/{SUB}/resourceGroups/{RG}"
    "/providers/Microsoft.OperationalInsights/workspaces/law-threadlight"
)
MONITOR_ID = (
    f"/subscriptions/{SUB}/resourceGroups/{RG}"
    "/providers/Microsoft.CognitiveServices/accounts/aoai-threadlight"
)
CUSTOMER_ID = "abcdabcd-1234-5678-9abc-abcdefabcdef"
KQL = 'AppTraces | where Message == "return_decision_completed" | count'

COST_URL = (
    f"https://management.azure.com/subscriptions/{SUB}"
    f"/resourceGroups/{RG}/providers/Microsoft.CostManagement"
    f"/query?api-version={COST_API_VERSION}"
)


# ---------------------------------------------------------------------------
# Harness — no real subprocess, ever
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """Any real process spawn is a test failure, not an Azure call."""

    def _boom(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError(f"test attempted a real subprocess call: {args!r}")

    monkeypatch.setattr(subprocess, "run", _boom)


@pytest.fixture(autouse=True)
def _azure_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(tmp_path / "azure-config"))


def _cp(stdout="", *, returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["az"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class FakeRunner:
    """Records every argv it is handed and replays queued responses."""

    def __init__(self, responses=None, *, default=None):
        self.responses = list(responses or [])
        self.default = default
        self.calls: list[list[str]] = []

    def __call__(self, args):
        assert isinstance(args, list), "runner must receive an argv list"
        assert all(isinstance(a, str) for a in args), "argv entries must be str"
        self.calls.append(list(args))
        if self.responses:
            nxt = self.responses.pop(0)
        elif self.default is not None:
            nxt = self.default
        else:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected extra runner call: {args!r}")
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


class SleepSpy:
    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, seconds):
        self.delays.append(seconds)


def _account_ok(subscription_id=SUB):
    return _cp(json.dumps({"id": subscription_id, "tenantId": TENANT}))


def _page(rows=None, *, next_link=None):
    page = {
        "properties": {
            "columns": [
                {"name": "PreTaxCost", "type": "Number"},
                {"name": "UsageDate", "type": "Number"},
                {"name": "ResourceId", "type": "String"},
            ],
            "rows": rows if rows is not None else [[1.5, 20260801, "/r/a"]],
        }
    }
    if next_link is not None:
        page["properties"]["nextLink"] = next_link
    return page


def _page_cp(rows=None, *, next_link=None):
    return _cp(json.dumps(_page(rows, next_link=next_link)))


def _token_doc():
    return {"value": [{"name": {"value": "InputTokens"}, "timeseries": []}]}


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_public_api_surface_is_exported():
    for name in (
        "ActualsSourceError",
        "Runner",
        "COST_API_VERSION",
        "cost_query_body",
        "assert_azure_context",
        "fetch_cost_pages",
        "resolve_workspace_customer_id",
        "fetch_interaction_result",
        "collect_sources",
    ):
        assert hasattr(actuals_sources, name), f"missing public name: {name}"


def test_actuals_source_error_is_runtime_error():
    assert issubclass(ActualsSourceError, RuntimeError)


def test_runner_alias_is_callable_type():
    assert Runner is not None


def test_cost_api_version_is_pinned():
    assert COST_API_VERSION == "2025-03-01"


def test_module_never_uses_shell():
    source = (SCRIPTS / "actuals_sources.py").read_text()
    assert "shell=True" not in source
    assert "os.system" not in source
    assert "check=True" not in source


# ---------------------------------------------------------------------------
# cost_query_body
# ---------------------------------------------------------------------------

def test_cost_query_body_shape():
    body = cost_query_body(START, END)
    assert body["type"] == "Usage"
    assert body["timeframe"] == "Custom"
    assert body["timePeriod"] == {
        "from": "2026-08-01T00:00:00Z",
        "to": "2026-08-08T00:00:00Z",
    }
    dataset = body["dataset"]
    assert dataset["granularity"] == "Daily"
    assert dataset["aggregation"] == {
        "totalCost": {"name": "PreTaxCost", "function": "Sum"}
    }
    names = [g["name"] for g in dataset["grouping"]]
    assert names == ["ResourceId", "ResourceType", "ServiceName"]
    assert all(g["type"] == "Dimension" for g in dataset["grouping"])


def test_cost_query_body_does_not_group_by_usage_date():
    """UsageDate is emitted by Daily granularity; grouping on it is wrong."""
    names = [g["name"].casefold() for g in cost_query_body(START, END)["dataset"]["grouping"]]
    assert "usagedate" not in names


def test_cost_query_body_returns_fresh_object_each_call():
    first = cost_query_body(START, END)
    first["dataset"]["grouping"].append({"type": "Dimension", "name": "Tampered"})
    second = cost_query_body(START, END)
    assert len(second["dataset"]["grouping"]) == 3


def test_cost_query_body_rejects_non_increasing_window():
    with pytest.raises(ActualsSourceError):
        cost_query_body(END, START)
    with pytest.raises(ActualsSourceError):
        cost_query_body(START, START)


def test_cost_query_body_rejects_non_date_inputs():
    with pytest.raises(ActualsSourceError):
        cost_query_body("2026-08-01", END)
    with pytest.raises(ActualsSourceError):
        cost_query_body(datetime(2026, 8, 1, tzinfo=timezone.utc), END)


# ---------------------------------------------------------------------------
# assert_azure_context
# ---------------------------------------------------------------------------

def test_assert_azure_context_exact_command_and_result():
    runner = FakeRunner([_account_ok()])
    ctx = assert_azure_context(SUB, runner=runner)
    assert runner.calls == [
        ["az", "account", "show", "--query", "{id:id,tenantId:tenantId}", "-o", "json"]
    ]
    assert ctx["subscription_id"] == SUB
    assert ctx["tenant_id"] == TENANT


def test_assert_azure_context_matches_guid_case_insensitively():
    runner = FakeRunner([_account_ok(SUB.upper())])
    ctx = assert_azure_context(SUB, runner=runner)
    assert ctx["subscription_id"].casefold() == SUB.casefold()


def test_assert_azure_context_rejects_other_active_subscription():
    runner = FakeRunner([_account_ok(OTHER_SUB)])
    with pytest.raises(ActualsSourceError):
        assert_azure_context(SUB, runner=runner)


@pytest.mark.parametrize(
    "stdout",
    ["", "   ", "not json", "[]", json.dumps({"tenantId": TENANT}),
     json.dumps({"id": SUB}), json.dumps({"id": "nope", "tenantId": TENANT})],
)
def test_assert_azure_context_rejects_malformed_account_response(stdout):
    runner = FakeRunner([_cp(stdout)])
    with pytest.raises(ActualsSourceError):
        assert_azure_context(SUB, runner=runner)


def test_assert_azure_context_rejects_failed_command():
    runner = FakeRunner([_cp("", returncode=1, stderr="Please run 'az login'")])
    with pytest.raises(ActualsSourceError):
        assert_azure_context(SUB, runner=runner)


def test_assert_azure_context_requires_azure_config_dir(monkeypatch):
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    runner = FakeRunner([_account_ok()])
    with pytest.raises(ActualsSourceError):
        assert_azure_context(SUB, runner=runner)
    assert runner.calls == []


def test_assert_azure_context_rejects_blank_azure_config_dir(monkeypatch):
    monkeypatch.setenv("AZURE_CONFIG_DIR", "   ")
    runner = FakeRunner([_account_ok()])
    with pytest.raises(ActualsSourceError):
        assert_azure_context(SUB, runner=runner)
    assert runner.calls == []


def test_assert_azure_context_validates_subscription_before_calling():
    runner = FakeRunner([_account_ok()])
    with pytest.raises(ActualsSourceError):
        assert_azure_context("not-a-guid", runner=runner)
    assert runner.calls == []


# ---------------------------------------------------------------------------
# fetch_cost_pages — command construction
# ---------------------------------------------------------------------------

def test_fetch_cost_pages_builds_read_only_az_rest_argv():
    runner = FakeRunner([_page_cp()])
    pages = fetch_cost_pages(SUB, RG, START, END, runner=runner)
    assert len(pages) == 1
    argv = runner.calls[0]
    assert argv[:5] == ["az", "rest", "--method", "post", "--url"]
    assert argv[5] == COST_URL
    assert argv[6] == "--body"
    assert json.loads(argv[7]) == cost_query_body(START, END)
    assert argv[8:] == ["--output", "json"]


def test_fetch_cost_pages_url_carries_explicit_subscription_and_api_version():
    runner = FakeRunner([_page_cp()])
    fetch_cost_pages(SUB, RG, START, END, runner=runner)
    url = runner.calls[0][5]
    assert url.startswith(f"https://management.azure.com/subscriptions/{SUB}/")
    assert url.endswith(f"api-version={COST_API_VERSION}")
    assert "Microsoft.CostManagement/query" in url


def test_fetch_cost_pages_returns_parsed_page_documents():
    runner = FakeRunner([_page_cp(rows=[[2.25, 20260802, "/r/b"]])])
    pages = fetch_cost_pages(SUB, RG, START, END, runner=runner)
    assert pages[0]["properties"]["rows"] == [[2.25, 20260802, "/r/b"]]


def test_fetch_cost_pages_requires_azure_config_dir(monkeypatch):
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    runner = FakeRunner([_page_cp()])
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner)
    assert runner.calls == []


# ---------------------------------------------------------------------------
# fetch_cost_pages — input validation / injection resistance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_sub",
    [
        "",
        "not-a-guid",
        f"{SUB} --debug",
        f"{SUB}\nwhoami",
        f"{SUB}/../{OTHER_SUB}",
        f"{SUB};rm -rf /",
        f"../../{SUB}",
        "11111111-2222-3333-4444-55555555555",
        None,
        12345,
    ],
)
def test_fetch_cost_pages_rejects_unsafe_subscription(bad_sub):
    runner = FakeRunner(default=_page_cp())
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(bad_sub, RG, START, END, runner=runner)
    assert runner.calls == []


@pytest.mark.parametrize(
    "bad_rg",
    [
        "",
        "rg name",
        "rg/../evil",
        "rg\nwhoami",
        "rg;rm -rf /",
        "rg&&curl evil",
        "rg$(whoami)",
        "../rg",
        "rg/sub",
        "rg.",
        "r" * 91,
        None,
        42,
    ],
)
def test_fetch_cost_pages_rejects_unsafe_resource_group(bad_rg):
    runner = FakeRunner(default=_page_cp())
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, bad_rg, START, END, runner=runner)
    assert runner.calls == []


def test_fetch_cost_pages_accepts_conventional_azure_names():
    runner = FakeRunner(default=_page_cp())
    for good in ("rg-threadlight_pilot", "RG.Prod(1)", "a", "r" * 90):
        runner.calls.clear()
        fetch_cost_pages(SUB, good, START, END, runner=runner)
        assert runner.calls[0][5].split("/resourceGroups/")[1].startswith(good)


def test_fetch_cost_pages_rejects_bad_window_before_calling():
    runner = FakeRunner(default=_page_cp())
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, END, START, runner=runner)
    assert runner.calls == []


# ---------------------------------------------------------------------------
# fetch_cost_pages — response handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stdout", ["", "   ", "\n"])
def test_fetch_cost_pages_rejects_empty_response(stdout):
    runner = FakeRunner([_cp(stdout)])
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner)


def test_fetch_cost_pages_rejects_malformed_json():
    runner = FakeRunner([_cp("{not json")])
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner)


@pytest.mark.parametrize("payload", ["[]", '"text"', "null", "3"])
def test_fetch_cost_pages_rejects_non_object_page(payload):
    runner = FakeRunner([_cp(payload)])
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner)


# ---------------------------------------------------------------------------
# fetch_cost_pages — pagination
# ---------------------------------------------------------------------------

def _next_link(skiptoken="abc"):
    return (
        f"https://management.azure.com/subscriptions/{SUB}/resourceGroups/{RG}"
        f"/providers/Microsoft.CostManagement/query"
        f"?api-version={COST_API_VERSION}&$skiptoken={skiptoken}"
    )


def test_fetch_cost_pages_follows_next_link_with_same_body():
    link = _next_link()
    runner = FakeRunner([
        _page_cp(next_link=link),
        _page_cp(rows=[[3.0, 20260803, "/r/c"]]),
    ])
    pages = fetch_cost_pages(SUB, RG, START, END, runner=runner)
    assert len(pages) == 2
    assert runner.calls[1][5] == link
    assert runner.calls[0][7] == runner.calls[1][7]
    assert runner.calls[1][:4] == ["az", "rest", "--method", "post"]


def test_fetch_cost_pages_follows_case_insensitive_resourcegroups_segment():
    link = _next_link().replace("/resourceGroups/", "/resourcegroups/")
    runner = FakeRunner([_page_cp(next_link=link), _page_cp()])
    assert len(fetch_cost_pages(SUB, RG, START, END, runner=runner)) == 2


def test_fetch_cost_pages_rejects_repeated_next_link():
    link = _next_link()
    runner = FakeRunner([
        _page_cp(next_link=link),
        _page_cp(next_link=link),
        _page_cp(),
    ])
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner)


@pytest.mark.parametrize(
    "link",
    [
        "http://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
        "/providers/Microsoft.CostManagement/query?api-version=2025-03-01",
        "https://evil.example.com/subscriptions/{sub}/resourceGroups/{rg}"
        "/providers/Microsoft.CostManagement/query?api-version=2025-03-01",
        "https://management.azure.com.evil.example/subscriptions/{sub}"
        "/resourceGroups/{rg}/providers/Microsoft.CostManagement/query",
        "https://management.azure.com:8443/subscriptions/{sub}/resourceGroups/{rg}"
        "/providers/Microsoft.CostManagement/query",
        "https://user:pass@management.azure.com/subscriptions/{sub}"
        "/resourceGroups/{rg}/providers/Microsoft.CostManagement/query",
        "https://management.azure.com/subscriptions/{other}/resourceGroups/{rg}"
        "/providers/Microsoft.CostManagement/query?api-version=2025-03-01",
        "https://management.azure.com/subscriptions/{sub}/resourceGroups/other-rg"
        "/providers/Microsoft.CostManagement/query?api-version=2025-03-01",
        "https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
        "/providers/Microsoft.Consumption/usageDetails?api-version=2025-03-01",
        "https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
        "/providers/Microsoft.CostManagement/query/../../evil",
        "ftp://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
        "/providers/Microsoft.CostManagement/query",
        "/subscriptions/{sub}/resourceGroups/{rg}"
        "/providers/Microsoft.CostManagement/query",
        "",
    ],
)
def test_fetch_cost_pages_rejects_untrustworthy_next_link(link):
    hostile = link.format(sub=SUB, rg=RG, other=OTHER_SUB)
    runner = FakeRunner([_page_cp(next_link=hostile), _page_cp()])
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner)
    assert len(runner.calls) == 1


@pytest.mark.parametrize("link", [123, {"url": "x"}, ["x"], True])
def test_fetch_cost_pages_rejects_non_string_next_link(link):
    runner = FakeRunner([_page_cp(next_link=link), _page_cp()])
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner)


def test_fetch_cost_pages_stops_on_absent_or_null_next_link():
    runner = FakeRunner([_cp(json.dumps({"properties": {"nextLink": None, "rows": []}}))])
    pages = fetch_cost_pages(SUB, RG, START, END, runner=runner)
    assert len(pages) == 1
    assert len(runner.calls) == 1


def test_fetch_cost_pages_bounds_total_pages():
    responses = [_page_cp(next_link=_next_link(f"tok{i}")) for i in range(200)]
    runner = FakeRunner(responses)
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner)
    assert len(runner.calls) < 200


# ---------------------------------------------------------------------------
# fetch_cost_pages — retry policy (RFC §11.1)
# ---------------------------------------------------------------------------

_THROTTLED = "(429) Too Many Requests - please retry"
_SERVER_ERR = "Internal Server Error (503)"


def test_fetch_cost_pages_retries_throttling_with_bounded_delays():
    sleeper = SleepSpy()
    runner = FakeRunner([
        _cp("", returncode=1, stderr=_THROTTLED),
        _cp("", returncode=1, stderr=_THROTTLED),
        _page_cp(),
    ])
    pages = fetch_cost_pages(SUB, RG, START, END, runner=runner, sleep=sleeper)
    assert len(pages) == 1
    assert sleeper.delays == [2, 4]
    assert len(runner.calls) == 3


def test_fetch_cost_pages_exhausts_retries_then_raises():
    sleeper = SleepSpy()
    runner = FakeRunner(default=_cp("", returncode=1, stderr=_THROTTLED))
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner, sleep=sleeper)
    assert sleeper.delays == [2, 4, 8]
    assert len(runner.calls) == 4


@pytest.mark.parametrize(
    "stderr",
    [_SERVER_ERR, "500 Internal Server Error", "(502) Bad Gateway",
     "504 Gateway Timeout", "ServiceUnavailable", "TooManyRequests"],
)
def test_fetch_cost_pages_treats_5xx_and_429_as_transient(stderr):
    sleeper = SleepSpy()
    runner = FakeRunner([_cp("", returncode=1, stderr=stderr), _page_cp()])
    fetch_cost_pages(SUB, RG, START, END, runner=runner, sleep=sleeper)
    assert sleeper.delays == [2]


@pytest.mark.parametrize(
    "stderr",
    ["(403) Forbidden: AuthorizationFailed", "(404) ResourceGroupNotFound",
     "(400) BadRequest: invalid dataset"],
)
def test_fetch_cost_pages_does_not_retry_non_transient_failures(stderr):
    sleeper = SleepSpy()
    runner = FakeRunner(default=_cp("", returncode=1, stderr=stderr))
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner, sleep=sleeper)
    assert sleeper.delays == []
    assert len(runner.calls) == 1


def test_fetch_cost_pages_retry_reuses_identical_body():
    runner = FakeRunner([
        _cp("", returncode=1, stderr=_THROTTLED),
        _page_cp(),
    ])
    fetch_cost_pages(SUB, RG, START, END, runner=runner, sleep=SleepSpy())
    assert runner.calls[0] == runner.calls[1]


def test_fetch_cost_pages_error_message_is_bounded_and_scrubbed():
    secret = "eyJhbGciOiJIUzI1NiJ9.c2VjcmV0LXBheWxvYWQ.c2ln"
    noisy = (
        f"Authorization: Bearer {secret} "
        + json.dumps(cost_query_body(START, END))
        + " AuthorizationFailed " + ("x" * 5000)
    )
    runner = FakeRunner(default=_cp("", returncode=1, stderr=noisy))
    with pytest.raises(ActualsSourceError) as excinfo:
        fetch_cost_pages(SUB, RG, START, END, runner=runner, sleep=SleepSpy())
    message = str(excinfo.value)
    assert len(message) <= 800
    assert "eyJ" not in message
    assert secret not in message
    assert "PreTaxCost" not in message
    assert "--body" not in message


def test_fetch_cost_pages_surfaces_runner_exceptions_as_source_error():
    runner = FakeRunner([FileNotFoundError("az not installed")])
    with pytest.raises(ActualsSourceError):
        fetch_cost_pages(SUB, RG, START, END, runner=runner, sleep=SleepSpy())


# ---------------------------------------------------------------------------
# Token metrics
# ---------------------------------------------------------------------------

def test_fetch_token_metrics_exact_read_only_argv():
    runner = FakeRunner([_cp(json.dumps(_token_doc()))])
    doc = fetch_token_metrics(MONITOR_ID, SUB, START, END, runner=runner)
    assert doc == _token_doc()
    assert runner.calls[0] == [
        "az", "monitor", "metrics", "list",
        "--resource", MONITOR_ID,
        "--metrics", "InputTokens", "OutputTokens", "CachedInputTokens",
        "--start-time", "2026-08-01T00:00:00Z",
        "--end-time", "2026-08-08T00:00:00Z",
        "--interval", "PT1H",
        "--aggregation", "Total",
        "--filter", "ModelDeploymentName eq '*' and ModelName eq '*'",
        "-o", "json",
    ]


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "aoai-threadlight",
        f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.CognitiveServices",
        f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/../accounts/x",
        f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.X/accounts/a b",
        f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.X/accounts/a\nb",
        f"/subscriptions/not-a-guid/resourceGroups/{RG}/providers/Microsoft.X/accounts/a",
        "https://management.azure.com" + MONITOR_ID,
        None,
        7,
    ],
)
def test_fetch_token_metrics_rejects_malformed_resource_id(bad_id):
    runner = FakeRunner(default=_cp(json.dumps(_token_doc())))
    with pytest.raises(ActualsSourceError):
        fetch_token_metrics(bad_id, SUB, START, END, runner=runner)
    assert runner.calls == []


def test_fetch_token_metrics_rejects_foreign_subscription_resource():
    foreign = MONITOR_ID.replace(SUB, OTHER_SUB)
    runner = FakeRunner(default=_cp(json.dumps(_token_doc())))
    with pytest.raises(ActualsSourceError):
        fetch_token_metrics(foreign, SUB, START, END, runner=runner)
    assert runner.calls == []


def test_fetch_token_metrics_requires_azure_config_dir(monkeypatch):
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    runner = FakeRunner(default=_cp(json.dumps(_token_doc())))
    with pytest.raises(ActualsSourceError):
        fetch_token_metrics(MONITOR_ID, SUB, START, END, runner=runner)
    assert runner.calls == []


@pytest.mark.parametrize(
    "response",
    [
        _cp("", returncode=1, stderr="(403) AuthorizationFailed"),
        _cp(""),
        _cp("{not json"),
        _cp("[]"),
    ],
)
def test_fetch_token_metrics_fails_closed_on_bad_response(response):
    runner = FakeRunner([response])
    with pytest.raises(ActualsSourceError):
        fetch_token_metrics(MONITOR_ID, SUB, START, END, runner=runner)


def test_fetch_token_metrics_does_not_parse_the_document():
    """Task 11 owns token parsing; this adapter returns the raw doc."""
    raw = {"value": [{"unexpected": "shape"}]}
    runner = FakeRunner([_cp(json.dumps(raw))])
    assert fetch_token_metrics(MONITOR_ID, SUB, START, END, runner=runner) == raw


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def test_resolve_workspace_customer_id_exact_argv():
    runner = FakeRunner([_cp(CUSTOMER_ID + "\n")])
    assert resolve_workspace_customer_id(WORKSPACE_ID, runner=runner) == CUSTOMER_ID
    assert runner.calls[0] == [
        "az", "monitor", "log-analytics", "workspace", "show",
        "--ids", WORKSPACE_ID, "--query", "customerId", "-o", "tsv",
    ]


@pytest.mark.parametrize(
    "stdout", ["", "   ", "None", "null", "not-a-guid", "abcd-1234", CUSTOMER_ID + " extra"]
)
def test_resolve_workspace_customer_id_returns_none_for_bad_output(stdout):
    runner = FakeRunner([_cp(stdout)])
    assert resolve_workspace_customer_id(WORKSPACE_ID, runner=runner) is None


def test_resolve_workspace_customer_id_returns_none_on_command_failure():
    runner = FakeRunner([_cp("", returncode=1, stderr="ResourceNotFound")])
    assert resolve_workspace_customer_id(WORKSPACE_ID, runner=runner) is None


def test_resolve_workspace_customer_id_never_raises():
    for runner in (
        FakeRunner([RuntimeError("boom")]),
        FakeRunner([FileNotFoundError("az missing")]),
    ):
        assert resolve_workspace_customer_id(WORKSPACE_ID, runner=runner) is None


@pytest.mark.parametrize(
    "bad_id",
    ["", "law-threadlight", "/subscriptions/x/resourceGroups/y", None,
     f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.X/workspaces/a;b"],
)
def test_resolve_workspace_customer_id_validates_resource_id(bad_id):
    runner = FakeRunner(default=_cp(CUSTOMER_ID))
    assert resolve_workspace_customer_id(bad_id, runner=runner) is None
    assert runner.calls == []


def test_resolve_workspace_customer_id_returns_none_without_azure_config_dir(monkeypatch):
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    runner = FakeRunner(default=_cp(CUSTOMER_ID))
    assert resolve_workspace_customer_id(WORKSPACE_ID, runner=runner) is None
    assert runner.calls == []


def test_resolve_workspace_customer_id_accepts_runner_as_second_positional():
    """Public API contract: `runner` is the second positional parameter.

    This is the exact call shape documented for callers — passing it
    positionally must work, not just as a keyword.
    """
    runner = FakeRunner([_cp(CUSTOMER_ID + "\n")])
    assert resolve_workspace_customer_id(WORKSPACE_ID, runner) == CUSTOMER_ID


# ---------------------------------------------------------------------------
# Interaction query
# ---------------------------------------------------------------------------

def _la_result():
    return [{"total_interactions": 120, "successful_interactions": 118}]


def test_fetch_interaction_result_resolves_then_queries():
    runner = FakeRunner([_cp(CUSTOMER_ID), _cp(json.dumps(_la_result()))])
    result = fetch_interaction_result(WORKSPACE_ID, KQL, runner=runner)
    assert result == _la_result()
    assert runner.calls[0][:5] == [
        "az", "monitor", "log-analytics", "workspace", "show"
    ]
    assert runner.calls[1] == [
        "az", "monitor", "log-analytics", "query",
        "--workspace", CUSTOMER_ID,
        "--analytics-query", KQL,
        "--output", "json",
    ]


def test_fetch_interaction_result_returns_none_when_workspace_unresolved():
    runner = FakeRunner([_cp("", returncode=1, stderr="ResourceNotFound")])
    assert fetch_interaction_result(WORKSPACE_ID, KQL, runner=runner) is None
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        _cp("", returncode=1, stderr="BadArgumentError"),
        _cp(""),
        _cp("{not json"),
    ],
)
def test_fetch_interaction_result_returns_none_on_query_failure(response):
    runner = FakeRunner([_cp(CUSTOMER_ID), response])
    assert fetch_interaction_result(WORKSPACE_ID, KQL, runner=runner) is None


def test_fetch_interaction_result_never_raises():
    runner = FakeRunner([_cp(CUSTOMER_ID), RuntimeError("boom")])
    assert fetch_interaction_result(WORKSPACE_ID, KQL, runner=runner) is None


@pytest.mark.parametrize("bad_kql", ["", "   ", None, 5, "count\x00"])
def test_fetch_interaction_result_rejects_unusable_kql_without_calling(bad_kql):
    runner = FakeRunner(default=_cp(CUSTOMER_ID))
    assert fetch_interaction_result(WORKSPACE_ID, bad_kql, runner=runner) is None
    assert runner.calls == []


def test_fetch_interaction_result_never_accepts_a_customer_guid():
    """A workspace GUID is not a valid ARM resource ID and must not be used."""
    runner = FakeRunner(default=_cp(json.dumps(_la_result())))
    assert fetch_interaction_result(CUSTOMER_ID, KQL, runner=runner) is None
    assert runner.calls == []


def test_no_public_function_accepts_a_customer_id_parameter():
    import inspect

    for name in ("fetch_interaction_result", "resolve_workspace_customer_id",
                 "collect_sources"):
        params = inspect.signature(getattr(actuals_sources, name)).parameters
        assert not any(
            "customer" in p.casefold() or p.casefold() in {"workspace_id", "guid"}
            for p in params
        ), f"{name} exposes a customerId-shaped parameter"


# ---------------------------------------------------------------------------
# collect_sources
# ---------------------------------------------------------------------------

def _collect_runner(*, token=True, workspace=True, query=True):
    responses = [_account_ok(), _page_cp()]
    if token:
        responses.append(_cp(json.dumps(_token_doc())))
    if workspace:
        responses.append(_cp(CUSTOMER_ID))
        if query:
            responses.append(_cp(json.dumps(_la_result())))
    return FakeRunner(responses)


def test_collect_sources_cost_only_bundle():
    runner = FakeRunner([_account_ok(), _page_cp()])
    bundle = collect_sources(SUB, RG, START, END, runner=runner)
    assert bundle["cost_pages"] == [_page()]
    assert bundle["token_doc"] is None
    assert bundle["token_source_resource_id"] is None
    assert bundle["interaction_result"] is None
    assert bundle["warnings"] == []
    assert bundle["window"] == {"start": "2026-08-01", "end": "2026-08-08"}
    assert bundle["subscription_id"] == SUB
    assert bundle["resource_group"] == RG
    assert bundle["azure_context"]["tenant_id"] == TENANT


def test_collect_sources_call_order_is_context_cost_token_workspace_query():
    runner = _collect_runner()
    collect_sources(
        SUB, RG, START, END,
        monitor_resource_id=MONITOR_ID,
        workspace_resource_id=WORKSPACE_ID,
        kql=KQL,
        runner=runner,
    )
    verbs = [" ".join(call[1:4]) for call in runner.calls]
    assert verbs == [
        "account show --query",
        "rest --method post",
        "monitor metrics list",
        "monitor log-analytics workspace",
        "monitor log-analytics query",
    ]


def test_collect_sources_all_sources_present():
    runner = _collect_runner()
    bundle = collect_sources(
        SUB, RG, START, END,
        monitor_resource_id=MONITOR_ID,
        workspace_resource_id=WORKSPACE_ID,
        kql=KQL,
        runner=runner,
    )
    assert bundle["token_doc"] == _token_doc()
    assert bundle["token_source_resource_id"] == MONITOR_ID
    assert bundle["interaction_result"] == _la_result()
    assert bundle["warnings"] == []


def test_collect_sources_records_token_source_only_when_query_succeeds():
    runner = FakeRunner([
        _account_ok(), _page_cp(),
        _cp("", returncode=1, stderr="(403) AuthorizationFailed"),
    ])
    bundle = collect_sources(
        SUB, RG, START, END, monitor_resource_id=MONITOR_ID, runner=runner
    )
    assert bundle["token_doc"] is None
    assert bundle["token_source_resource_id"] is None
    assert bundle["cost_pages"] == [_page()]
    assert len(bundle["warnings"]) == 1
    assert "token" in bundle["warnings"][0].casefold()


def test_collect_sources_distinguishes_workspace_resolution_from_query_failure():
    unresolved = FakeRunner([
        _account_ok(), _page_cp(), _cp("", returncode=1, stderr="ResourceNotFound"),
    ])
    resolution_bundle = collect_sources(
        SUB, RG, START, END,
        workspace_resource_id=WORKSPACE_ID, kql=KQL, runner=unresolved,
    )

    failed_query = FakeRunner([
        _account_ok(), _page_cp(), _cp(CUSTOMER_ID),
        _cp("", returncode=1, stderr="BadArgumentError"),
    ])
    query_bundle = collect_sources(
        SUB, RG, START, END,
        workspace_resource_id=WORKSPACE_ID, kql=KQL, runner=failed_query,
    )

    assert len(resolution_bundle["warnings"]) == 1
    assert len(query_bundle["warnings"]) == 1
    assert resolution_bundle["warnings"] != query_bundle["warnings"]
    assert resolution_bundle["interaction_result"] is None
    assert query_bundle["interaction_result"] is None
    assert resolution_bundle["cost_pages"] == [_page()]
    assert query_bundle["cost_pages"] == [_page()]


def test_collect_sources_warns_once_per_failing_optional_source():
    runner = FakeRunner([
        _account_ok(), _page_cp(),
        _cp("", returncode=1, stderr="(403) AuthorizationFailed"),
        _cp("", returncode=1, stderr="ResourceNotFound"),
    ])
    bundle = collect_sources(
        SUB, RG, START, END,
        monitor_resource_id=MONITOR_ID,
        workspace_resource_id=WORKSPACE_ID,
        kql=KQL,
        runner=runner,
    )
    assert len(bundle["warnings"]) == 2
    assert len(set(bundle["warnings"])) == 2


def test_collect_sources_skips_interaction_without_kql():
    runner = FakeRunner([_account_ok(), _page_cp()])
    bundle = collect_sources(
        SUB, RG, START, END, workspace_resource_id=WORKSPACE_ID, runner=runner
    )
    assert bundle["interaction_result"] is None
    assert bundle["warnings"] == []
    assert len(runner.calls) == 2


def test_collect_sources_skips_interaction_without_workspace():
    runner = FakeRunner([_account_ok(), _page_cp()])
    bundle = collect_sources(SUB, RG, START, END, kql=KQL, runner=runner)
    assert bundle["interaction_result"] is None
    assert bundle["warnings"] == []
    assert len(runner.calls) == 2


def test_collect_sources_token_only():
    runner = FakeRunner([_account_ok(), _page_cp(), _cp(json.dumps(_token_doc()))])
    bundle = collect_sources(
        SUB, RG, START, END, monitor_resource_id=MONITOR_ID, runner=runner
    )
    assert bundle["token_doc"] == _token_doc()
    assert bundle["interaction_result"] is None
    assert bundle["warnings"] == []


def test_collect_sources_interaction_only():
    runner = FakeRunner([
        _account_ok(), _page_cp(), _cp(CUSTOMER_ID), _cp(json.dumps(_la_result())),
    ])
    bundle = collect_sources(
        SUB, RG, START, END,
        workspace_resource_id=WORKSPACE_ID, kql=KQL, runner=runner,
    )
    assert bundle["token_doc"] is None
    assert bundle["interaction_result"] == _la_result()


def test_collect_sources_cost_failure_aborts_the_bundle():
    runner = FakeRunner([
        _account_ok(), _cp("", returncode=1, stderr="(403) AuthorizationFailed"),
    ])
    with pytest.raises(ActualsSourceError):
        collect_sources(
            SUB, RG, START, END,
            monitor_resource_id=MONITOR_ID, workspace_resource_id=WORKSPACE_ID,
            kql=KQL, runner=runner, sleep=SleepSpy(),
        )
    assert len(runner.calls) == 2


def test_collect_sources_context_failure_prevents_every_query():
    runner = FakeRunner([_account_ok(OTHER_SUB)])
    with pytest.raises(ActualsSourceError):
        collect_sources(SUB, RG, START, END, runner=runner)
    assert len(runner.calls) == 1


def test_collect_sources_requires_azure_config_dir(monkeypatch):
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    runner = FakeRunner(default=_page_cp())
    with pytest.raises(ActualsSourceError):
        collect_sources(SUB, RG, START, END, runner=runner)
    assert runner.calls == []


def test_collect_sources_issues_only_read_only_commands():
    runner = _collect_runner()
    collect_sources(
        SUB, RG, START, END,
        monitor_resource_id=MONITOR_ID, workspace_resource_id=WORKSPACE_ID,
        kql=KQL, runner=runner,
    )
    allowed = {
        ("account", "show"),
        ("rest", "--method"),
        ("monitor", "metrics"),
        ("monitor", "log-analytics"),
    }
    mutating = {"create", "delete", "update", "set", "deploy", "purge", "write"}
    for call in runner.calls:
        assert call[0] == "az"
        assert (call[1], call[2]) in allowed, call
        assert not mutating & set(call), call
        if call[1] == "rest":
            assert call[3] == "post" and "Microsoft.CostManagement/query" in call[5]


def test_collect_sources_does_not_mutate_runner_inputs_or_share_state():
    runner_one = _collect_runner()
    bundle_one = collect_sources(
        SUB, RG, START, END,
        monitor_resource_id=MONITOR_ID, workspace_resource_id=WORKSPACE_ID,
        kql=KQL, runner=runner_one,
    )
    bundle_one["warnings"].append("tampered")
    bundle_one["cost_pages"].clear()

    runner_two = _collect_runner()
    bundle_two = collect_sources(
        SUB, RG, START, END,
        monitor_resource_id=MONITOR_ID, workspace_resource_id=WORKSPACE_ID,
        kql=KQL, runner=runner_two,
    )
    assert bundle_two["warnings"] == []
    assert bundle_two["cost_pages"] == [_page()]
    assert runner_one.calls == runner_two.calls


def test_collect_sources_validates_optional_ids_before_any_call():
    runner = FakeRunner(default=_account_ok())
    with pytest.raises(ActualsSourceError):
        collect_sources(
            SUB, RG, START, END, monitor_resource_id="not-an-arm-id", runner=runner
        )
    assert runner.calls == []


def test_collect_sources_accepts_kql_keyword():
    """Public API contract: the interaction-query keyword is `kql`.

    This is the exact spec-literal call shape a future CLI issues.
    """
    runner = FakeRunner([
        _account_ok(), _page_cp(), _cp(CUSTOMER_ID), _cp(json.dumps(_la_result())),
    ])
    bundle = collect_sources(
        SUB, RG, START, END,
        workspace_resource_id=WORKSPACE_ID, kql=KQL, runner=runner,
    )
    assert bundle["interaction_result"] == _la_result()


def test_collect_sources_rejects_old_interaction_kql_keyword():
    """Drift guard: the old `interaction_kql` keyword must no longer work.

    `collect_sources` has exactly one public spelling for the query
    keyword (`kql`); a caller still using the old name must fail loudly
    with a `TypeError`, not be silently accepted as an alias.
    """
    runner = FakeRunner([_account_ok(), _page_cp()])
    with pytest.raises(TypeError):
        collect_sources(
            SUB, RG, START, END,
            workspace_resource_id=WORKSPACE_ID, interaction_kql=KQL, runner=runner,
        )


# ---------------------------------------------------------------------------
# Default runner
# ---------------------------------------------------------------------------

def test_default_runner_is_a_bounded_non_shell_subprocess(monkeypatch):
    seen = {}

    def _fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return _cp(json.dumps({"id": SUB, "tenantId": TENANT}))

    monkeypatch.setattr(actuals_sources.subprocess, "run", _fake_run)
    assert_azure_context(SUB)

    assert seen["args"] == [
        "az", "account", "show", "--query", "{id:id,tenantId:tenantId}", "-o", "json"
    ]
    kwargs = seen["kwargs"]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert 0 < kwargs["timeout"] <= 300
    assert kwargs.get("check", False) is False
    assert kwargs.get("shell", False) is False


def test_default_runner_maps_missing_az_to_source_error(monkeypatch):
    def _fake_run(args, **kwargs):
        raise FileNotFoundError("az")

    monkeypatch.setattr(actuals_sources.subprocess, "run", _fake_run)
    with pytest.raises(ActualsSourceError):
        assert_azure_context(SUB)


def test_default_runner_maps_timeout_to_source_error(monkeypatch):
    def _fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="az", timeout=1)

    monkeypatch.setattr(actuals_sources.subprocess, "run", _fake_run)
    with pytest.raises(ActualsSourceError):
        assert_azure_context(SUB)
