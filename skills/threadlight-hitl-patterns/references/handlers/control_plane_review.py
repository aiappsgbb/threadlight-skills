"""A delegated decision bridge, not a bot login or business-effect executor."""
from collections.abc import Awaitable, Callable
from copy import deepcopy

from azure.core.credentials_async import AsyncTokenCredential
import httpx

from govern_control_plane.review import decide, validate_pending_review


async def decide_submission(
    submission: dict, *,
    load_review: Callable[[str], Awaitable[tuple[str, dict]]],
    credential: AsyncTokenCredential,
    role: str,
    base_url: str,
    scope: str,
    http: httpx.AsyncClient | None = None,
) -> dict:
    required = {"gate", "decision", "case_id", "review_id"}
    if (not isinstance(submission, dict) or not required <= submission.keys()
            or submission.keys() - required - {"reason", "rationale"}
            or any(not isinstance(submission[key], str) or not 0 < len(submission[key]) <= 128
                   for key in required)
            or any(not isinstance(submission[key], str) or len(submission[key]) > 1024
                   for key in ("reason", "rationale") if key in submission)):
        raise ValueError("card_submission_invalid")
    decisions = {
        "approve": {"approved": True, "declined": False},
        "reject": {"declined": False},
    }
    if submission["gate"] not in decisions:
        raise ValueError("gate_requires_application_integration")
    if submission["decision"] not in decisions[submission["gate"]]:
        raise ValueError("card_decision_invalid")

    case_id, stored_pending = await load_review(submission["review_id"])
    pending = deepcopy(stored_pending)
    intent = validate_pending_review(pending)
    if case_id != submission["case_id"] or intent.nonce != submission["review_id"]:
        raise ValueError("card_review_binding_mismatch")
    return await decide(
        pending, approved=decisions[submission["gate"]][submission["decision"]],
        role=role, base_url=base_url, scope=scope, credential=credential, http=http)
