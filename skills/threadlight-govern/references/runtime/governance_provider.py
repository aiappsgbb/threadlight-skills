"""Host-owned selection; bundle integrity and signature verification are injected.

No repository-relative imports: copy this package alongside your application and
supply the Task6 verifier, shared contract validator, and real signature authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
import math
import time


class GovernanceProvider(Protocol):
    def middleware(self): ...
    def health(self) -> Mapping: ...
    def policy_digest(self) -> str: ...


@dataclass(frozen=True)
class VerifiedPolicy:
    """Authenticated digest AND expiry, not unsigned Task6 bundle metadata."""
    digest: str
    expires_at: datetime


class SignatureVerifier(Protocol):
    def verify(self, bundle) -> VerifiedPolicy: ...


POINTS = {"startup": "agent_startup", "shutdown": "agent_shutdown"}
AUDIT = frozenset({"durable-audit", "audit", "decision-receipt"})
APPROVAL = frozenset({"approval", "human-approval-record"})


class AcsGovernanceProvider:
    def __init__(
        self, *, contract, bundle_path, expected_digest, bundle_verifier,
        signature_verifier: SignatureVerifier, contract_validator, safe_provider,
        environment="production", deployment_target="customer-pilot", timeout=5.0,
        max_output_bytes=1024 * 1024, approval_resolver=None, principal=None, audit=None,
        agent_version=None, image_digest=None, tenant=None, allowed_approval_roles=(),
    ):
        from .maf_agent_hooks_acs import AcsInterceptor, BoundApprovalResolver, NativeRecordSink, hooks_bundle

        self._contract = deepcopy(contract_validator(
            deepcopy(contract), deployment_target=deployment_target,
            runtime="microsoft-agent-framework",
        ))
        modes = self._contract["governance"]["environment_modes"]
        if environment not in modes or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("invalid governance environment or timeout")
        self.mode = modes[environment]
        self.timeout = timeout
        if type(max_output_bytes) is not int or max_output_bytes <= 0:
            raise ValueError("positive output byte limit required")
        self.max_output_bytes = max_output_bytes
        self.approval_resolver = approval_resolver
        self.principal = principal
        self.tenant = tenant
        if isinstance(allowed_approval_roles, str) or any(
            not isinstance(role, str) or not role.strip() for role in allowed_approval_roles
        ):
            raise ValueError("approval roles must be a collection of nonblank role identifiers")
        self.allowed_approval_roles = tuple(sorted(set(allowed_approval_roles)))
        self.audit = audit
        self.agent_version = agent_version
        self.image_digest = image_digest
        self._path = Path(bundle_path)
        self._digest = expected_digest
        self._verify_bundle = bundle_verifier
        self._signature = signature_verifier
        self._safe_provider = safe_provider
        self._engine = None
        self._trust = None
        self._deadline = None
        self._last_now = None
        self._claimed = False
        self._bindings = {}
        for tool in self._contract["tools"]:
            if tool["policy_binding"] is not None:
                for point in tool["intervention_points"]:
                    self._bind(tool["id"], point, tool)
        for binding in self._contract["governance"]["lifecycle_bindings"]:
            self._bind(None, binding["lifecycle_point"], binding)
        self._refresh()
        self._bundle = hooks_bundle(
            {"acs": AcsInterceptor(self)}, mode=self.mode, timeout=timeout + 1,
            resolver=BoundApprovalResolver(self),
            record_sink=NativeRecordSink(self),
        )

    def _bind(self, tool, point, binding):
        point = POINTS.get(point, point)
        key = f"{tool or 'lifecycle'}:{point}"
        self._bindings[key] = {
            "tool": tool, "point": point, "policy": binding["policy_binding"],
            "path": binding["enforcement_path"], "requires": tuple(binding.get("requires", [])),
            "healthy": False, "ready": False, "last_failure": None,
            "reason": "threadlight:policy_unavailable",
        }

    def _refresh(self):
        import yaml
        from agent_control_specification import AgentControl
        try:
            bundle = self._verify_bundle(self._path, expected_digest=self._digest)
            trust = self._signature.verify(bundle)
            now = self._now()
            if (not isinstance(trust, VerifiedPolicy) or trust.digest != self._digest
                    or trust.expires_at.tzinfo is None
                    or trust.expires_at <= now):
                raise ValueError("policy authentication unavailable")
            if self._trust is None:
                self._trust = trust
                self._deadline = time.monotonic() + (trust.expires_at - now).total_seconds()
            if trust != self._trust or time.monotonic() >= self._deadline:
                raise ValueError("policy authorization changed or expired")

            def points(path):
                document = yaml.safe_load(path.read_text())
                combined = {}
                for parent in document.get("extends", []):
                    combined.update(points(path.parent / parent))
                combined.update(document.get("intervention_points", {}))
                return combined

            declared = points(bundle.manifest_path)
            if self._engine is None:
                self._engine = AgentControl.from_path(str(bundle.manifest_path))
            for binding in self._bindings.values():
                config = declared.get(binding["point"], {})
                requirements = set(binding["requires"])
                supported = binding["tool"] is None or binding["point"] in {
                    "pre_tool_call", "post_tool_call",
                }
                # Approval and pre-effect durability need a pre-tool binding.
                if binding["point"] == "post_tool_call" and requirements & (AUDIT | APPROVAL):
                    coverage = set().union(*(
                        set(b["requires"]) for b in self._bindings.values()
                        if b["point"] == "pre_tool_call"
                        and (b["tool"] is None or b["tool"] == binding["tool"])
                    ))
                    # Aliases represent the same required control.
                    if ((requirements & AUDIT and not coverage & AUDIT)
                            or (requirements & APPROVAL and not coverage & APPROVAL)):
                        supported = False
                binding["ready"] = (
                    supported and binding["path"] == "local-agent-hooks"
                    and config.get("policy", {}).get("id") == binding["policy"]
                    and (not requirements & AUDIT or self.audit is not None)
                    and (not requirements & APPROVAL
                         or self._approval_ready())
                )
                binding["healthy"] = binding["ready"] and binding["last_failure"] is None
                binding["reason"] = (binding["last_failure"] if binding["ready"]
                                     else "threadlight:binding_unavailable")
            if trust.expires_at <= self._now() or time.monotonic() >= self._deadline:
                raise ValueError("policy authorization expired during verification")
        except Exception:
            self._engine = None
            for binding in self._bindings.values():
                binding.update(ready=False, healthy=False, reason="threadlight:policy_unavailable")

    def _now(self):
        now = datetime.now(timezone.utc)
        # A wall-clock rollback cannot extend an existing authorization.
        self._last_now = max(now, self._last_now) if self._last_now else now
        return self._last_now

    def _approval_ready(self):
        return (all(isinstance(v, str) and bool(v.strip()) for v in (self.principal, self.tenant))
                and bool(self.allowed_approval_roles)
                and callable(getattr(self.approval_resolver, "resolve", None))
                and callable(getattr(self.approval_resolver, "verify", None)))

    def _tool_bindings(self, name):
        return [
            b for b in self._bindings.values()
            if b["tool"] == name or (
                b["tool"] is None and b["point"] in {"pre_tool_call", "post_tool_call"}
            )
        ]

    def _select(self, context):
        point = context["interception_point"]
        tool = context.get("tool_call", {}).get("name")
        return [
            b for b in self._bindings.values()
            if b["point"] == point and (b["tool"] is None or b["tool"] == tool)
        ]

    def middleware(self):
        return self._bundle

    def policy_digest(self):
        return self._digest

    def health(self):
        return {
            "policy_digest": self._digest, "mode": self.mode,
            "scope": "local configuration and last evaluation; not live deployment proof",
            "bindings": {
                key: {"healthy": b["healthy"], "reason": b["reason"],
                      "status": ("observational" if b["point"] == "agent_shutdown"
                                 else "unverified" if self.mode == "enforce" else "observed"),
                      "policy_digest": self._digest, "mode": self.mode}
                for key, b in self._bindings.items()
            },
        }
