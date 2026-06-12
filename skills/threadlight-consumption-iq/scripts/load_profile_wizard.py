"""
Phase 2 — load-profile wizard + SPEC writer.

Reads `specs/SPEC.md § 12 → load_profile{}`. If the sub-block is present
and all required fields are populated, returns the parsed dict. If
missing or partial, runs an interactive wizard for the seven required
fields plus `declared_constraints`, then writes the answers back to the
SPEC in canonical YAML shape so the next run is non-interactive.

Required fields (see references/load-profile-schema.md for full schema):

    workload_class
    peak_concurrent_sessions
    avg_requests_per_session
    avg_tokens_per_request
    peak_requests_per_second
    business_hours_only
    cosmos_gb_year_one
    storage_gb_year_one
    ai_search_documents
    monthly_growth_rate
    declared_constraints:
      max_p95_latency_ms
      min_redundancy
      pinned_region        # optional

If `non_interactive=True` and the SPEC is incomplete, raise
ProfileIncompleteError (CLI exits 4).

Idempotency: re-running the wizard against a fully-populated SPEC must
be a no-op (no SPEC mutation, no prompts).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


REQUIRED_FIELDS = (
    "workload_class",
    "peak_concurrent_sessions",
    "avg_requests_per_session",
    "avg_tokens_per_request",
    "peak_requests_per_second",
    "business_hours_only",
    "cosmos_gb_year_one",
    "storage_gb_year_one",
    "ai_search_documents",
    "monthly_growth_rate",
)

REQUIRED_CONSTRAINTS = (
    "max_p95_latency_ms",
    "min_redundancy",
)


def load_or_prompt_profile(
    spec_path: Path,
    non_interactive: bool = False,
) -> dict[str, Any]:
    """Read SPEC § 12; prompt for any missing fields; write back."""
    # TODO(load-profile): parse SPEC § 12 markdown -> extract YAML code block.
    # TODO(load-profile): validate against REQUIRED_FIELDS + REQUIRED_CONSTRAINTS.
    # TODO(load-profile): if non_interactive and incomplete -> raise ProfileIncompleteError.
    # TODO(load-profile): if interactive, prompt for missing fields with
    #                     validation + sensible defaults per workload_class.
    # TODO(load-profile): serialize back to canonical YAML; re-write SPEC § 12 in place.
    raise NotImplementedError(
        "load_or_prompt_profile is scaffolded but not yet implemented; "
        "see todos 'load-profile' in plan.md"
    )


def _parse_section_12(spec_text: str) -> dict[str, Any]:
    """Extract the load_profile{} YAML block from SPEC § 12."""
    raise NotImplementedError


def _serialize_section_12(load_profile: dict[str, Any]) -> str:
    """Render canonical YAML for SPEC § 12 load_profile{} block."""
    raise NotImplementedError
