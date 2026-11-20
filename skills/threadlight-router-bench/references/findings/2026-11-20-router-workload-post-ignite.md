# Finding — model router after Ignite 2026: workload comparison (2026-11-20)

> **Supersedes** [the 2026-06-30 finding](2026-06-30-router-validation-5.4-vs-mini.md)
> (router subset `{gpt-5.4, gpt-5.4-mini}`). That subset and its prices are
> stale; keep the old log only as history.

**Question:** after the Ignite 2026 model router changes (Responses API, per-request
routing controls, own deployments serving routed requests first, a 45-model pool),
what does the router cost on a mixed workload against the cheapest adequate fixed
model, and what should a new pilot use by default?

## Test conditions

- **Setup:** an internal test subscription, Sweden Central, model router
  `2025-11-18`, Responses API, Entra ID auth. Claude and Fireworks models were not
  deployable in that subscription, so the full pool had 31 of 45 models.
- **Workload:** the deterministic task mix in
  [`scripts/workload_tasks.py`](../../scripts/workload_tasks.py): 40 easy tasks
  (8 each of simple Q&A, RAG, function tools, numeric reasoning, Python code run
  against tests) and 20 hard tasks. Deterministic graders, no LLM judge.
  2 repetitions per arm. Router arms used `routing_config.session_affinity.strategy = none`.
- **Cost:** an **estimate**: tokens × Azure Retail Prices API list price (Global,
  pulled 2026-11-20) plus the routing fee ($0.14 per 1M input tokens Global,
  $0.154 Data Zone). Not billed spend. `gpt-6.2-astra` had no retail meter on that
  date; its price is unverified.
- **Reproduce offline:** the recorded easy-tier usage is in
  [`fixtures/workload-recorded-easy.jsonl`](../fixtures/workload-recorded-easy.jsonl):

  ```bash
  python skills/threadlight-router-bench/scripts/router_bench.py workload \
    --ingest skills/threadlight-router-bench/references/fixtures/workload-recorded-easy.jsonl \
    --prices skills/threadlight-router-bench/references/fixtures/retail-prices-swedencentral.json
  ```

## Results — easy tier (80 calls per arm, all at 100 % quality)

| Arm | $ per 1k tasks | Served models |
|---|---|---|
| fixed `gpt-6-luna` | **0.054** | gpt-6-luna |
| router, subset {gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol} + own deployments, Balanced | **0.082** | gpt-6-luna 100 % |
| router, full pool, Balanced (Auto identical) | **0.147** | gpt-5.6-luna 52 %, DeepSeek-V4.1-Flash 20 %, grok-4-1-fast-reasoning 15 %, … |
| fixed `gpt-5.4-mini` | 0.385 | gpt-5.4-mini |
| fixed `gpt-6.1-terra` | 0.482 | gpt-6.1-terra |
| fixed `gpt-6.1-sol` | 0.916 | gpt-6.1-sol |
| router, full pool, Quality | **1.832** | gpt-5.6-sol 35 %, gpt-5.4 15 %, … |
| fixed `gpt-6.2-astra` (unverified price) | 3.160 | gpt-6.2-astra |

The `workload` mode reproduces these four headline figures from the recording
exactly (the offline test allows ±10 %).

## Results — hard tier (40 calls per arm)

| Arm | Correct | $ per 1k tasks |
|---|---|---|
| fixed gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol, gpt-6-astra, gpt-6.2-astra | 40/40 | 0.14 – 10.9 |
| router subset, Balanced (all to gpt-6-luna) | 40/40 | 0.168 |
| router full pool (Cost / Balanced / Quality) | 38–39/40 | 0.26 / 0.33 / 3.75 |
| fixed gpt-5.4-mini | 37/40 | 0.88 |

## Other observations

- **[Tested] Responses API works on model router**: HTTP 200 on a fresh resource;
  tools, JSON schema output, reasoning effort and streaming all worked. The old
  "Responses returns 400 operation unsupported" guardrail is withdrawn.
  [Learn: model router APIs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router-apis).
- **[Documented] GitHub Copilot BYOK uses Chat Completions** with model router, so
  the GHCP harness keeps the `completions` wire.
  [Learn: model router with agents and coding tools](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router-agents).
- **[Tested] Router-managed capacity throttled** at about 12–15 RPM / 12–15k TPM
  (`x-ratelimit-key: model-router-2025-11-18`) although the deployment had 250
  units; about 25 % of full-pool calls at ~12 concurrent requests got HTTP 429
  with no failover. Deploying the subset models in the same resource removed the
  throttle: `x-ratelimit-key` then named our own deployment.
  Learn documents that own deployments serve routed requests first and that the
  deployment's TPM/RPM apply:
  [Learn: model router quotas](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router-quotas).
- **[Tested] `model` is not always the served model**: 2 of 360 calls returned
  `model-router-2025-11-18`; the served model was only in
  `model_selection_details.model_router_details.routing_trace`. Responses returns
  unversioned names, Chat Completions versioned ones. `workload` falls back to the
  last `routing_trace` attempt and normalizes versions.
- **[Tested] Routing is deterministic and cheap in latency**: 0 of 40 tasks changed
  model across repetitions; `routing_trace` latency p50 about 25 ms.
- **[Tested] Metrics need every deployment**: routed tokens appear under the router
  deployment and under the own deployments that served them, so `bench` now
  queries `ModelDeploymentName eq '*'` and attributes by served `ModelName`.
- **[Documented] Per-request controls** need the `Foundry-Features:
  ModelRouterControls=V1Preview` header plus `routing_config`; the header also turns
  on `adaptive` session affinity unless `strategy` is `none`.
  [Learn: routing controls](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router-routing-controls).
- **[Documented] The pool lists 45 models** for router `2025-11-18`; `pool` reads
  it from the ARM models API instead of hard-coding it.
  [Learn: model router models](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router-models).

## Decision rule (advice)

- Start from the cheapest fixed model that passes the workload's graders; here that
  is `gpt-6-luna`.
- A curated subset with each member deployed in the same resource is the
  router-ready option; the full pool in Balanced costs about 2.7× fixed
  `gpt-6-luna` on this workload and Quality about 34×.
- When one model serves **90 % or more** of a router arm's calls (the subset in
  Balanced sent 100 % to `gpt-6-luna`), call that model directly and drop the
  routing fee: on small models the fee ($0.14 per 1M input) exceeds the input price.
- Re-run `workload` on the pilot's own task mix before go-live; this synthetic mix
  is evidence for defaults, not for a specific customer workload.
