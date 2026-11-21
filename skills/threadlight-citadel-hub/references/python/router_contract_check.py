#!/usr/bin/env python3
"""Access-contract gate for model router deployments behind a Citadel hub.

An access contract that allows a model router deployment without a model
subset implicitly allows every pool model in the region (31 served in a
Nov 2026 test). Azure Policy is evaluated when a deployment is created or
updated, not per request, so the deployment subset is the real gate
(https://learn.microsoft.com/en-us/azure/foundry/how-to/model-router-policy).

Rules:
  ROUTER-SUBSET-001  an allowed model resolves to a `model-router` deployment
                     with no `properties.routing.models` subset   -> violation
  ROUTER-AUTO-001    `auto` (model router without a deployment, whole pool)
                     is allowed                                    -> violation
  UNRESOLVED         an allowed model has no deployment in the account
                     (wrong account, or a router on another backend)
                                                                  -> violation
An empty allowedModels grants every deployment, so every router is checked.

Inputs: the contract's product policy XML (`allowedModels` variable, as in
the accelerator's ai-product-policy.xml) or an explicit list, and the Foundry
deployments as returned by `az cognitiveservices account deployment list`.
Exit 0 when no violations, 1 otherwise. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROUTER_MODEL = "model-router"


def allowed_models(policy_xml: str) -> list[str]:
    m = re.search(r'<set-variable\s+name="allowedModels"\s+value="([^"]*)"', policy_xml)
    if not m:
        raise ValueError("policy XML has no allowedModels set-variable")
    return [x.strip() for x in m.group(1).split(",") if x.strip()]


def check(allowed: list[str], deployments: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {str(d.get("name", "")).lower(): d for d in deployments}
    violations: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    routers: dict[str, Any] = {}
    for model in allowed:
        key = model.lower()
        if key == "auto":
            violations.append({"code": "ROUTER-AUTO-001", "deployment": model,
                               "detail": "Auto routes across the whole regional pool and has no subset; "
                                         "allow a model router deployment with a subset instead"})
            continue
        dep = by_name.get(key)
        if dep is None:
            violations.append({"code": "UNRESOLVED", "deployment": model,
                               "detail": "no deployment with this name in the account; check the account "
                                         "or the backend that serves it, the gate cannot pass unchecked"})
            continue
        _check_router(dep, routers, violations)
    if not allowed:
        warnings.append({"code": "ALL-DEPLOYMENTS", "deployment": "*",
                         "detail": "empty allowedModels grants every deployment; every router was checked"})
        for dep in deployments:
            _check_router(dep, routers, violations)
    return {"ok": not violations, "violations": violations, "warnings": warnings, "routers": routers}


def _check_router(dep: dict[str, Any], routers: dict[str, Any], violations: list[dict[str, str]]) -> None:
    props = dep.get("properties") or {}
    if str((props.get("model") or {}).get("name", "")).lower() != ROUTER_MODEL:
        return
    subset = [m.get("name") for m in ((props.get("routing") or {}).get("models") or []) if m.get("name")]
    routers[dep["name"]] = {"mode": (props.get("routing") or {}).get("mode"), "subset": subset}
    if not subset:
        violations.append({"code": "ROUTER-SUBSET-001", "deployment": dep["name"],
                           "detail": "model router deployment without properties.routing.models: "
                                     "granting it grants every pool model in the region"})


def _az_deployments(account: str, resource_group: str, subscription: str | None) -> list[dict[str, Any]]:
    cmd = ["az", "cognitiveservices", "account", "deployment", "list", "-n", account,
           "-g", resource_group, "-o", "json"]
    if subscription:
        cmd += ["--subscription", subscription]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return json.loads(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--policy-xml", type=Path, help="access-contract product policy XML")
    src.add_argument("--allowed", help="comma-separated allowed models")
    dep = p.add_mutually_exclusive_group(required=True)
    dep.add_argument("--deployments", type=Path, help="JSON from az cognitiveservices account deployment list")
    dep.add_argument("--account", help="Foundry account name (read with az)")
    p.add_argument("--resource-group")
    p.add_argument("--subscription")
    args = p.parse_args(argv)
    allowed = (allowed_models(args.policy_xml.read_text(encoding="utf-8")) if args.policy_xml
               else [x.strip() for x in args.allowed.split(",") if x.strip()])
    deployments = (json.loads(args.deployments.read_text(encoding="utf-8")) if args.deployments
                   else _az_deployments(args.account, args.resource_group or "", args.subscription))
    result = check(allowed, deployments)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
