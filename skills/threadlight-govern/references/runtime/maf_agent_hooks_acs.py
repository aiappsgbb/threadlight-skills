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
from threading import Event
import time
import uuid
from weakref import ref

from agent_framework import AgentMiddleware, ChatMiddleware, FunctionMiddleware, MiddlewareBundle, ResponseStream

from agent_hooks import ALLOW, Decision, Transform, Verdict
from .evidence import ApprovalGrant, ApprovalIntent, digest
from .governance_provider import AUDIT, APPROVAL


_approvals = ContextVar("threadlight_approvals", default=None)
_effect_authorization = ContextVar("threadlight_effect_authorization", default=None)
_execution = ContextVar("threadlight_execution", default=None)


def _emission(context):
    state = _execution.get()
    if state is None:
        return None
    return state["emissions"].get((context["session"]["id"], context["sequence"]))


class NativeRecordSink:
    """Consume typed native outcomes, never SDK serialization or exception text."""
    def __init__(self, provider):
        self.provider = provider

    def __call__(self, record):
        p = self.provider
        state = _execution.get()
        point = record.interception_point.value
        entry = state["emissions"].get((record.session_id, record.sequence)) if state else None
        selected = entry["selected"] if entry else [
            b for b in p._bindings.values() if b["point"] == point
        ]
        if not selected:
            return
        decision = record.verdict.decision.value
        reason = record.verdict.reason
        if (entry and entry.get("probe") is not None and point == "pre_tool_call"
                and decision in {"allow", "deny"} and entry["receipt_decision"] == decision
                and entry.get("receipt_id")):
            # Only the native emitter's typed, sequenced record authorizes a
            # probe observation. ACS evaluation alone is not interception proof.
            entry["probe_record"] = (decision, entry["receipt_id"])
            state["probe_pending"].set()
        if decision in {"allow", "transform"}:
            if entry and point == "pre_model_call" and state is not None and "model_scope" in state:
                state["model_scope"]["target"] = entry["target_hash"]
            if entry and point == "pre_tool_call" and state is not None:
                state["calls"][entry["call_id"]] = (
                    entry["tool"], entry["args_hash"], entry["original_args_hash"],
                )
            return
        known = {
            "threadlight:engine_failure", "threadlight:policy_unavailable",
            "threadlight:binding_unavailable", "threadlight:approval_unavailable",
            "threadlight:audit_unavailable", "threadlight:policy_deny",
            "threadlight:policy_escalate", "threadlight:transform_requires_approval",
            "threadlight:output_limit",
        }
        code = reason if reason in known else "threadlight:native_failure"
        if code not in {"threadlight:policy_deny", "threadlight:policy_escalate"}:
            binding_failure(selected, code)
        if entry and entry["receipt_decision"] in {"deny", "error"}:
            return
        if p.audit is None:
            return
        try:
            p.audit.append(
                correlation_id=digest(record.session_id), decision="deny",
                action_hash=entry["action_hash"] if entry else digest({
                    "input_identity": record.input_identity,
                    "enforced_identity": record.enforced_identity,
                    "sequence": record.sequence,
                }),
                policy_hash=p.policy_digest(), agent_version=p.agent_version,
                image_digest=p.image_digest, reason_code=code, interception_point=point,
                action_id=(entry["tool"] if entry else None) or point,
            )
        except Exception:
            binding_failure(selected, "threadlight:audit_unavailable")
            if state is not None:
                state["audit_failed"].update(id(b) for b in selected)


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
    tool = context.get("tool_call")
    if target is not None and context["interception_point"] == "pre_tool_call":
        tool = {**tool, "args": target}
    return digest({
        "policy_hash": provider.policy_digest(), "principal": provider.principal,
        "tenant": provider.tenant,
        "agent": context["agent"], "session": context["session"],
        "point": context["interception_point"], "tool": tool,
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
        details = {
            "reason_code": "threadlight:policy_" + decision,
            "interception_point": context["interception_point"],
        }
        if context.get("tool_result", {}).get("is_error"):
            decision = "error"
            details = {"reason_code": "threadlight:tool_unavailable",
                       "interception_point": context["interception_point"]}
        entry = _emission(context)
        probe = entry.get("probe") if entry else None
        receipt_id = provider.audit.append(
            correlation_id=digest(context["session"]), decision=decision,
            action_hash=action_hash(provider, context, target),
            policy_hash=provider.policy_digest(),
            agent_version=provider.agent_version, image_digest=provider.image_digest,
            action_id=(context.get("tool_call") or {}).get("name") or context["interception_point"],
            **({"probe": probe.model_dump(mode="json")} if probe is not None else {}),
            **details,
        )
        entry = _emission(context)
        if entry is not None:
            entry["receipt_decision"] = decision
            entry["receipt_id"] = receipt_id
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
                else:
                    state = _execution.get()
                    if state is None:
                        raise ValueError("approval scope unavailable")
                    state["lifecycle_tickets"][request.context["interception_point"]] = (
                        request.context["interception_point"], digest(request.context["target"]),
                        intent.expires_at, deadline,
                    )
                verdict = ALLOW
            elif not grant.approved:
                verdict = Verdict.deny(reason="threadlight:policy_deny")
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
        state = _execution.get()
        entry = None
        if state is not None:
            tool = context.get("tool_call", {})
            entry = {
                "selected": selected, "receipt_decision": None,
                "point": context["interception_point"], "session_hash": digest(context["session"]["id"]),
                "action_hash": action_hash(p, context),
                "call_id": tool.get("id"), "tool": tool.get("name"),
                "args_hash": digest(tool.get("args")),
                "original_args_hash": digest(tool.get("args")),
                "target_hash": digest(context["target"]),
            }
            state["emissions"][(context["session"]["id"], context["sequence"])] = entry
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
            telemetry = getattr(p, "probes", None)
            if telemetry is not None and context["interception_point"] == "pre_tool_call":
                await telemetry.begin(context, entry, selected)
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
            if entry and entry.get("probe") is not None and decision not in {"allow", "deny"}:
                raise ValueError("probe_policy_unsupported")
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
                if entry is not None and point == "pre_tool_call":
                    entry["args_hash"] = digest(value)
                    entry["action_hash"] = action_hash(p, context, value)
                if entry is not None and point == "pre_model_call":
                    from agent_framework._agent_hooks import _ModelRequestCodec
                    # Compare in the owning hook codec, including its equivalent
                    # string/single-text-content representations after writeback.
                    entry["target_hash"] = digest(_ModelRequestCodec.to_wire(
                        _ModelRequestCodec.write_back([], [], value) or [],
                    ))
                    entry["action_hash"] = action_hash(p, context, value)
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
        _check_lifecycle(self.provider, ("agent_startup", "input"))
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
        _check_model_config(self.provider, context.client, context.kwargs, context.options)
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
    if "middleware" in options:
        _check_extra_middleware(options["middleware"])
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


def _argument_hash(arguments):
    from agent_framework import MiddlewareTermination
    from agent_framework._agent_hooks import _ToolArgumentsCodec
    from agent_framework.exceptions import UserInputRequiredException
    try:
        return digest(_ToolArgumentsCodec.to_wire(arguments))
    except (MiddlewareTermination, UserInputRequiredException):
        raise
    except Exception:
        pass
    raise GovernedToolUnavailable("threadlight:invalid_arguments") from None


def _deny_boundary(provider, selected, reason):
    binding_failure(selected, reason)
    state = _execution.get()
    if state is not None:
        state["boundary_error"] = reason
        ids = {id(b) for b in selected}
        entry = next((e for e in reversed(list(state["emissions"].values()))
                      if any(id(b) in ids for b in e["selected"])), None)
        key = (id(entry), tuple(sorted(ids)), reason)
        if provider.audit is not None and entry and key not in state["boundary_receipts"]:
            state["boundary_receipts"].add(key)
            try:
                provider.audit.append(
                    correlation_id=entry["session_hash"], decision="deny",
                    action_hash=entry["action_hash"], policy_hash=provider.policy_digest(),
                    agent_version=provider.agent_version, image_digest=provider.image_digest,
                    reason_code=reason, interception_point=entry["point"],
                    action_id=entry["tool"] or entry["point"],
                )
            except Exception:
                binding_failure(selected, "threadlight:audit_unavailable")
                state["audit_failed"].update(ids)
    raise GovernedToolUnavailable(reason)


def _effect_lifecycle(provider):
    return [b for b in provider._bindings.values() if b["tool"] is None
            and b["point"] in {"agent_startup", "input", "post_model_call"}]


def _check_lifecycle(provider, points):
    for point in points:
        selected = [b for b in provider._bindings.values()
                    if b["tool"] is None and b["point"] == point]
        if not selected:
            continue
        state = _execution.get()
        ticket = state["lifecycle_tickets"].get(point) if state else None
        if (provider.mode == "enforce" and ticket is None
                and any(APPROVAL.intersection(b["requires"]) for b in selected)):
            _deny_boundary(provider, selected, "threadlight:approval_unavailable")
        _check_effect(provider, selected, ticket)


def _check_effect(provider, selected, ticket):
    if provider.mode != "enforce":
        return
    state = _execution.get()
    if state is not None and any(
        id(b) in state["audit_failed"] and AUDIT.intersection(b["requires"]) for b in selected
    ):
        _deny_boundary(provider, selected, "threadlight:audit_unavailable")
    if not authorized(provider, selected):
        _deny_boundary(provider, selected, "threadlight:policy_unavailable")
    if ticket is not None and (
        provider._now() >= ticket[2] or time.monotonic() >= ticket[3]
    ):
        _deny_boundary(provider, selected, "threadlight:approval_unavailable")


class _FunctionBoundary(FunctionMiddleware):
    def __init__(self, provider):
        self.provider = provider

    async def process(self, context, call_next):
        from agent_framework import MiddlewareTermination
        from agent_framework.exceptions import UserInputRequiredException
        p = self.provider
        telemetry = getattr(p, "probes", None)
        execution = _execution.get()
        if telemetry is not None and execution is not None:
            # Early drain only: queued synchronous work rechecks at its worker.
            await telemetry.flush(execution)
        if not p._bindings:
            await call_next()
            return
        selected = p._tool_bindings(context.function.name)
        if not selected and not _effect_lifecycle(p):
            await call_next()
            return
        _check_lifecycle(p, ("agent_startup", "input", "post_model_call"))
        state = _approvals.get()
        ticket = state.pop(context.metadata.get("call_id"), None) if state is not None else None
        actual = (context.function.name, _argument_hash(context.arguments))
        if p.mode == "enforce" and (
            (any(APPROVAL.intersection(b["requires"]) for b in selected) and ticket is None)
            or (ticket is not None and ticket[:2] != actual)
        ):
            _deny_boundary(p, selected, "threadlight:approval_unavailable")
        _check_effect(p, selected, ticket)
        execution = _execution.get()
        expected = execution["calls"].pop(context.metadata.get("call_id"), None) if execution else None
        if (p.mode == "enforce" and any(b["point"] == "pre_tool_call" for b in selected)
                and (expected is None or expected[:2] != actual)):
            _deny_boundary(p, selected, "threadlight:arguments_changed")
        token = _effect_authorization.set((p, actual, ticket, {
            "dispatched": False, "called": False,
            "transformed": bool(expected and expected[1] != expected[2]),
            "probe_entry": next((e for e in execution["emissions"].values()
                                 if e["call_id"] == context.metadata.get("call_id")
                                 and e.get("probe") is not None), None)
            if execution and getattr(p, "probes", None) is not None else None,
        }))
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


def _private_validation(call, *args, **kwargs):
    from agent_framework import MiddlewareTermination
    from agent_framework.exceptions import UserInputRequiredException
    try:
        return call(*args, **kwargs)
    except (MiddlewareTermination, UserInputRequiredException):
        raise
    except Exception:
        pass
    # Native auto-invocation catches TypeError before any hooks execute. Do not
    # return a replacement model or retain Pydantic input/context in the error.
    raise TypeError("threadlight:invalid_arguments") from None


def _guard_input_model(model, tool):
    from agent_framework._tools import _validate_arguments_against_schema
    from pydantic import RootModel
    base = model if model is not None else RootModel[dict]
    def checked_dump(dump, *args, **kwargs):
        value = _validate_arguments_against_schema(
            arguments=dump(*args, **kwargs), schema=tool.parameters(), tool_name=tool.name,
        )
        # Check the native hook projection inside the private pre-hook boundary.
        _argument_hash(value)
        return value
    class ValidatedInput:
        def __init__(self, value):
            self.value = value

        def model_dump(self, *args, **kwargs):
            # A root/wrap validator may return a different model (or object).
            # Keep lookup, serialization and the downstream native schema check
            # inside one private boundary, regardless of that returned type.
            return _private_validation(
                lambda: checked_dump(self.value.model_dump, *args, **kwargs),
            )

    class GuardedInput(base):
        @classmethod
        def model_validate(cls, *args, **kwargs):
            return ValidatedInput(_private_validation(base.model_validate, *args, **kwargs))

        @classmethod
        def model_validate_json(cls, *args, **kwargs):
            return ValidatedInput(_private_validation(base.model_validate_json, *args, **kwargs))

        @classmethod
        def model_validate_strings(cls, *args, **kwargs):
            return ValidatedInput(_private_validation(base.model_validate_strings, *args, **kwargs))

        def model_dump(self, *args, **kwargs):
            return _private_validation(checked_dump, super().model_dump, *args, **kwargs)

        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            return deepcopy(model.model_json_schema(*args, **kwargs) if model else tool.parameters())
    return GuardedInput


def _guard_probe_tool(tool, provider):
    """Opt-in persistence boundary only; keep unbound native validation/accounting."""
    from agent_framework import FunctionTool
    cache = provider.__dict__.setdefault("_probe_guarded_tools", {})
    key = id(tool)
    cached = cache.get(key)
    if cached is not None and cached[0]() is tool:
        return cached[1]
    guarded = copy(tool)
    function = tool.func.func if isinstance(tool.func, FunctionTool) else tool.func
    on_loop = inspect.iscoroutinefunction(function) or getattr(tool, "_invoke_sync_on_event_loop", False)
    telemetry = provider.probes
    async def invoke(call_kwargs):
        state = _execution.get()
        def call():
            telemetry.flush_sync(state)
            return guarded(**call_kwargs)
        if on_loop:
            await telemetry.flush(state)
            value = guarded(**call_kwargs)
        else:
            value = await asyncio.to_thread(call)
        if inspect.isawaitable(value):
            try:
                await telemetry.flush(state)
            except BaseException:
                if inspect.iscoroutine(value):
                    value.close()
                raise
            return await value
        return value
    guarded._invoke_function = invoke
    guarded._threadlight_owner = provider
    def discard(reference):
        if cache.get(key, (None,))[0] is reference:
            cache.pop(key)
    cache[key] = (ref(tool, discard), guarded)
    return guarded


def _guard_tool(tool, provider):
    from agent_framework import FunctionTool
    from agent_framework.exceptions import UserInputRequiredException
    from agent_framework import MiddlewareTermination
    if not provider._bindings:
        return tool
    if not isinstance(tool, FunctionTool):
        tool = FunctionTool(func=tool)
    selected = provider._tool_bindings(tool.name)
    if tool.func is None or getattr(tool, "_threadlight_owner", None) is provider:
        return tool
    if not selected and not _effect_lifecycle(provider):
        return _guard_probe_tool(tool, provider) if getattr(provider, "probes", None) is not None else tool
    cache = provider.__dict__.setdefault("_guarded_tools", {})
    key = id(tool)
    cached = cache.get(key)
    if cached is not None and cached[0]() is tool:
        return cached[1]
    guarded = copy(tool)
    # Native provider projection mutates its schema. It must not reach the shared
    # caller-owned tool, including nested property schemas.
    guarded._input_schema_cached = deepcopy(guarded.parameters())
    guarded._cached_parameters = guarded._input_schema_cached
    original_model = tool.input_model
    guarded.input_model = _guard_input_model(original_model, guarded)
    # Route schema-only tools through the same private pre-hook check as models.
    guarded._schema_supplied = False
    function = tool.func
    if isinstance(function, FunctionTool):
        function = function.func
    tool_name = tool.name
    bound_self = getattr(tool, "_instance", None)
    invoke_sync_on_event_loop = getattr(tool, "_invoke_sync_on_event_loop", False)
    telemetry = getattr(provider, "probes", None)
    @wraps(function)
    async def invoke(*args, **kwargs):
        def check():
            authorization = _effect_authorization.get()
            if provider.mode == "enforce":
                if authorization is None or authorization[0] is not provider:
                    raise GovernedToolUnavailable("threadlight:tool_unavailable")
                values = dict(kwargs)
                for name in inspect.signature(function).parameters:
                    # MAF injects the native invocation context, not a tool argument.
                    if name in values and isinstance(values[name], FunctionInvocationContext):
                        values.pop(name)
                native_self = len(args) == 1 and bound_self is not None and args[0] is bound_self
                if (args and not native_self) or authorization[1] != (tool_name, _argument_hash(values)):
                    _deny_boundary(provider, selected, "threadlight:arguments_changed")
            _check_lifecycle(provider, ("agent_startup", "input", "post_model_call"))
            _check_effect(provider, selected, authorization[2] if authorization else None)
        def call():
            if telemetry is not None and not on_loop:
                telemetry.flush_sync(_execution.get())
            check()
            authorization = _effect_authorization.get()
            if provider.mode == "enforce" and authorization is not None:
                if authorization[3]["called"]:
                    _deny_boundary(provider, selected, "threadlight:authorization_reused")
                authorization[3]["called"] = True
            # Native budget checks and accounting must share the actual call's
            # worker turn, not run before queued work reaches this boundary.
            return execution_tool(*args, **kwargs)
        from agent_framework import FunctionInvocationContext
        check()
        try:
            on_loop = inspect.iscoroutinefunction(function) or invoke_sync_on_event_loop
            if on_loop:
                if telemetry is not None:
                    await telemetry.flush(_execution.get())
                value = call()
            else:
                value = await asyncio.to_thread(call)
            if inspect.isawaitable(value):
                try:
                    if telemetry is not None:
                        await telemetry.flush(_execution.get())
                    check()
                except BaseException:
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
    # The pinned auto-invocation pipeline already validated before pre_tool_call.
    # Retain that model/schema on the exposed tool, but use the public schema-only
    # invocation path on a separate execution copy. No model_construct/model_dump
    # round-trip for unchanged canonical arguments. Changed ACS targets must pass
    # the original model without normalization changing the authorized hash.
    execution_tool = copy(guarded)
    execution_tool.input_model = None
    execution_tool.func = function
    async def invoke_function(call_kwargs):
        return await invoke(**call_kwargs)
    # Keep native validation/context/parsing, but authorize before __call__ so
    # denied or unscheduled work never consumes an application invocation.
    execution_tool._invoke_function = invoke_function
    async def invoke_validated(*, arguments=None, context=None, **kwargs):
        authorization = _effect_authorization.get()
        if provider.mode == "enforce" and (authorization is None or authorization[0] is not provider):
            raise GovernedToolUnavailable("threadlight:tool_unavailable")
        if provider.mode == "enforce" and authorization[1] != (tool_name, _argument_hash(arguments)):
            _deny_boundary(provider, selected, "threadlight:arguments_changed")
        if (provider.mode == "enforce" and authorization[3]["transformed"]
                and original_model is not None):
            valid = False
            try:
                normalized = original_model.model_validate(deepcopy(arguments)).model_dump(exclude_unset=True)
                valid = _argument_hash(normalized) == authorization[1][1]
            except (MiddlewareTermination, UserInputRequiredException):
                raise
            except Exception:
                pass
            if not valid:
                _deny_boundary(provider, selected, "threadlight:invalid_transform")
            arguments = normalized
        _check_lifecycle(provider, ("agent_startup", "input", "post_model_call"))
        _check_effect(provider, selected, authorization[2] if authorization else None)
        if provider.mode == "enforce":
            if authorization[3]["dispatched"]:
                _deny_boundary(provider, selected, "threadlight:authorization_reused")
            authorization[3]["dispatched"] = True
        return await execution_tool.invoke(arguments=arguments, context=context, **kwargs)
    guarded.invoke = invoke_validated
    guarded._threadlight_owner = provider
    # Keep native counters on the same execution copy for the original's lifetime,
    # without retaining discarded per-call tools or reusing an unrelated object's id.
    def discard(reference):
        if cache.get(key, (None,))[0] is reference:
            cache.pop(key)
    cache[key] = (ref(tool, discard), guarded)
    return guarded


def _check_client_middleware(client):
    if any(getattr(client, name, None) for name in (
        "agent_middleware", "chat_middleware", "function_middleware", "middleware",
    )):
        raise ValueError("client middleware must be supplied inside the governed Agent Hooks bundle")


def _model_bindings(provider):
    return [b for b in provider._bindings.values()
            if b["tool"] is None and b["point"] == "pre_model_call"]


def _check_model_config(provider, client, *values):
    selected = _model_bindings(provider)
    if not selected or provider.mode != "enforce":
        return
    def check(value):
        if not isinstance(value, Mapping):
            return
        for key, item in value.items():
            reason = None
            if key == "compaction_strategy" and item is not None:
                reason = "threadlight:unsupported_model_compaction"
            elif key in {"input", "messages"} and item is not None:
                reason = "threadlight:unsupported_model_override"
            if reason:
                if _execution.get() is not None:
                    _deny_boundary(provider, selected, reason)
                binding_failure(selected, reason)
                raise ValueError(reason)
            check(item)
    check({"compaction_strategy": getattr(client, "compaction_strategy", None)})
    for value in values:
        check(value)
    if getattr(client, "message_preparer", None) is not None:
        reason = "threadlight:unsupported_model_preparer"
        if _execution.get() is not None:
            _deny_boundary(provider, selected, reason)
        binding_failure(selected, reason)
        raise ValueError(reason)


def _model_configuration(options, kwargs):
    from agent_framework import FunctionTool
    from agent_framework._serialization import make_json_safe
    def project(value):
        if isinstance(value, FunctionTool):
            return {"name": value.name, "description": value.description,
                    "parameters": deepcopy(value.parameters())}
        if isinstance(value, Mapping):
            return {k: project(v) for k, v in value.items() if k not in {"store", "tokenizer"}}
        if isinstance(value, (list, tuple)):
            return [project(v) for v in value]
        return make_json_safe(value)
    return digest([project(options or {}), project(kwargs)])


class _ModelScope(ChatMiddleware):
    """Hold the invariant configuration beside the native message authorization."""
    def __init__(self, provider):
        self.provider = provider

    async def process(self, context, call_next):
        if _model_bindings(self.provider) and self.provider.mode == "enforce":
            _check_model_config(self.provider, context.client, context.kwargs, context.options)
            _execution.get()["model_scope"] = {
                "configuration": _model_configuration(context.options, context.kwargs),
                "target": None, "wire": None,
            }
        await call_next()


def _check_model_target(provider, messages, options, kwargs):
    selected = _model_bindings(provider)
    if not selected or provider.mode != "enforce":
        return
    from agent_framework._agent_hooks import _ModelRequestCodec
    state = _execution.get()
    scope = state.get("model_scope") if state else None
    if (scope is None or scope["target"] != digest(_ModelRequestCodec.to_wire(messages))
            or scope["configuration"] != _model_configuration(options, kwargs)):
        _deny_boundary(provider, selected, "threadlight:model_target_changed")


def _middleware_entries(value):
    return list(value) if isinstance(value, (list, tuple)) else [] if value is None else [value]


def _check_extra_middleware(value):
    for item in _middleware_entries(value):
        if isinstance(item, MiddlewareBundle) or type(item).__module__ == "agent_framework._agent_hooks":
            raise ValueError("extra Agent Hooks middleware must not share the governance boundary")


def _check_run_options(value):
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "middleware":
                _check_extra_middleware(item)
            elif isinstance(item, Mapping):
                _check_run_options(item)


class _ExecutionScope(AgentMiddleware):
    """Host guard outside the one native bundle; applications remain inside it."""
    def __init__(self, provider, client):
        self.provider, self.client = provider, client

    async def process(self, context, call_next):
        _check_client_middleware(self.client)
        state = {"emissions": {}, "calls": {}, "audit_failed": set(), "boundary_error": None,
                 "lifecycle_tickets": {}, "boundary_receipts": set()}
        if getattr(self.provider, "probes", None) is not None:
            state.update(probe_provider=self.provider, probe_loop=asyncio.get_running_loop(),
                         probe_pending=Event(), probe_bridge_failed=Event())
        @contextmanager
        def scope():
            token = _execution.set(state)
            try:
                yield
            finally:
                _execution.reset(token)
        with scope():
            try:
                await call_next()
            finally:
                telemetry = getattr(self.provider, "probes", None)
                if telemetry is not None:
                    await telemetry.flush(state)
            if isinstance(context.result, ResponseStream):
                inner = context.result
                # Native buffered streams run registered transform hooks BEFORE
                # their output gate. An outer stream checks AFTER that gate instead.
                async def updates():
                    try:
                        async for update in inner:
                            with scope():
                                if telemetry is not None:
                                    await telemetry.flush(state)
                                self._release(update)
                            yield update
                    finally:
                        if telemetry is not None:
                            await telemetry.flush(state)
                async def finalizer(updates):
                    with scope():
                        return self._release(await inner.get_final_response())
                context.result = ResponseStream(updates(), finalizer=finalizer)
                context.result.with_pull_context_manager(scope)
            elif context.result is not None:
                self._release(context.result)

    def _release(self, value):
        _check_lifecycle(self.provider, ("output",))
        return value


def _guard_client(client, provider):
    _check_client_middleware(client)
    guarded = copy(client)
    _guard_http_client(guarded, provider)
    prepare = guarded._prepare_messages_for_model_call
    async def prepare_messages(messages, **kwargs):
        _check_model_config(provider, guarded, kwargs)
        return await prepare(messages, **kwargs)
    guarded._prepare_messages_for_model_call = prepare_messages
    dispatch = guarded._inner_get_response
    def transport(*, messages, stream, options, **kwargs):
        # BaseChatClient calls this extension point AFTER awaited compaction.
        _check_lifecycle(provider, ("agent_startup", "input", "pre_model_call"))
        _check_model_config(provider, guarded, options, kwargs)
        options = {**_check_options(options or {}), "store": False}
        kwargs = _check_options(kwargs)
        _check_model_target(provider, messages, options, kwargs)
        result = dispatch(messages=messages, stream=stream, options=options, **kwargs)
        if not inspect.isawaitable(result) or isinstance(result, ResponseStream):
            return result
        async def finish():
            try:
                return await result
            except Exception:
                state = _execution.get()
                if not state or not state["boundary_error"]:
                    raise
                reason = state["boundary_error"]
            raise GovernedToolUnavailable(reason) from None
        return finish()
    guarded._inner_get_response = transport
    return guarded


def _check_model_transport(client, provider):
    selected = [b for b in provider._bindings.values() if b["tool"] is None
                and b["point"] in {"agent_startup", "input", "pre_model_call"}]
    if not selected:
        return
    from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient
    from agent_framework.foundry import FoundryChatClient
    from openai import AsyncOpenAI
    import httpx
    sdk = getattr(client, "client", None)
    if (type(client) not in {OpenAIChatClient, OpenAIChatCompletionClient, FoundryChatClient}
            or not isinstance(sdk, AsyncOpenAI)
            or not isinstance(getattr(sdk, "_client", None), httpx.AsyncClient)):
        binding_failure(selected, "threadlight:unsupported_model_transport")
        raise ValueError("a supported native model HTTP transport is required for selected lifecycle bindings")


def _guard_http_client(client, provider):
    """Bind native JSON before SDK awaits; check it at final HTTP dispatch.

    request, with_options and AsyncBaseTransport are public extension surfaces. The pinned
    SDK has no public getter for its HTTP client or mounted transports; read those
    references only, and replace them on host-owned shallow copies, never originals.
    """
    from openai import AsyncOpenAI
    from openai._base_client import _merge_mappings
    from openai._utils._json import openapi_dumps
    import httpx
    _check_model_transport(client, provider)
    _check_model_config(provider, client)
    sdk = getattr(client, "client", None)
    if not isinstance(sdk, AsyncOpenAI):
        return
    http = copy(sdk._client)
    selected = _model_bindings(provider)
    def request_identity(body):
        try:
            return digest(json.loads(body))
        except Exception:
            pass
        _deny_boundary(provider, selected, "threadlight:model_target_changed")
    def options_identity(options):
        try:
            if options.content is not None or options.files:
                raise ValueError("unsupported request body")
            body = options.json_data
            if options.extra_json is not None:
                body = options.extra_json if body is None else _merge_mappings(body, options.extra_json)
            # Match the pinned SDK's body merge and serializer, not the different
            # MAF hook projection. JSON byte formatting is not authorization.
            return request_identity(body if isinstance(body, bytes) else openapi_dumps(body))
        except Exception:
            pass
        _deny_boundary(provider, selected, "threadlight:model_target_changed")
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, inner):
            self.inner = inner

        async def handle_async_request(self, request):
            _check_lifecycle(provider, ("agent_startup", "input", "pre_model_call"))
            if selected and provider.mode == "enforce":
                scope = _execution.get()["model_scope"]
                # httpx.content is a cache; transports send stream instead.
                # Pinned native JSON requests use a replayable ByteStream, not
                # arbitrary body producers with effects during iteration.
                if type(request.stream) is not httpx.ByteStream:
                    _deny_boundary(provider, selected, "threadlight:model_target_changed")
                body = b"".join(request.stream)
                try:
                    content = request.content
                except httpx.RequestNotRead:
                    # HTTPX redirects retain the replayable stream without a
                    # content cache. There is no second cached value to verify.
                    content = body
                if (scope["wire"] != request_identity(content)
                        or scope["wire"] != request_identity(body)):
                    _deny_boundary(provider, selected, "threadlight:model_target_changed")
            return await self.inner.handle_async_request(request)

        async def aclose(self):
            # The caller owns the shared connection pools and their lifetime.
            pass
    http._transport = Transport(http._transport)
    http._mounts = {pattern: Transport(transport) if transport is not None else None
                    for pattern, transport in http._mounts.items()}
    client.client = sdk.with_options(http_client=http)
    request = client.client.request
    @wraps(request)
    def authorized_request(cast_to, options, **kwargs):
        if selected and provider.mode == "enforce":
            state = _execution.get()
            scope = state.get("model_scope") if state else None
            if scope is None or scope["target"] is None:
                _deny_boundary(provider, selected, "threadlight:model_target_changed")
            identity = options_identity(options)
            if scope["wire"] is None:
                scope["wire"] = identity
            elif scope["wire"] != identity:
                _deny_boundary(provider, selected, "threadlight:model_target_changed")
        # Capture before even entering the SDK coroutine: its options/request
        # preparation, credential refresh and retries can all await. HTTPX
        # request hooks are too late because authentication runs before them.
        return request(cast_to, options, **kwargs)
    client.client.request = authorized_request


def create_governed_agent(provider, *, client, middleware=(), default_options=None, **kwargs):
    from agent_framework import Agent
    if provider._claimed:
        raise ValueError("each agent requires its own governance provider and bundle")
    hooks = provider.middleware()
    _check_client_middleware(client)
    _check_model_config(provider, client, kwargs, default_options or {})
    _check_tools(kwargs.get("tools") or [])
    installed = _middleware_entries(middleware)
    if any(isinstance(m, MiddlewareBundle) and m is not hooks for m in installed):
        raise ValueError("foreign bundles must not share the governance boundary")
    if hooks in installed:
        if installed[0] is not hooks or installed.count(hooks) != 1:
            raise ValueError("exactly one Agent Hooks bundle must be first")
    else:
        installed.insert(0, hooks)
    _check_extra_middleware([m for m in installed if m is not hooks])
    _check_run_options(kwargs)
    _check_run_options(default_options or {})
    installed.insert(0, _ModelScope(provider))
    installed.insert(0, _ExecutionScope(provider, client))
    installed.extend([_RunBoundary(provider), _ChatBoundary(provider), _FunctionBoundary(provider)])
    agent = Agent(
        client=_guard_client(client, provider), middleware=installed,
        default_options={**(default_options or {}), "store": False}, **kwargs,
    )
    run = agent.run
    @wraps(run)
    def checked_run(*args, **run_kwargs):
        _check_client_middleware(client)
        _check_client_middleware(agent.client)
        _check_model_transport(agent.client, provider)
        _check_model_config(provider, agent.client, run_kwargs, {
            "compaction_strategy": agent.compaction_strategy,
        })
        _check_run_options(run_kwargs)
        return run(*args, **run_kwargs)
    agent.run = checked_run
    provider._claimed = True
    return agent
