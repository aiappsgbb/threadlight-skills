"""Resolve SPEC ``agent_type`` against ``runtime-policy.json`` (Foundry Copilot harness opt-in).

The Foundry Microsoft Copilot harness (a prompt agent with ``harness.type:
microsoft_copilot_preview``) is preview. ``agent_type: harness`` resolves to
``harness`` only when the user opted in explicitly (``harness_opt_in_source: user``
with ``harness_opt_in_evidence``), acknowledged the preview and answered every
``harness_fit`` question without hitting a disqualifier. An agent-written value, a
missing field, an unknown fit answer or any disqualifier resolves to the hosted
default (``skills/_shared/foundry-only-agents.md`` item 2d).

``fit_verdict`` answers the separate pre-sales question "would the harness fit?"
from the ``harness_fit`` answers alone (used by threadlight-qualify and the design
template), without implying any opt-in.

Stdlib only, so blueprint tests, generate_only and the Cowork qualify bundle can
all call it.

CLI: ``python skills/_shared/agent_type_policy.py --spec specs/SPEC.md [--json | --verdict-md]``
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).resolve().parents[1] / "threadlight-design" / "references" / "runtime-policy.json"
_NULLS = ("", "null", "~", "none")


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    # Cowork qualify bundle: the policy rides inside cost-runtime.zip as package data.
    from importlib import resources
    return json.loads(resources.files("harness_policy").joinpath("runtime-policy.json").read_text(encoding="utf-8"))


def _clean(raw: str, nulls: tuple[str, ...] = _NULLS) -> str | None:
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        value = raw[1:end] if end > 0 else raw[1:]
    else:
        value = raw.split(" #", 1)[0].strip()
    return None if value.lower() in nulls else value


def spec_scalar(spec: str, key: str) -> str | None:
    """Top-level ``key: value`` from SPEC YAML; quoted values keep a ``#``, comments are dropped."""
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", spec, re.M)
    return _clean(m.group(1)) if m else None


def spec_mapping(spec: str, key: str) -> dict[str, str | None] | None:
    """A one-level ``key:`` mapping (indented ``child: value`` lines) from SPEC YAML, or None."""
    m = re.search(rf"^{re.escape(key)}:[ \t]*(?:#.*)?$", spec, re.M)
    if not m:
        return None
    values: dict[str, str | None] = {}
    for line in spec[m.end():].splitlines()[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        child = re.match(r"^[ \t]+([A-Za-z_][\w-]*):[ \t]*(.*)$", line)
        if not child:
            break
        # "none" is a real answer here (e.g. consequential_write_seam: none), not a null.
        values[child.group(1)] = _clean(child.group(2), ("", "null", "~"))
    return values


def _norm_region(value: str | None) -> str:
    return re.sub(r"[\s_-]", "", (value or "").lower())


def _bool(value: str | None) -> bool | None:
    lowered = (value or "").lower()
    return True if lowered == "true" else False if lowered == "false" else None


def evaluate_fit(fit: dict[str, Any], hosting: dict[str, Any], voice_frontend: str | None = None) -> list[dict[str, str]]:
    """Disqualifiers hit by the ``harness_fit`` answers; a missing or unknown answer is a hit."""
    hits: list[dict[str, str]] = []
    for rule in hosting["harness_disqualifiers"]:
        raw = fit.get(rule["fit_field"])
        answer = raw if raw is None or isinstance(raw, str) else str(raw).lower()
        if "disqualifies_unless" in rule:
            hit = _bool(answer) is not rule["disqualifies_unless"]
        elif "allowed_values_ref" in rule:
            allowed = {_norm_region(v) for v in hosting[rule["allowed_values_ref"]]}
            hit = _norm_region(answer) not in allowed
        else:
            hit = (answer or "").lower() not in rule["allowed_values"]
        for spec_key, spec_value in (rule.get("also_when_spec") or {}).items():
            hit = hit or (spec_key == "voice_frontend" and (voice_frontend or "").lower() == spec_value)
        if hit:
            hits.append({"id": rule["id"], "reason": f"{rule['id']}: harness_fit.{rule['fit_field']}={raw!r} ({rule['reason']})"})
    return hits


def fit_verdict(fit: dict[str, Any] | None, policy: dict[str, Any] | None = None,
                voice_frontend: str | None = None) -> dict[str, Any]:
    """``{verdict, reasons, disqualifiers, advisories}`` for the harness fit answers.

    ``not-assessed`` when no answer was recorded, ``not-fit`` on any disqualifier
    (including an unknown answer), else ``fit``. A fit verdict is not an opt-in.
    """
    hosting = (policy or load_policy())["agent_hosting"]
    if not fit:
        return {"verdict": "not-assessed", "reasons": [], "disqualifiers": [], "advisories": []}
    hits = evaluate_fit(fit, hosting, voice_frontend)
    advisories = []
    for question in hosting["harness_fit_questions"]:
        advisory = question.get("advisory_when")
        if advisory is not None and str(fit.get(question["field"])).lower() == str(advisory).lower():
            advisories.append(question["advisory"])
    return {"verdict": "not-fit" if hits else "fit", "reasons": [h["reason"] for h in hits],
            "disqualifiers": [h["id"] for h in hits], "advisories": advisories}


def verdict_markdown(verdict: dict[str, Any]) -> str:
    """The SPEC § 11e "Harness fit verdict" block (design template)."""
    lines = [f"**Harness fit verdict: {verdict['verdict']}**", "", "| Reason | Kind |", "| --- | --- |"]
    lines += [f"| {r} | disqualifier |" for r in verdict["reasons"]]
    lines += [f"| {a} | advisory |" for a in verdict["advisories"]]
    if not verdict["reasons"] and not verdict["advisories"]:
        lines.append("| no disqualifier hit | - |" if verdict["verdict"] == "fit" else "| harness_fit not answered | - |")
    return "\n".join(lines) + "\n"


def resolve(spec: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return ``{requested, agent_type, reasons, disqualifiers}`` for the SPEC text."""
    hosting = (policy or load_policy())["agent_hosting"]
    requested = (spec_scalar(spec, "agent_type") or hosting["default"]).lower()
    result: dict[str, Any] = {"requested": requested, "agent_type": requested, "reasons": [], "disqualifiers": []}
    if requested != "harness":
        return result

    reasons: list[str] = []
    if (spec_scalar(spec, "harness_opt_in_source") or "").lower() not in hosting["harness_opt_in_source_allowed"]:
        reasons.append("harness_opt_in_source must be 'user' (an agent-written agent_type: harness is not an opt-in)")
    if not spec_scalar(spec, "harness_opt_in_evidence"):
        reasons.append("harness_opt_in_evidence (quote of the user's request) is missing")
    if (spec_scalar(spec, "preview_acknowledged") or "").lower() != "true":
        reasons.append("preview_acknowledged must be true (the Copilot harness is preview)")
    fit = spec_mapping(spec, "harness_fit")
    if not fit:
        reasons.append("harness_fit record is missing")
        fit = {}

    hits = evaluate_fit(fit, hosting, voice_frontend=spec_scalar(spec, "voice_frontend"))
    disqualifiers = [hit["id"] for hit in hits]
    reasons += [hit["reason"] for hit in hits]

    result["reasons"], result["disqualifiers"] = reasons, disqualifiers
    if reasons:
        result["agent_type"] = hosting["refusal_route"]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve SPEC agent_type against runtime-policy.json")
    parser.add_argument("--spec", required=True, help="path to specs/SPEC.md")
    parser.add_argument("--json", action="store_true", help="print the full JSON result")
    parser.add_argument("--verdict-md", action="store_true", help="print the SPEC § 11e harness fit verdict block")
    args = parser.parse_args(argv)
    spec = Path(args.spec).read_text(encoding="utf-8")
    if args.verdict_md:
        print(verdict_markdown(fit_verdict(spec_mapping(spec, "harness_fit"),
                                           voice_frontend=spec_scalar(spec, "voice_frontend"))), end="")
        return 0
    result = resolve(spec)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["agent_type"] + ("" if not result["reasons"] else " (" + "; ".join(result["reasons"]) + ")"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
