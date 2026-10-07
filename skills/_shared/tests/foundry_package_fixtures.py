"""Complete full-Foundry-package manifest fixture shared by gate consumers."""
from __future__ import annotations

import json
from pathlib import Path


def complete_manifest(agent_name: str = "returns-triage") -> dict:
    return {
        "schema": "threadlight-foundry-package/v1",
        "agent": {
            "kind": "foundry-hosted",
            "agent_name": agent_name,
            "agent_id": f"{agent_name}:3",
            "agent_version": "3",
            "fallback_active": False,
        },
        "telemetry": {
            "app_insights_connection_id": "/subscriptions/x/resourceGroups/rg/providers/Microsoft.Insights/components/ai",
            "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        },
        "evaluation": {
            "eval_run_id": "evalrun_123",
            "data_source_type": "azure_ai_target_completions",
            "target": {"type": "azure_ai_agent", "name": agent_name},
            "builtin_evaluators": ["task_adherence", "intent_resolution"],
            "custom_rubric_evaluators": [{
                "name": "returns-policy-rubric",
                "anchors": ["1: cites no policy", "5: cites the exact policy clause"],
                "derived_from_acceptance_criteria": ["AC-1", "AC-2"],
                "threshold": 3.5,
                "score": 4.2,
            }],
        },
        "continuous_evaluation": {
            "rule_id": "cer_returns_triage",
            "enabled": True,
            "agent_name": agent_name,
        },
    }


def seed_foundry_package(root: Path, value: dict | None = None) -> Path:
    path = Path(root) / "specs" / "foundry-package-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value if value is not None else complete_manifest()), encoding="utf-8")
    return path
