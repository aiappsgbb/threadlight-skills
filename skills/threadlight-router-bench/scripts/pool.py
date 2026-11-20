#!/usr/bin/env python3
"""Model router pool pre-flight via the ARM models API.

Lists the models a model router version can route to in a region instead of
hard-coding the pool size: ``GET /subscriptions/{id}/providers/
Microsoft.CognitiveServices/locations/{region}/models?api-version=2025-06-01``,
then read ``capabilities.models`` on the ``model-router`` entry.

Docs: https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router-models
"""
from __future__ import annotations

import collections
import json
import subprocess
from typing import Any, Callable

ARM_API_VERSION = "2025-06-01"
DEFAULT_ROUTER_VERSION = "2025-11-18"


def fetch_pool(subscription: str, region: str,
               runner: Callable[[list[str]], str] | None = None) -> dict[str, Any]:
    url = (f"https://management.azure.com/subscriptions/{subscription}/providers/"
           f"Microsoft.CognitiveServices/locations/{region}/models?api-version={ARM_API_VERSION}")
    run = runner or _default_runner
    return json.loads(run(["rest", "--method", "get", "--url", url, "-o", "json"]))


def parse_router_pool(doc: dict[str, Any] | list[Any],
                      version: str = DEFAULT_ROUTER_VERSION) -> dict[str, Any]:
    entries = doc.get("value", []) if isinstance(doc, dict) else doc
    for entry in entries:
        model = entry.get("model") or {}
        if model.get("name") != "model-router" or model.get("version") != version:
            continue
        raw = (model.get("capabilities") or {}).get("models") or "[]"
        models = json.loads(raw) if isinstance(raw, str) else list(raw)
        return {"router_version": version, "count": len(models), "models": models,
                "formats": dict(collections.Counter(m.get("format", "?") for m in models)),
                "skus": sorted({s.get("name") for s in model.get("skus") or [] if s.get("name")})}
    raise LookupError(f"model-router version {version} not found in the ARM models listing")


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(["az", *args], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("az " + " ".join(args[:3]) + " failed:\n" + proc.stderr.strip())
    return proc.stdout
