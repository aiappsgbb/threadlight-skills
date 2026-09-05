"""Pinned native MAF adapter; no synthetic hook dispatch or receipt write-back."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import uuid

from agent_framework import AgentMiddleware, ChatMiddleware, MiddlewareBundle, ResponseStream

from agent_hooks import ALLOW, Decision, Transform, Verdict
from .evidence import ApprovalGrant, ApprovalIntent, digest
from .governance_provider import AUDIT, APPROVAL


def hooks_bundle(interceptors, **kwargs):
    """One shared native bundle construction path (also exercised by the CTK)."""
    from agent_framework import create_agent_hooks_middleware
    return create_agent_hooks_middleware(interceptors, **kwargs)


def action_hash(provider, context, target=None):
    return digest({
        "policy_hash": provider.policy_digest(), "principal": provider.principal,
        "agent": context["agent"], "session": context["session"],
        "point": context["interception_point"], "tool": context.get("tool_call"),
        "target": context["target"] if target is None else target,
    })


def binding_failure(selected, reason):
    for binding in selected:
        binding.update(healthy=False, last_failure=reason, reason=reason)


def binding_success(selected):
    for binding in selected:
        binding.update(healthy=True, last_failure=None, reason=None)


def receipt(provider, selected, context, decision, target=None):
    required = any(AUDIT.intersection(b["requires"]) for b in selected)
    if provider.audit is None:
        return not required
    try:
        provider.audit.append(
            correlation_id=digest(context["session"]), decision=decision,
            action_hash=action_hash(provider, context, target),
            policy_hash=provider.policy_digest(),
            agent_version=provider.agent_version, image_digest=provider.image_digest,
        )
        return True
    except Exception:
        binding_failure(selected, "threadlight:audit_unavailable")
        return not required


class BoundApprovalResolver:
    """Native ApprovalResolver, with a separately trusted Task8 service contract."""
    def __init__(self, provider):
        self.provider = provider

    async def resolve(self, request):
        from agent_hooks import ApprovalOutcome, ApprovalResolution
        p = self.provider
        deny = Verdict.deny(reason="threadlight:approval_unavailable")
        verdict = deny
        selected = p._select(request.context)
        try:
            if (not selected or not request.context_identity or not p.principal
                    or p.approval_resolver is None):
                raise ValueError("approval unavailable")
            intent = ApprovalIntent(
                action_hash=action_hash(p, request.context), policy_hash=p.policy_digest(),
                principal=p.principal, agent_id=request.context["agent"]["id"],
                session_id=request.context["session"]["id"],
                context_identity=request.context_identity, nonce=uuid.uuid4().hex,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=p.timeout),
            )
            grant = await asyncio.wait_for(p.approval_resolver.resolve(intent), p.timeout)
            p._refresh()
            if (not isinstance(grant, ApprovalGrant) or type(grant.approved) is not bool
                    or grant.intent != intent or intent.expires_at <= datetime.now(timezone.utc)
                    or not all(b["ready"] for b in selected)):
                raise ValueError("approval binding invalid")
            binding_success(selected)
            if grant.approved and receipt(p, selected, request.context, "allow"):
                verdict = ALLOW
        except Exception:
            binding_failure(selected, "threadlight:approval_unavailable")
        return ApprovalResolution(
            outcome=ApprovalOutcome.APPROVE if verdict is ALLOW else ApprovalOutcome.REJECT,
            context_identity=request.context_identity, verdict=verdict,
        )


class AcsInterceptor:
    def __init__(self, provider):
        self.provider = provider

    async def intercept(self, context):
        p = self.provider
        selected = p._select(context)
        if context["interception_point"] == "pre_tool_call":
            bindings = [b for b in p._bindings.values()
                        if b["tool"] == context["tool_call"]["name"]]
            if bindings:
                p._refresh()
                if not all(b["ready"] for b in bindings):
                    return Verdict.deny(reason="threadlight:policy_unavailable")
        if not selected:
            return ALLOW
        p._refresh()
        if not all(b["ready"] for b in selected):
            return Verdict.deny(reason="threadlight:policy_unavailable")
        try:
            identity = {
                "agent_id": context["agent"]["id"],
                "session_id": context["session"]["id"],
                "policy_hash": p.policy_digest(),
                "principal": p.principal, "action_hash": action_hash(p, context),
            }
            snapshot = {
                name: deepcopy(context[name])
                for name in ("input", "messages", "response", "tool_call",
                             "output", "agent_init", "summary") if name in context
            }
            if "tool_result" in context:
                snapshot["tool_result"] = deepcopy(context["tool_result"]["value"])
            snapshot["safe"] = deepcopy(p._safe_provider(identity))
            result = await asyncio.wait_for(p._engine.evaluate_intervention_point(
                context["interception_point"], snapshot, mode="enforce",
            ), timeout=p.timeout)
            from agent_control_specification import InterventionPointResult
            if not isinstance(result, InterventionPointResult):
                raise ValueError("invalid ACS result")
            decision = result.verdict.decision.value
            if decision not in {"allow", "deny", "escalate", "transform"}:
                raise ValueError("invalid ACS decision")
            if (result.verdict.reason or "").startswith(("runtime_error", "host_error")):
                raise ValueError("ACS engine unavailable")
            if decision == "transform":
                if not result.transformed_policy_target_applied:
                    raise ValueError("ACS did not apply transform")
                value = result.transformed_policy_target
                point = context["interception_point"]
                if point in ("agent_startup", "agent_shutdown"):
                    raise ValueError("non-transformable lifecycle point")
                if point == "pre_tool_call" and not isinstance(value, dict):
                    raise ValueError("tool arguments must be an object")
                if point == "output" and len(json.dumps(value).encode()) > p.max_output_bytes:
                    return Verdict.deny(reason="threadlight:output_limit")
                # A transform cannot bypass a separately required approval.
                if any(APPROVAL.intersection(b["requires"]) for b in selected):
                    return Verdict.deny(reason="threadlight:transform_requires_approval")
                verdict = Verdict(
                    decision=Decision.TRANSFORM, reason="threadlight:policy_transform",
                    transform=Transform("$target", value),
                )
                binding_success(selected)
                if not receipt(p, selected, context, "transform", value):
                    return Verdict.deny(reason="threadlight:audit_unavailable")
                return verdict
            if result.transformed_policy_target_applied or result.verdict.transform is not None:
                raise ValueError("unexpected ACS transform")
            binding_success(selected)
            if decision == "escalate" or (
                decision == "allow" and any(APPROVAL.intersection(b["requires"]) for b in selected)
            ):
                return Verdict.escalate(reason="threadlight:policy_escalate")
            if not receipt(p, selected, context, decision):
                return Verdict.deny(reason="threadlight:audit_unavailable")
            if decision == "allow":
                return ALLOW
            return Verdict.deny(reason="threadlight:policy_deny")
        except Exception:
            binding_failure(selected, "threadlight:engine_failure")
            return Verdict.deny(reason="threadlight:engine_failure")


class OutputLimitExceeded(RuntimeError):
    def __init__(self):
        super().__init__("threadlight:output_limit")


class _RunBoundary(AgentMiddleware):
    def __init__(self, provider):
        self.provider = provider

    async def process(self, context, call_next):
        # Options are host-owned: per-run callers must not override store=False.
        context.options = {**(context.options or {}), "store": False}
        _check_tools(context.tools or [])
        await call_next()
        if any(b["point"] == "output" for b in self.provider._bindings.values()):
            if isinstance(context.result, ResponseStream):
                context.result.with_transform_hook(_bound_updates(self.provider.max_output_bytes))
            elif context.result is not None:
                _bound_updates(self.provider.max_output_bytes)(context.result)


def _bound_updates(limit):
    size = 0

    def bounded(update):
        nonlocal size
        size += len(update.to_json().encode())
        if size > limit:
            raise OutputLimitExceeded()
        return update
    return bounded


class _ChatBoundary(ChatMiddleware):
    def __init__(self, provider):
        self.provider = provider

    async def process(self, context, call_next):
        context.options = {**(context.options or {}), "store": False}
        await call_next()
        p = self.provider
        if not any(b["point"] == "output" for b in p._bindings.values()):
            return
        bounded = _bound_updates(p.max_output_bytes)
        if isinstance(context.result, ResponseStream):
            context.result.with_transform_hook(bounded)
        elif context.result is not None:
            bounded(context.result)


def _check_tools(tools):
    from agent_framework import FunctionTool
    if any(not isinstance(tool, FunctionTool) and not callable(tool) for tool in tools):
        raise ValueError("provider-hosted tools are unsupported by local Agent Hooks")


def create_governed_agent(provider, *, client, middleware=(), default_options=None, **kwargs):
    from agent_framework import Agent
    if provider._claimed:
        raise ValueError("each agent requires its own governance provider and bundle")
    hooks = provider.middleware()
    _check_tools(kwargs.get("tools") or [])
    installed = list(middleware)
    if any(isinstance(m, MiddlewareBundle) and m is not hooks for m in installed):
        raise ValueError("foreign bundles must not share the governance boundary")
    if hooks in installed:
        if installed[0] is not hooks or installed.count(hooks) != 1:
            raise ValueError("exactly one Agent Hooks bundle must be first")
    else:
        installed.insert(0, hooks)
    installed.extend([_RunBoundary(provider), _ChatBoundary(provider)])
    agent = Agent(
        client=client, middleware=installed,
        default_options={**(default_options or {}), "store": False}, **kwargs,
    )
    provider._claimed = True
    return agent
