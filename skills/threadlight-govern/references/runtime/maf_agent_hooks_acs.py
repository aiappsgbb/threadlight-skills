"""Pinned native MAF adapter; no synthetic hook dispatch or receipt write-back."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy, deepcopy
from datetime import datetime, timedelta, timezone
from functools import wraps
import inspect
import json
import time
import uuid

from agent_framework import AgentMiddleware, ChatMiddleware, FunctionMiddleware, MiddlewareBundle, ResponseStream

from agent_hooks import ALLOW, Decision, Transform, Verdict
from .evidence import ApprovalGrant, ApprovalIntent, digest
from .governance_provider import AUDIT, APPROVAL


_approvals = ContextVar("threadlight_approvals", default=None)
_effect_authorization = ContextVar("threadlight_effect_authorization", default=None)


@contextmanager
def approval_scope(state):
    token = _approvals.set(state)
    try:
        yield
    finally:
        _approvals.reset(token)


def authorized(provider, selected):
    provider._refresh()
    return all(b["ready"] for b in selected)


def hooks_bundle(interceptors, **kwargs):
    """One shared native bundle construction path (also exercised by the CTK)."""
    from agent_framework import create_agent_hooks_middleware
    return create_agent_hooks_middleware(interceptors, **kwargs)


def action_hash(provider, context, target=None):
    return digest({
        "policy_hash": provider.policy_digest(), "principal": provider.principal,
        "tenant": provider.tenant,
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
            if (not selected or not request.context_identity or not p._approval_ready()
                    or not authorized(p, selected)):
                raise ValueError("approval unavailable")
            expires_at = min(p._now() + timedelta(seconds=p.timeout), p._trust.expires_at)
            deadline = min(time.monotonic() + p.timeout, p._deadline)
            intent = ApprovalIntent(
                action_hash=action_hash(p, request.context), policy_hash=p.policy_digest(),
                principal=p.principal, agent_id=request.context["agent"]["id"],
                session_id=request.context["session"]["id"],
                context_identity=request.context_identity, nonce=uuid.uuid4().hex,
                expires_at=expires_at, tenant=p.tenant, allowed_roles=p.allowed_approval_roles,
                policy_expires_at=p._trust.expires_at,
            )
            grant = await asyncio.wait_for(p.approval_resolver.resolve(intent), p.timeout)
            def fresh():
                return (authorized(p, selected) and p._now() < intent.expires_at
                        and time.monotonic() < deadline)
            if (not isinstance(grant, ApprovalGrant) or type(grant.approved) is not bool
                    or grant.intent != intent or not fresh()
                    or not all(isinstance(v, str) and v.strip() for v in (
                        grant.approver, grant.approver_tenant, grant.approver_role, grant.provenance))
                    or grant.approver_tenant != intent.tenant
                    or grant.approver_role not in intent.allowed_roles):
                raise ValueError("approval binding invalid")
            verified = await asyncio.wait_for(
                p.approval_resolver.verify(grant, intent=intent),
                max(0, deadline - time.monotonic()),
            )
            if verified is not True or not fresh():
                raise ValueError("approval authentication unavailable")
            binding_success(selected)
            if grant.approved and receipt(p, selected, request.context, "allow"):
                if not fresh():
                    raise ValueError("approval expired before dispatch")
                if request.context["interception_point"] == "pre_tool_call":
                    state = _approvals.get()
                    if state is None:
                        raise ValueError("approval scope unavailable")
                    tool = request.context["tool_call"]
                    state[tool["id"]] = (
                        tool["name"], digest(tool["args"]), intent.expires_at, deadline,
                    )
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
            bindings = p._tool_bindings(context["tool_call"]["name"])
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
            if not authorized(p, selected):
                return Verdict.deny(reason="threadlight:policy_unavailable")
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
                if not authorized(p, selected):
                    return Verdict.deny(reason="threadlight:policy_unavailable")
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
            if not authorized(p, selected):
                return Verdict.deny(reason="threadlight:policy_unavailable")
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
        state = {}
        with approval_scope(state):
            await call_next()
        if isinstance(context.result, ResponseStream):
            context.result.with_pull_context_manager(lambda: approval_scope(state))
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
        context.options = {**_check_options(context.options or {}), "store": False}
        context.kwargs = _check_options(context.kwargs)
        tools = context.options.get("tools")
        if tools:
            # Keep the live list used by MAF's invocation loop, but never mutate
            # a caller-owned FunctionTool shared with another (unbound) agent.
            tools[:] = [_guard_tool(tool, self.provider) for tool in tools]
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
    if isinstance(tools, (FunctionTool, Mapping)) or callable(tools):
        tools = [tools]
    if any(not isinstance(tool, FunctionTool) and not callable(tool) for tool in tools):
        raise ValueError("provider-hosted tools are unsupported by local Agent Hooks")


def _check_options(options):
    if not isinstance(options, Mapping):
        return options
    options = dict(options)
    if options.get("tools") is not None:
        _check_tools(options["tools"])
    if options.get("web_search_options") is not None:
        raise ValueError("provider-hosted search is unsupported by local Agent Hooks")
    for key in ("extra_body", "options", "additional_chat_options", "client_kwargs"):
        if key in options:
            options[key] = _check_options(options[key])
    if "store" in options:
        options["store"] = False
    return options


class GovernedToolUnavailable(RuntimeError):
    def __init__(self, reason="threadlight:tool_unavailable"):
        super().__init__(reason)


def _check_effect(provider, selected, ticket):
    if provider.mode != "enforce":
        return
    if not authorized(provider, selected):
        raise GovernedToolUnavailable("threadlight:policy_unavailable")
    if ticket is not None and (
        provider._now() >= ticket[2] or time.monotonic() >= ticket[3]
    ):
        binding_failure(selected, "threadlight:approval_unavailable")
        raise GovernedToolUnavailable("threadlight:approval_unavailable")


class _FunctionBoundary(FunctionMiddleware):
    def __init__(self, provider):
        self.provider = provider

    async def process(self, context, call_next):
        from agent_framework import MiddlewareTermination
        from agent_framework.exceptions import UserInputRequiredException
        p = self.provider
        if not p._bindings:
            await call_next()
            return
        selected = p._tool_bindings(context.function.name)
        if not selected:
            await call_next()
            return
        state = _approvals.get()
        ticket = state.pop(context.metadata.get("call_id"), None) if state is not None else None
        if p.mode == "enforce" and (
            (any(APPROVAL.intersection(b["requires"]) for b in selected) and ticket is None)
            or (ticket is not None and ticket[:2] != (
                context.function.name, digest(dict(context.arguments)),
            ))
        ):
            binding_failure(selected, "threadlight:approval_unavailable")
            raise GovernedToolUnavailable("threadlight:approval_unavailable")
        _check_effect(p, selected, ticket)
        token = _effect_authorization.set(ticket)
        try:
            await call_next()
            return
        except (MiddlewareTermination, UserInputRequiredException, GovernedToolUnavailable):
            raise
        except Exception:
            pass
        finally:
            _effect_authorization.reset(token)
        raise GovernedToolUnavailable() from None


def _guard_tool(tool, provider):
    from agent_framework import FunctionTool
    from agent_framework.exceptions import UserInputRequiredException
    from agent_framework import MiddlewareTermination
    if not provider._bindings:
        return tool
    if not isinstance(tool, FunctionTool):
        tool = FunctionTool(func=tool)
    selected = provider._tool_bindings(tool.name)
    if not selected or tool.func is None or getattr(tool, "_threadlight_owner", None) is provider:
        return tool
    guarded = copy(tool)
    function = tool.func
    if isinstance(function, FunctionTool):
        function = function.func
    @wraps(function)
    async def invoke(*args, **kwargs):
        def call():
            _check_effect(provider, selected, _effect_authorization.get())
            return function(*args, **kwargs)
        _check_effect(provider, selected, _effect_authorization.get())
        try:
            if inspect.iscoroutinefunction(function) or getattr(tool, "_invoke_sync_on_event_loop", False):
                value = call()
            else:
                value = await asyncio.to_thread(call)
            if inspect.isawaitable(value):
                try:
                    _check_effect(provider, selected, _effect_authorization.get())
                except GovernedToolUnavailable:
                    if inspect.iscoroutine(value):
                        value.close()
                    raise
                return await value
            return value
        except (MiddlewareTermination, UserInputRequiredException, GovernedToolUnavailable):
            raise
        except Exception:
            pass
        # Raise outside the handler so the private exception is not retained
        # as __context__, and MAF still emits its error post-tool bracket.
        raise GovernedToolUnavailable() from None
    guarded.func = invoke
    guarded._threadlight_owner = provider
    return guarded


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
    installed.extend([_RunBoundary(provider), _ChatBoundary(provider), _FunctionBoundary(provider)])
    agent = Agent(
        client=client, middleware=installed,
        default_options={**(default_options or {}), "store": False}, **kwargs,
    )
    provider._claimed = True
    return agent
