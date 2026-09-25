"""Offline native operator wire composition; no HTTP or Azure credentials used."""
import importlib.util
from pathlib import Path
import sys

import pytest


def helper():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "returns_gateway_operator", Path(__file__).resolve().parents[1] / "scripts/returns_gateway_operator.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stop_uses_actual_operator_inspect_cas_and_independent_readback():
    requests = []

    def invoke(request):
        requests.append(request)
        return {"state": "open" if len(requests) == 1 else "stopped",
                "record_hash": "sha256:" + ("a" if len(requests) == 1 else "b") * 64,
                "audit_receipt_id": "receipt", "retry_authorized": False}

    result = helper().operate("stop", requester="1" * 32, invoke=invoke)
    assert [row["operation"] for row in requests] == ["inspect", "admission", "inspect"]
    assert requests[1] == {
        "operation": "admission", "action": "returns_apply_decision", "requester": "1" * 32,
        "state": "stopped", "valid_until": None, "reason_code": "release-stop",
        "expected_record_hash": "sha256:" + "a" * 64,
    }
    assert result["state"] == "stopped" and result["retry_authorized"] is False


def test_lost_stop_reply_never_retries_mutation():
    requests = []

    def invoke(request):
        requests.append(request)
        if len(requests) == 2:
            raise ValueError("lost acknowledgement")
        return {"state": "open", "record_hash": "sha256:" + "a" * 64, "retry_authorized": False}

    with pytest.raises(ValueError):
        helper().operate("stop", requester="1" * 32, invoke=invoke)
    assert [row["operation"] for row in requests] == ["inspect", "admission"]


def test_open_requires_bounded_lease_and_exact_ack():
    with pytest.raises(ValueError):
        helper().operate("open", requester="1" * 32, invoke=lambda _: {}, valid_until=None)


def test_readback_drift_does_not_claim_closure():
    replies = iter([
        {"state": "missing", "record_hash": None, "retry_authorized": False},
        {"state": "stopped", "record_hash": "sha256:" + "a" * 64, "retry_authorized": False},
        {"state": "open", "record_hash": "sha256:" + "b" * 64, "retry_authorized": False},
    ])
    with pytest.raises(ValueError):
        helper().operate("stop", requester="1" * 32, invoke=lambda _: next(replies))


def test_reference_handoff_names_real_gateway_boundary_and_remaining_traffic_owner():
    root = Path(__file__).resolve().parents[3]
    text = (root / "docs/reference-release.md").read_text()
    assert "POST /governance/operations" in text
    assert "Governance.Operate" in text
    assert "300" in text
    assert "traffic controller" in text
    assert "returns_gateway_operator.py" in text
    assert "returns-mcp/v1" in (root / "skills/threadlight-cicd/SKILL.md").read_text()
