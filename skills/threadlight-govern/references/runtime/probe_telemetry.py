"""Opt-in native hook producer and its fixed, awaited noop tool implementation."""
from __future__ import annotations

import asyncio


class NativeProbeTelemetry:
    def __init__(self, *, provider, service, downstream, client_id):
        if service.producer != "native" or provider.audit is None or provider.mode != "enforce":
            raise ValueError("native_probe_requires_enforced_durable_audit")
        registry = service.registry
        if (provider.principal not in registry.actions[0].workloads
                or provider.tenant != registry.tenant_id
                or provider.agent_version != registry.deployment.agent_version
                or provider.image_digest != registry.deployment.image_digest
                or provider.environment != registry.deployment.environment
                or provider.policy_digest() != service.policy_digest):
            raise ValueError("native_probe_deployment_mismatch")
        self.provider, self.service, self.downstream = provider, service, downstream
        self.client_id = client_id
        self.action = service.action("governance_probe_noop", provider.principal)
        bindings = provider._tool_bindings(self.action.name)
        if not bindings or any(b["point"] != "pre_tool_call" or b["policy"] != self.action.policy_binding
                               for b in bindings):
            raise ValueError("native_probe_pre_tool_binding_required")
        self.provider.probes = self

    async def begin(self, context, entry, selected):
        if context["tool_call"]["name"] != self.action.name:
            return
        from govern_control_plane.probes import PROBE_INPUT, hashed
        from govern_gateway.dispatcher import validated
        if entry is None or not selected or context["agent"]["id"] != self.service.registry.deployment.agent_id:
            raise ValueError("native_probe_scope")
        args = validated(context["tool_call"]["args"], PROBE_INPUT)
        entry["probe"] = await self.service.begin(
            self.provider.principal, args["probe_run_id"], self.action.name, args["variant"],
            session_id=hashed(context["session"]["id"]), call_id=hashed(context["tool_call"]["id"]))

    async def flush_entry(self, entry):
        if not entry or "probe_record" not in entry or entry.get("probe_flushed"):
            return
        from .maf_agent_hooks_acs import GovernedToolUnavailable, binding_failure
        if not entry.get("probe_flush_failed"):
            try:
                async with asyncio.timeout(min(10, self.provider.timeout)):
                    async with entry.setdefault("probe_flush_lock", asyncio.Lock()):
                        if entry.get("probe_flush_failed"):
                            raise ValueError("probe_flush_failed")
                        if entry.get("probe_flushed"):
                            return
                        decision, receipt_id = entry["probe_record"]
                        await self.service.intercept(entry["probe"], decision=decision, receipt_id=receipt_id,
                                                     action_hash=entry["action_hash"])
                        if decision == "deny":
                            await self.service.complete(entry["probe"], terminal="denied")
                        entry["probe_flushed"] = True
                        return
            except asyncio.CancelledError:
                entry["probe_flush_failed"] = True
                raise
            except Exception:
                entry["probe_flush_failed"] = True
        binding_failure(entry["selected"], "threadlight:probe_unavailable")
        # Do not retain a private storage exception, including as __context__.
        raise GovernedToolUnavailable("threadlight:probe_unavailable") from None

    async def flush(self, state):
        if state is None or state.get("probe_provider") is not self.provider:
            return
        def check_bridge():
            if state["probe_bridge_failed"].is_set():
                from .maf_agent_hooks_acs import GovernedToolUnavailable, binding_failure
                for entry in state["emissions"].values():
                    if "probe_record" in entry:
                        binding_failure(entry["selected"], "threadlight:probe_unavailable")
                raise GovernedToolUnavailable("threadlight:probe_unavailable")
        check_bridge()
        # No transcript inspection and no inferred zero. A missing native record
        # leaves registration/received nonterminal, including fake/unreached tools.
        while pending := [entry for entry in tuple(state["emissions"].values())
                          if "probe_record" in entry and not entry.get("probe_flushed")]:
            for entry in pending:
                await self.flush_entry(entry)
        check_bridge()
        state["probe_pending"].clear()

    def flush_sync(self, state):
        if state is None or state.get("probe_provider") is not self.provider:
            return
        from .maf_agent_hooks_acs import GovernedToolUnavailable
        if state["probe_bridge_failed"].is_set():
            raise GovernedToolUnavailable("threadlight:probe_unavailable")
        if not state["probe_pending"].is_set():
            return
        # Only workers block here. Locks, storage and ContextVars stay on the
        # owning run's loop; never run a second loop around its async store.
        coroutine = self.flush(state)
        future = None
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, state["probe_loop"])
            future.result(timeout=min(10, self.provider.timeout) + 0.1)
            return
        except Exception:
            state["probe_bridge_failed"].set()
            if future is not None:
                future.cancel()
            else:
                coroutine.close()
        raise GovernedToolUnavailable("threadlight:probe_unavailable") from None

    def tool(self):
        from agent_framework import FunctionTool
        from govern_control_plane.probes import PROBE_INPUT, PROBE_OUTPUT, hashed
        from govern_gateway.dispatcher import digest, validated
        from .maf_agent_hooks_acs import _effect_authorization, require_effect_authorization_async

        async def noop(probe_run_id: str, variant: str):
            authorization = _effect_authorization.get()
            if authorization is None or authorization[0] is not self.provider:
                raise ValueError("native_probe_authority")
            entry = authorization[3]["probe_entry"]
            if (entry is None or entry.get("probe_record", (None,))[0] != "allow"
                    or entry["probe"].probe_run_id != probe_run_id):
                raise ValueError("native_probe_interception_required")
            await self.flush_entry(entry)
            probe = entry["probe"]
            args = validated({"probe_run_id": probe_run_id, "variant": variant}, PROBE_INPUT)
            authorize = await require_effect_authorization_async(self.action.name, args)
            facts = {"tenant": probe.tenant, "subject": probe.subject, "client": self.client_id,
                     "action": self.action.name, "scope": self.action.scope,
                     "policy": probe.policy_digest, "deployment": probe.deployment.model_dump(mode="json")}
            async def guard():
                await authorize()
                self.provider._refresh()
                self.service.fresh()
                if not all(b["ready"] for b in self.provider._tool_bindings(self.action.name)):
                    raise ValueError("native_probe_unavailable")
            async with asyncio.timeout(15):
                result = await self.downstream.request(
                    action=self.action, arguments=args, key=hashed(probe_run_id),
                    action_hash=digest({"facts": facts, "arguments": args}),
                    provenance=entry["receipt_id"], facts=facts, guard=guard,
                    on_dispatch=lambda: self.service.dispatch(probe))
                await guard()
                output = validated(result["result"], PROBE_OUTPUT)
                await self.service.complete(probe, terminal="completed")
                return output

        return FunctionTool(name=self.action.name, description="Explicit staging noop probe",
                            func=noop, input_model=PROBE_INPUT)
