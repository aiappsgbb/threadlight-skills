"""Shared sample-data shape normalization.

The threadlight ecosystem currently allows two on-disk shapes for
``specs/sample-data/<entity>.json``:

1. **Plain JSON array** (legacy / fixture-poc):
   ``[{"id": "T-1", ...}, {"id": "T-2", ...}]``

2. **Envelope with metadata** (canonical, per threadlight-design SKILL § specs/
   sample-data/{entity}.json, threadlight-demo-data-factory § "Wrap shape",
   and threadlight-workspace-ui § strip ``_meta``):
   ``{"_meta": {...}, "records": [{"id": "T-1", ...}, ...]}``

Both ``discover.py`` (validation) and ``stub_tools.py`` (loading) need to
accept both shapes. Keep the unwrap logic in one place so the contract
stays consistent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_SHAPE_HELP = (
    "sample-data file must be a JSON array, or an object of the form "
    '{"_meta": {...}, "records": [...]}'
)


def extract_records(data: Any, source: Path) -> list[dict[str, Any]]:
    """Return the record list for either accepted on-disk shape.

    Does NOT validate individual records beyond top-level shape — callers
    that need per-record validation (e.g. ``InMemoryStore.load``) keep
    their own checks.

    Raises ``ValueError`` with a message naming both accepted shapes if
    ``data`` is neither a list nor a ``{"_meta", "records"}`` envelope.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    got = type(data).__name__
    if isinstance(data, dict):
        keys = sorted(data.keys())
        detail = f"got dict with keys {keys!r}"
    else:
        detail = f"got {got}"
    raise ValueError(f"{_SHAPE_HELP} ({detail}): {source}")
