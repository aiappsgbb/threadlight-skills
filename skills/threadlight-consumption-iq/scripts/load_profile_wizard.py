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

import re
from pathlib import Path
from typing import Any


class ProfileIncompleteError(RuntimeError):
    """SPEC § 12 load_profile{} missing required fields after the wizard."""


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

_WORKLOAD_CLASSES = ("chat-agent", "batch", "scheduled", "hybrid")

# Maps field name → parse/serialize type tag.
_FIELD_TYPES: dict[str, str] = {
    "workload_class": "str",
    "peak_concurrent_sessions": "int",
    "avg_requests_per_session": "int",
    "avg_tokens_per_request": "int",
    "peak_requests_per_second": "float",
    "business_hours_only": "bool",
    "cosmos_gb_year_one": "float",
    "storage_gb_year_one": "float",
    "ai_search_documents": "int",
    "monthly_growth_rate": "float",
    "max_p95_latency_ms": "int",
    "min_redundancy": "str",
    "pinned_region": "str",
}

# Wizard default-suggestion table (from references/load-profile-schema.md).
_DEFAULTS: dict[str, dict[str, Any]] = {
    "chat-agent": {
        "peak_concurrent_sessions": 50,
        "avg_requests_per_session": 8,
        "avg_tokens_per_request": 1500,
        "business_hours_only": True,
        "cosmos_gb_year_one": 50.0,
        "storage_gb_year_one": 100.0,
        "ai_search_documents": 50000,
        "monthly_growth_rate": 0.15,
    },
    "batch": {
        "peak_concurrent_sessions": 1,
        "avg_requests_per_session": 10000,
        "avg_tokens_per_request": 2000,
        "business_hours_only": False,
        "cosmos_gb_year_one": 50.0,
        "storage_gb_year_one": 100.0,
        "ai_search_documents": 50000,
        "monthly_growth_rate": 0.15,
    },
    "scheduled": {
        "peak_concurrent_sessions": 5,
        "avg_requests_per_session": 50,
        "avg_tokens_per_request": 1200,
        "business_hours_only": True,
        "cosmos_gb_year_one": 50.0,
        "storage_gb_year_one": 100.0,
        "ai_search_documents": 50000,
        "monthly_growth_rate": 0.15,
    },
    "hybrid": {
        "peak_concurrent_sessions": 25,
        "avg_requests_per_session": 100,
        "avg_tokens_per_request": 1500,
        "business_hours_only": False,
        "cosmos_gb_year_one": 50.0,
        "storage_gb_year_one": 100.0,
        "ai_search_documents": 50000,
        "monthly_growth_rate": 0.15,
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_or_prompt_profile(
    spec_path: Path,
    non_interactive: bool = False,
) -> dict[str, Any]:
    """Read SPEC § 12; prompt for any missing fields; write back."""
    if not spec_path.exists():
        raise FileNotFoundError(f"SPEC not found: {spec_path}")

    text = spec_path.read_text(encoding="utf-8")
    profile = _parse_section_12(text)

    missing = _find_missing(profile)

    if not missing:
        # Complete — return immediately; do NOT re-write the SPEC (idempotent).
        return profile

    if non_interactive:
        raise ProfileIncompleteError(
            f"load_profile incomplete; missing fields: {', '.join(missing)}"
        )

    # Interactive wizard fills any gaps then writes back.
    profile = _run_wizard(profile)
    block_text = _serialize_section_12(profile)
    _write_back(spec_path, block_text)
    return profile


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_section_12(spec_text: str) -> dict[str, Any]:
    """Extract the load_profile{} YAML block from SPEC § 12.

    Returns {} if § 12 or the load_profile{} sub-block is absent.
    Never uses the yaml module — hand-rolls the tiny 2-level schema.
    """
    # Locate § 12 heading.
    sec12_m = re.search(r"^## §\s*12\b", spec_text, re.MULTILINE)
    if not sec12_m:
        return {}

    # Slice from § 12 to the next ## heading (exclusive) or end-of-file.
    after_sec12 = spec_text[sec12_m.start():]
    next_h2 = re.search(r"\n## ", after_sec12[1:])  # skip the opening "##"
    sec12_text = after_sec12[: 1 + next_h2.start()] if next_h2 else after_sec12

    # Locate the ### `load_profile{}` subsection.
    lp_m = re.search(r"###\s+`load_profile\{\}`", sec12_text)
    if not lp_m:
        return {}

    # Slice from the heading to the next ### heading or end-of-section.
    after_lp = sec12_text[lp_m.start():]
    next_h3 = re.search(r"\n### ", after_lp[1:])
    lp_text = after_lp[: 1 + next_h3.start()] if next_h3 else after_lp

    # Find the yaml fenced code block.
    fence_m = re.search(r"```yaml\s*\n(.*?)```", lp_text, re.DOTALL)
    if not fence_m:
        return {}

    yaml_content = fence_m.group(1)
    if not re.search(r"^load_profile\s*:", yaml_content, re.MULTILINE):
        return {}

    return _parse_load_profile_yaml(yaml_content)


def _parse_load_profile_yaml(yaml_text: str) -> dict[str, Any]:
    """Parse the 2-level load_profile YAML block into a dict."""
    result: dict[str, Any] = {}
    constraints: dict[str, Any] = {}

    # Find `load_profile:` and its indentation level.
    lp_indent = None
    lp_line_idx = None
    lines = yaml_text.splitlines()
    for i, raw in enumerate(lines):
        stripped = raw.lstrip()
        if stripped.startswith("load_profile:"):
            lp_indent = len(raw) - len(stripped)
            lp_line_idx = i
            break

    if lp_indent is None:
        return {}

    child_indent = lp_indent + 2
    constraint_child_indent = lp_indent + 4
    in_constraints = False

    for raw in lines[lp_line_idx + 1:]:
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue

        indent = len(raw) - len(raw.lstrip())
        content = raw.lstrip()

        # Dedent back to root means we left the load_profile block.
        if indent <= lp_indent:
            break

        # Strip inline YAML comment.
        if " #" in content:
            content = content[: content.index(" #")].rstrip()

        if ":" not in content:
            continue

        key, _, val = content.partition(":")
        key = key.strip()
        val = val.strip()

        if indent == child_indent:
            if key == "declared_constraints":
                in_constraints = True
                continue
            in_constraints = False  # left the constraints sub-block
            if val:
                parsed = _coerce(val, key)
                if parsed is not None:
                    result[key] = parsed

        elif indent == constraint_child_indent and in_constraints:
            if val:
                parsed = _coerce(val, key)
                if parsed is not None:
                    constraints[key] = parsed

    if constraints:
        result["declared_constraints"] = constraints

    return result


def _coerce(val: str, key: str) -> Any:
    """Coerce a YAML scalar string to the appropriate Python type."""
    val = val.strip()
    if not val or val in ("~", "null"):
        return None

    ftype = _FIELD_TYPES.get(key, "str")

    if ftype == "bool":
        if val.lower() in ("true", "yes"):
            return True
        if val.lower() in ("false", "no"):
            return False
        return None

    if ftype == "int":
        try:
            return int(val)
        except ValueError:
            try:
                return int(float(val))  # handles "50.0"
            except ValueError:
                return None

    if ftype == "float":
        try:
            return float(val)
        except ValueError:
            return None

    # string — strip optional surrounding quotes
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    return val


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _find_missing(profile: dict[str, Any]) -> list[str]:
    """Return the names of every required field that is absent or empty."""
    missing: list[str] = []
    for key in REQUIRED_FIELDS:
        v = profile.get(key)
        if v is None or v == "":
            missing.append(key)

    constraints = profile.get("declared_constraints")
    if not isinstance(constraints, dict):
        missing.extend(f"declared_constraints.{k}" for k in REQUIRED_CONSTRAINTS)
    else:
        for key in REQUIRED_CONSTRAINTS:
            v = constraints.get(key)
            if v is None or v == "":
                missing.append(f"declared_constraints.{key}")

    return missing


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------


def _run_wizard(profile: dict[str, Any]) -> dict[str, Any]:
    """Interactively fill any missing load_profile fields."""
    print("\n=== threadlight-consumption-iq: load_profile wizard ===\n")

    # ── workload_class first ───────────────────────────────────────────────
    if not profile.get("workload_class"):
        print("Select workload class:")
        for i, wc in enumerate(_WORKLOAD_CLASSES, 1):
            print(f"  [{i}] {wc}")
        while True:
            raw = input("workload_class [1]: ").strip() or "1"
            if raw.isdigit() and 1 <= int(raw) <= len(_WORKLOAD_CLASSES):
                profile["workload_class"] = _WORKLOAD_CLASSES[int(raw) - 1]
                break
            elif raw in _WORKLOAD_CLASSES:
                profile["workload_class"] = raw
                break
            print(f"  Invalid. Enter 1-{len(_WORKLOAD_CLASSES)} or a class name.")

    wclass = profile["workload_class"]
    defaults = _DEFAULTS.get(wclass, _DEFAULTS["chat-agent"])

    # NOTE: peak_requests_per_second is auto-suggested after the loop because
    # it depends on peak_concurrent_sessions, avg_requests_per_session, and
    # business_hours_only — which are all gathered first.
    _skip = {"workload_class", "peak_requests_per_second"}

    # ── scalar required fields ─────────────────────────────────────────────
    for key in REQUIRED_FIELDS:
        if key in _skip:
            continue
        v = profile.get(key)
        if v is not None and v != "":
            continue  # already populated

        default = defaults.get(key)
        default_str = _fmt_scalar(default) if default is not None else ""
        ftype = _FIELD_TYPES.get(key, "str")
        prompt = f"{key} [{default_str}]: " if default_str else f"{key}: "

        while True:
            raw = input(prompt).strip()
            if not raw:
                raw = default_str
            parsed = _validate_wizard_input(raw, key, ftype)
            if parsed is not None:
                profile[key] = parsed
                break
            print(f"  Invalid value for {key} (expected {ftype})")

    # ── auto-suggest peak_requests_per_second ─────────────────────────────
    if profile.get("peak_requests_per_second") is None:
        pcs = profile.get("peak_concurrent_sessions") or 0
        ars = profile.get("avg_requests_per_session") or 0
        bho = profile.get("business_hours_only", True)
        divisor = 8 * 3600 if bho else 24 * 3600
        suggested: float = round(pcs * ars / divisor, 4) if (pcs and ars) else 0.0
        while True:
            raw = input(f"peak_requests_per_second [{suggested}]: ").strip()
            if not raw:
                raw = str(suggested)
            try:
                val = float(raw)
                if val >= 0:
                    profile["peak_requests_per_second"] = val
                    break
            except ValueError:
                pass
            print("  Expected a non-negative number.")

    # ── declared_constraints ──────────────────────────────────────────────
    constraints: dict[str, Any] = profile.get("declared_constraints") or {}
    if not isinstance(constraints, dict):
        constraints = {}

    if constraints.get("max_p95_latency_ms") is None:
        while True:
            raw = input("max_p95_latency_ms [2500]: ").strip() or "2500"
            try:
                val_i = int(raw)
                if val_i >= 0:
                    constraints["max_p95_latency_ms"] = val_i
                    break
            except ValueError:
                pass
            print("  Expected a non-negative integer.")

    _redundancy_opts = ("none", "zone-redundant", "geo-redundant")
    if not constraints.get("min_redundancy"):
        print("min_redundancy options: none | zone-redundant | geo-redundant")
        while True:
            raw = input("min_redundancy [zone-redundant]: ").strip() or "zone-redundant"
            if raw in _redundancy_opts:
                constraints["min_redundancy"] = raw
                break
            print(f"  Choose from: {', '.join(_redundancy_opts)}")

    # pinned_region is optional — empty input means absent.
    if "pinned_region" not in constraints:
        raw = input("pinned_region (optional; press Enter to skip): ").strip()
        if raw:
            constraints["pinned_region"] = raw

    profile["declared_constraints"] = constraints
    return profile


def _validate_wizard_input(raw: str, key: str, ftype: str) -> Any:
    """Validate and coerce a wizard input string; return None on failure."""
    if not raw:
        return None

    if ftype == "bool":
        if raw.lower() in ("y", "yes", "true", "1"):
            return True
        if raw.lower() in ("n", "no", "false", "0"):
            return False
        return None

    if ftype == "int":
        try:
            v = int(raw)
            return v if v >= 0 else None
        except ValueError:
            return None

    if ftype == "float":
        try:
            v = float(raw)
            return v if v >= 0 else None
        except ValueError:
            return None

    return raw  # str — accept as-is


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _serialize_section_12(profile: dict[str, Any]) -> str:
    """Produce canonical YAML for the load_profile{} block.

    Fields are emitted in REQUIRED_FIELDS order; declared_constraints last.
    Always ends with a trailing newline.
    """
    lines = ["load_profile:"]

    for key in REQUIRED_FIELDS:
        val = profile.get(key)
        if val is None:
            continue
        lines.append(f"  {key}: {_fmt_scalar(val)}")

    # Forward-compatibility: preserve unknown top-level keys.
    for key, val in profile.items():
        if key in REQUIRED_FIELDS or key == "declared_constraints":
            continue
        lines.append(f"  {key}: {_fmt_scalar(val)}")

    constraints = profile.get("declared_constraints")
    if isinstance(constraints, dict) and constraints:
        lines.append("  declared_constraints:")
        for ckey in REQUIRED_CONSTRAINTS:
            cval = constraints.get(ckey)
            if cval is not None:
                lines.append(f"    {ckey}: {_fmt_scalar(cval)}")
        if constraints.get("pinned_region") is not None:
            lines.append(f"    pinned_region: {_fmt_scalar(constraints['pinned_region'])}")
        # Forward-compatibility: preserve unknown constraint keys.
        for ckey, cval in constraints.items():
            if ckey in REQUIRED_CONSTRAINTS or ckey == "pinned_region":
                continue
            lines.append(f"    {ckey}: {_fmt_scalar(cval)}")

    return "\n".join(lines) + "\n"


def _fmt_scalar(val: Any) -> str:
    """Format a Python value as a YAML scalar string."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, float):
        # Render whole-number floats without a decimal point (e.g. 50.0 → "50").
        try:
            int_val = int(val)
            if float(int_val) == val:
                return str(int_val)
        except (ValueError, OverflowError):
            pass
        return str(val)
    return str(val)


# ---------------------------------------------------------------------------
# Write-back
# ---------------------------------------------------------------------------


def _write_back(spec_path: Path, block_text: str) -> None:
    """Splice the yaml block into SPEC § 12 under ### `load_profile{}`."""
    text = spec_path.read_text(encoding="utf-8")

    if not re.search(r"^## §\s*12\b", text, re.MULTILINE):
        raise RuntimeError("SPEC § 12 missing; run threadlight-design to scaffold")

    new_fence = f"```yaml\n{block_text}```"

    # Pattern: ### `load_profile{}` heading (any title suffix).
    lp_heading_pat = re.compile(r"### `load_profile\{\}`[^\n]*\n")
    lp_m = lp_heading_pat.search(text)

    if lp_m:
        after_heading = text[lp_m.end():]
        # Limit search to the current subsection (before next heading).
        next_heading = re.search(r"\n(?:##|###) ", after_heading)
        section_text = after_heading[: next_heading.start()] if next_heading else after_heading

        fence_m = re.search(r"```yaml\s*\n.*?```", section_text, re.DOTALL)
        if fence_m:
            abs_start = lp_m.end() + fence_m.start()
            abs_end = lp_m.end() + fence_m.end()
            new_text = text[:abs_start] + new_fence + text[abs_end:]
        else:
            # Heading exists but no yaml fence yet — insert right after heading.
            insert_pos = lp_m.end()
            new_text = text[:insert_pos] + "\n" + new_fence + "\n" + text[insert_pos:]
    else:
        # No subsection at all — append it right after the § 12 heading line.
        sec12_m = re.search(r"^## §\s*12\b[^\n]*\n", text, re.MULTILINE)
        if not sec12_m:
            raise RuntimeError("SPEC § 12 missing; run threadlight-design to scaffold")
        insert_pos = sec12_m.end()
        subsection = (
            f"\n### `load_profile{{}}` (consumed by `threadlight-consumption-iq`)\n"
            f"\n{new_fence}\n"
        )
        new_text = text[:insert_pos] + subsection + text[insert_pos:]

    spec_path.write_text(new_text, encoding="utf-8")
