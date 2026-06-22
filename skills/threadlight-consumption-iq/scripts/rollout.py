"""
Pre-sales phased rollout profile (`rollout.py`).

Where `load_profile_wizard.py` captures **one** steady-state production load
for a deployed pilot, a *rollout profile* captures **N adoption phases** — a
land-and-expand story (e.g. POC -> expansion -> business-wide). Each phase is a
different topology + load + hardening posture, so the seller can show the
customer how monthly cost steps up as adoption grows, instead of quoting a
single number.

This is the pre-sales front-end the post-deploy chain explicitly lacks. It is
read by the `estimate` orchestrator, which projects every resource at each
phase's load and posture.

Parsing is **fail-fast**: an incomplete phase raises `RolloutProfileError`
(never project on guessed numbers — mirrors the load-profile wizard's exit-4
discipline). Accepts JSON (machine artefact, like `cost-manifest.json`) or a
small YAML subset (human-authored).

Stdlib only. See `references/rollout-profile-schema.md`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from load_profile_wizard import _find_missing as _missing_load_fields  # noqa: E402

POSTURES: set[str] = {"demo", "production", "production-hardened"}
AUDIENCES: set[str] = {"internal", "customer"}
_DEFAULT_AUDIENCE = "internal"


class RolloutProfileError(RuntimeError):
    """Rollout profile is structurally invalid or has an incomplete phase."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_rollout_profile(path: str | Path) -> dict[str, Any]:
    """Load + validate a rollout profile from `.json` or `.yaml`/`.yml`."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"rollout profile not found: {path}")
    text = path.read_text()
    if path.suffix.lower() == ".json":
        raw = json.loads(text)
    else:
        raw = parse_rollout_yaml(text)
    return validate_rollout_profile(raw)


def validate_rollout_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized copy, or raise `RolloutProfileError` on first fault."""
    if not isinstance(profile, dict):
        raise RolloutProfileError("rollout profile must be a mapping")

    phases = profile.get("phases")
    if not isinstance(phases, list) or not phases:
        raise RolloutProfileError("rollout profile must declare a non-empty `phases` list")

    seen_ids: list[str] = []
    norm_phases: list[dict[str, Any]] = []
    for i, phase in enumerate(phases):
        if not isinstance(phase, dict):
            raise RolloutProfileError(f"phases[{i}] must be a mapping")

        pid = phase.get("id")
        if not pid:
            raise RolloutProfileError(f"phases[{i}] is missing required `id`")
        if pid in seen_ids:
            raise RolloutProfileError(f"duplicate phase id '{pid}'")
        seen_ids.append(pid)

        if not phase.get("label"):
            raise RolloutProfileError(f"phase '{pid}' is missing required `label`")

        posture = phase.get("posture")
        if posture not in POSTURES:
            raise RolloutProfileError(
                f"phase '{pid}' has invalid posture {posture!r}; "
                f"must be one of {sorted(POSTURES)}"
            )

        audience = phase.get("audience", _DEFAULT_AUDIENCE)
        if audience not in AUDIENCES:
            raise RolloutProfileError(
                f"phase '{pid}' has invalid audience {audience!r}; "
                f"must be one of {sorted(AUDIENCES)}"
            )

        load_profile = phase.get("load_profile")
        if not isinstance(load_profile, dict):
            raise RolloutProfileError(
                f"phase '{pid}' is missing required `load_profile` mapping"
            )
        missing = _missing_load_fields(load_profile)
        if missing:
            detail = ", ".join(f"phases[{i}].load_profile.{m}" for m in missing)
            raise RolloutProfileError(
                f"phase '{pid}' load_profile is incomplete: {detail}. "
                "Pre-sales estimates are never produced on guessed numbers."
            )

        norm_phases.append(
            {
                "id": pid,
                "label": phase["label"],
                "audience": audience,
                "posture": posture,
                "load_profile": load_profile,
                **({"benchmark": phase["benchmark"]} if phase.get("benchmark") else {}),
            }
        )

    current_phase = profile.get("current_phase") or seen_ids[0]
    if current_phase not in seen_ids:
        raise RolloutProfileError(
            f"current_phase '{current_phase}' not found in phases {seen_ids}"
        )

    normalized: dict[str, Any] = {
        "customer": profile.get("customer", "Generic Pilot"),
        "currency": profile.get("currency", "USD"),
        "phases": norm_phases,
        "current_phase": current_phase,
    }
    if profile.get("discount"):
        normalized["discount"] = profile["discount"]
    if profile.get("benchmark"):
        normalized["benchmark"] = profile["benchmark"]
    return normalized


# ---------------------------------------------------------------------------
# Minimal YAML subset parser (maps, lists-of-maps, scalars)
# ---------------------------------------------------------------------------

def parse_rollout_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by rollout profiles.

    Supports nested maps (indent), lists of maps (`- key: value`), scalar
    lists (`- value`), comments (`#`), and scalar coercion. Not a general
    YAML implementation — just enough for hand-authored rollout files.
    """
    tokens: list[tuple[int, str]] = []
    for raw in text.splitlines():
        # Strip trailing ` # comment` (conservative: requires a leading space).
        if " #" in raw:
            raw = raw.split(" #", 1)[0]
        if raw.lstrip().startswith("#"):
            continue
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        tokens.append((indent, raw.strip()))
    if not tokens:
        return {}
    value, _ = _parse_block(tokens, 0, tokens[0][0])
    return value


def _parse_block(tokens: list[tuple[int, str]], idx: int, indent: int) -> tuple[Any, int]:
    _, content = tokens[idx]
    if content == "-" or content.startswith("- "):
        return _parse_list(tokens, idx, indent)
    return _parse_map(tokens, idx, indent)


def _parse_map(tokens: list[tuple[int, str]], idx: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while idx < len(tokens):
        ind, content = tokens[idx]
        if ind < indent or ind > indent:
            break
        key, _, rawval = content.partition(":")
        key = key.strip()
        rawval = rawval.strip()
        if rawval == "":
            if idx + 1 < len(tokens) and tokens[idx + 1][0] > indent:
                child, idx = _parse_block(tokens, idx + 1, tokens[idx + 1][0])
                result[key] = child
            else:
                result[key] = None
                idx += 1
        else:
            result[key] = _coerce_scalar(rawval)
            idx += 1
    return result, idx


def _parse_list(tokens: list[tuple[int, str]], idx: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while idx < len(tokens):
        ind, content = tokens[idx]
        if ind != indent or not (content == "-" or content.startswith("- ")):
            break
        rest = content[1:].strip()
        if rest == "":
            child, idx = _parse_block(tokens, idx + 1, tokens[idx + 1][0])
            items.append(child)
        elif ":" in rest:
            # Inline first map key, e.g. "- id: poc". Continuation keys sit at
            # indent + 2 (the columns the dash + space occupied).
            virt_indent = indent + 2
            tokens[idx] = (virt_indent, rest)
            child, idx = _parse_map(tokens, idx, virt_indent)
            items.append(child)
        else:
            items.append(_coerce_scalar(rest))
            idx += 1
    return items, idx


def _coerce_scalar(s: str) -> Any:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s
