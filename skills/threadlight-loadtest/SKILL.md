---
name: threadlight-loadtest
description: >
  Manual, live/cost-bearing skill that runs a budget-capped load-test
  profile through k6, locust or an injected adapter and emits
  `specs/load-manifest.json` under the shared threadlight.load/v1 envelope,
  so pilots get real latency/throughput/error-rate evidence instead of
  static claims. USE FOR: load testing, latency benchmarking, throughput
  testing, k6, locust, LOAD-001/LOAD-002/LOAD-003 findings, budget-capped
  load runs, production load-test confirmation, load-manifest evidence,
  p50/p95 latency, error-rate and token evidence, hosted agent latency
  waterfall (runtime vs model vs MCP vs cold start, cost per 1,000
  requests) on a deployed GHCP pilot, voice calls at 8 kHz.
  DO NOT USE FOR: deploying
  infrastructure or agents (threadlight-deploy / microsoft-foundry /
  azure-deploy); autonomous/agentic-loop execution (never runs unattended
  or installs k6/locust); release or rollout gating
  (threadlight-production-ready); quality/groundedness evals
  (threadlight-evals); adversarial/safety scans (threadlight-redteam).
metadata:
  version: "0.3.0"
---

# Threadlight Load Test — guarded, budget-capped load evidence

A **manual, live, cost-bearing** skill. Running it can spend real money
(token cost against a live endpoint) and can hit a production endpoint if you
let it. Every run is gated **before** any load-generation command is ever
invoked:

1. **Budget ceiling.** You must supply `budget_ceiling_usd`. The run's
   projected token cost is compared **directly** to the ceiling: preferring the
   profile's declared `projected_token_cost_usd`, otherwise a deterministic
   derivation from explicit request/rate + token-rate inputs. A **known**
   projection over the ceiling **aborts** — no adapter is ever called. A
   projection that cannot be determined (no declared value and no explicit
   inputs to derive one) yields a `partial` / `LOAD-002: not-verified` manifest
   rather than a fabricated request count that could undercount real spend.
2. **Production confirmation.** If `endpoint_class == "production"`, the run
   **aborts** unless you pass `allow_production=True` explicitly.
3. **No surprise installs.** If neither `k6` nor `locust` is already on
   `PATH`, the skill does **not** install anything. It emits a `partial`
   manifest with `LOAD-002: not-verified` and tells you what's missing.

This skill never deploys anything, never loops autonomously, and never
gates a release by itself — it produces evidence for a human (or a paired
skill like `threadlight-production-ready`) to read.

## What this skill does (and does not)

- **Runs:** one guarded execution of a load profile against an adapter (`k6`,
  `locust`, or an injected adapter for tests), then summarizes the resulting
  samples (p50/p95/p99 latency, error rate, tokens/request, observed
  throughput, and cold-start / time-to-scale when samples carry them).
- **Emits:** `specs/load-manifest.json` (`threadlight.load/v1`), atomically
  written and schema-validated. A failed write never clobbers the previous
  valid manifest.
- **Never installs dependencies.** `k6`/`locust` must already be on `PATH`.
  If neither is found, the manifest records `LOAD-002: not-verified` — it
  does not attempt `pip install`, `npm install`, `brew install`, etc.
- **Never runs unattended / in an agentic loop.** There is no scheduler, no
  retry loop, no autonomous re-run. A human (or an explicit CI step) invokes
  it once per evidence run.
- **Never deploys or releases anything.** No infra provisioning, no rollout
  gating. Use `threadlight-deploy` / `threadlight-deploy/references/azd-modules` for deployment and
  `threadlight-production-ready` for release-readiness scoring.
- **Never writes SPEC.md automatically.** Only a `complete` run may propose an
  advisory patch snippet (`spec_update_plan`, a safe quoted-YAML block under
  `load_profile/performance`) for a human to paste into `SPEC.md` by hand — it
  is never applied automatically, and it is **omitted entirely** from partial or
  aborted manifests.
- **Never persists secrets or payloads.** No access tokens, prompts, model
  responses, request/response bodies, endpoint URLs, credential references,
  or raw command stdout/stderr ever reach the manifest (see Privacy below).

## The profile you provide

The **approved minimal shape** carries a direct, declared budget projection
plus the volume it describes:

```json
{
  "peak_requests_per_second": 2,
  "hold_seconds": 10,
  "projected_token_cost_usd": 5.00
}
```

A richer profile can also describe a live run (endpoint, script, SLO) and/or
let the projection be **derived** deterministically from explicit inputs:

```json
{
  "name": "checkout-agent-smoke",
  "endpoint": {"url": "https://staging.example.test/api", "credential_ref": "kv:load-test-key"},
  "peak_requests_per_second": 10,
  "hold_seconds": 30,
  "request_count": 300,
  "tokens_per_request_estimate": 500,
  "price_per_1k_tokens_usd": 0.002,
  "slo": {"max_p95_latency_ms": 800, "max_error_rate": 0.02},
  "script_path": "loadtest/checkout.js",
  "adapter_args": ["--summary-trend-stats", "p(50),p(95),p(99)"]
}
```

| Field | Required | Meaning |
|---|---|---|
| `projected_token_cost_usd` | no¹ | **Declared** budget projection in USD, compared directly to the ceiling. Nonnegative + finite; `0` is honored **only** because it is explicit. |
| `peak_requests_per_second` / `hold_seconds` | no | Volume of the approved shape; `peak_requests_per_second × hold_seconds` also feeds a **derived** projection. Both strictly positive. |
| `request_count` | no | Explicit total request count for a **derived** projection (positive integer). Never defaulted from `virtual_users` (that would undercount). |
| `tokens_per_request_estimate` / `price_per_1k_tokens_usd` | no¹ | Token-rate inputs for a **derived** projection (nonnegative). |
| `name` | no | Human-readable profile name; defaults to `load-profile` in the manifest if absent. |
| `endpoint.url` / `endpoint.credential_ref` | no (both, to run live) | Target + credential **reference name**, never the secret itself. Absent ⇒ `partial`, `LOAD-002: not-verified`. A present-but-malformed endpoint is a controlled validation error. |
| `duration_s` | no | Legacy run duration in seconds (positive). Used for throughput/derivation only when `hold_seconds` is absent. |
| `virtual_users` | no | Concurrent virtual users (positive integer). Descriptive only — **never** used to project cost. |
| `spawn_rate_per_s` | no | Ramp-up rate passed to the adapter |
| `slo` | no | `max_p95_latency_ms` / `max_error_rate` thresholds scored into `LOAD-003` |
| `script_path` | no | Load-test script path passed to `k6 run` / `locust -f` |
| `adapter_args` | no | Optional engine-specific summary/stat presentation argv. Must be a list of non-empty, control-character-free strings; see **Safe adapter arguments** below. |

¹ *Nothing is structurally required.* To get a budget verdict you need **either**
a declared `projected_token_cost_usd` **or** enough explicit inputs to derive
one (`request_count` — or `peak_requests_per_second × hold_seconds` — together
with `tokens_per_request_estimate` **and** `price_per_1k_tokens_usd`). Otherwise
the projection is `unavailable` and the run is `partial` / not-verified.

Unknown profile keys are rejected — this is a strict allowlist, not a
free-form bag.

## The contract — `specs/load-manifest.json`

| Field | Meaning |
|---|---|
| `schema` | Always `threadlight.load/v1` |
| `tool_version` | Version of `scripts/loadtest.py` (currently `0.1.0`) |
| `generated_at` / `freshness` | Shared envelope timestamp fields; `source_oldest_at` is the earliest `observed_at` found across samples, or `null` |
| `status` | `complete` \| `partial` \| `aborted` — **never** reports `complete` for an aborted or partial run |
| `profile_name` | Echo of the profile's `name` |
| `endpoint_class` | `non-production` \| `production` |
| `endpoint_configured` | `true` only if the profile declared both a URL and a credential reference name (never the values themselves) |
| `allow_production` | Echo of the caller's explicit production confirmation |
| `adapter_name` | Selected engine name (`k6`, `locust`, injected name), or `null` if none was selected/available |
| `budget.ceiling_usd` | The mandatory positive ceiling |
| `budget.projected_usd` | Projected cost in USD, or `null` when `projection_source == "unavailable"` |
| `budget.within_ceiling` | Whether the projection stayed under the ceiling, or `null` when unavailable |
| `budget.projection_source` | `declared` (profile's `projected_token_cost_usd`) \| `derived` (computed from explicit inputs) \| `unavailable` (could not determine without inventing inputs) |
| `diagnostics` | `sample_count`, `p50_latency_ms`, `p95_latency_ms`, `p99_latency_ms`, `error_rate`, `tokens_per_request`, `throughput_rps`, `cold_start_latency_ms`, `time_to_scale_s` (each `null` when not observed), `adapter_error` (scrubbed, ≤220 chars, or `null`) |
| `spec_update_plan` | **Omitted entirely** unless `status == "complete"` (never a `null` placeholder); when present, an advisory `{action: "advisory", target: "SPEC.md", section: "load_profile/performance", snippet}` object |
| `findings` | Exactly one each of `LOAD-001`, `LOAD-002`, `LOAD-003` — no duplicates, missing, or unknown ids — each `pass \| must-fix \| should-fix \| not-verified` |

## Findings

| ID | Dimension | `must-fix` when | `not-verified` when |
|---|---|---|---|
| `LOAD-001` | Production safety | `endpoint_class == "production"` and `allow_production` is not `true` | — |
| `LOAD-002` | Budget / execution | A **known** projection exceeded the ceiling (run aborted) | The budget projection was `unavailable`; no adapter was selected/available; the endpoint/credential was not configured; or the adapter itself returned `partial` (including a `complete` claim with zero samples, which is treated as untrustworthy) |
| `LOAD-003` | SLO / quality | Declared `slo` thresholds were violated by the observed samples | No samples were collected or no `slo` was declared |

Aborted and partial runs are **never** represented as successful: `status`
stays `aborted`/`partial`, `spec_update_plan` is **omitted**, and `LOAD-002`
reflects exactly why.

## Gate order (what happens before any command runs)

1. Structural validation of the profile, budget ceiling, `endpoint_class`,
   and `allow_production` — a caller mistake raises `LoadTestValidationError`
   immediately (this is a programming-usage error, not a manifest state). A
   *missing* endpoint or projection is **not** a caller mistake — it flows to a
   partial gate below.
2. **Budget gate (known projection):** a declared/derived projection strictly
   over the ceiling ⇒ `status: aborted`, `LOAD-002: must-fix`. Adapter is never
   called.
3. **Production gate:** `endpoint_class == "production"` without
   `allow_production=True` ⇒ `status: aborted`, `LOAD-001: must-fix`.
   Adapter is never called.
4. **Projection gate:** the projection is `unavailable` (neither declared nor
   deterministically derivable) ⇒ `status: partial`, `LOAD-002: not-verified`.
   We never invent a request count to fabricate a projection.
5. **Adapter gate:** no adapter selected/injected ⇒ `status: partial`,
   `LOAD-002: not-verified`. Nothing is installed.
6. **Endpoint gate:** profile missing `endpoint.url` or `endpoint.credential_ref`
   ⇒ `status: partial`, `LOAD-002: not-verified`.

Only after all gates pass is `adapter.run(profile)` invoked — exactly once.
`LOAD-001` is always reported accurately even when a different gate aborts the
run (a budget abort on a production endpoint still shows `LOAD-001: must-fix`).

## Adapters

`LoadAdapter` is a `Protocol`: any object with a `name: str` attribute and a
`run(profile) -> {"status": "complete"|"partial", "samples": [...], "error"?: str}`
method satisfies it. Tests inject fakes; production code uses
`select_adapter(available_commands)` (k6, then locust, then `None` — pure
selection, no side effects) and `CommandLoadAdapter`, which:

- invokes **only** the one selected, already-existing command (`shutil.which`
  probe, no install);
- always passes argv as a **list** (never a shell string) with `shell=False`;
- always sets a `timeout_s`;
- scrubs secrets out of any error text before it can reach the manifest:
  Bearer tokens, `key=value`-shaped secrets, embedded URL credentials,
  API keys, JWTs, Azure SAS `sig=` query tokens, storage/connection-string
  keys (`AccountKey=` / `SharedAccessKey=` / `SharedAccessSignature=`), PEM
  private-key blocks, and pre-masked `******` markers (each replaced
  with `[REDACTED]`);
- never returns raw command stdout/stderr — only parsed NDJSON samples and a
  scrubbed, truncated error summary.

### Safe adapter arguments

`adapter_args` is deliberately narrow and fail-closed. Tokens are passed
**verbatim** in list-form argv (never through a shell): k6 arguments are placed
after Threadlight's guarded options and before the script path; locust arguments
are placed after all guarded standard flags. An empty list is equivalent to
omitting the field.

Allowed presentation-only controls:

- **k6:** `--summary-trend-stats`, `--summary-time-unit`, and
  `--summary-mode`, either followed by one non-option value or as
  `--option=value`.
- **locust:** `--only-summary`, `--print-stats`, and `--reset-stats`.

Everything else is rejected with `AdapterArgumentError`; a guarded
`run_loadtest` converts that to `status: partial`, `LOAD-002: not-verified`,
without invoking the adapter. In particular, arguments may not replace or add:

- the command/subcommand or script/locustfile;
- endpoint/host/environment/config inputs (including a profile endpoint or
  credential-reference value);
- VUs/users, spawn rate/RPS/stages/iterations, or duration/run time;
- headless/worker/master/process behavior; or
- stdout/log/debug controls and persistent outputs such as k6 `--out`,
  `--summary-export`, or `--console-output`, and locust CSV/HTML/JSON/log files.

This prevents extra argv from changing Threadlight's safety and budget
ownership or persisting raw request data. The endpoint remains supplied only by
Threadlight's standard guarded `TARGET_URL`/`--host` position; credentials and
credential references are never added to argv.

## Privacy

`write_load_manifest` runs the manifest through the schema-mirroring
validator, **then** a recursive forbidden-key + secret-value scan, and only
then performs an atomic write. Either check failing means nothing is written
and the previous valid manifest (if any) is untouched. Forbidden key words
include `token`, `secret`, `password`, `credential(s)`, `authorization`,
`prompt`, `completion(s)`, `payload`, `stdout`, `stderr` (word-matched, so
`tokens_per_request` is unaffected). Forbidden value shapes include
Bearer-style tokens, `sk-...` API keys, JWTs, embedded URL credentials,
Azure SAS `sig=` tokens, connection-string keys (`AccountKey=` /
`SharedAccessKey=`), PEM private-key material, and pre-masked `******`
markers — a strict allowlist of value shapes, so opaque metric IDs
(UUIDs, hex / metric identifiers) are never false-positives.

## Usage

```python
import shutil
import sys
sys.path.insert(0, "skills/threadlight-loadtest/scripts")
from loadtest import run_loadtest, write_load_manifest
from adapters import detect_available_commands, select_adapter, CommandLoadAdapter

profile = {
    "name": "checkout-agent-smoke",
    "endpoint": {"url": None, "credential_ref": None},  # dry-run: no live endpoint
    "peak_requests_per_second": 10,
    "hold_seconds": 30,
    "projected_token_cost_usd": 3.00,  # declared → compared directly to the ceiling
}

# detect_available_commands() returns a *set* of names (a pure shutil.which
# probe), so resolve the chosen name's path with shutil.which — never index the
# set. (loadtest.build_cli_adapter() wraps these three lines if you don't need
# the intermediate values.)
name = select_adapter(detect_available_commands())    # "k6" | "locust" | None
command_path = shutil.which(name) if name else None
adapter = (
    CommandLoadAdapter(name=name, command_path=command_path)
    if command_path else None
)

manifest = run_loadtest(
    profile=profile,
    budget_ceiling_usd=5.00,       # MANDATORY — no default
    endpoint_class="non-production",
    allow_production=False,        # MUST be True to touch a production endpoint
    adapter=adapter,
    generated_at="2026-08-17T10:00:00Z",
)
write_load_manifest("specs/load-manifest.json", manifest)
```

Or from the CLI:

```bash
python3 scripts/loadtest.py \
  --profile profile.json \
  --budget-ceiling-usd 5.00 \
  --endpoint-class non-production \
  --out specs/load-manifest.json

# Production run — requires explicit confirmation flag:
python3 scripts/loadtest.py \
  --profile profile.json \
  --budget-ceiling-usd 5.00 \
  --endpoint-class production \
  --allow-production \
  --out specs/load-manifest.json
```

## Hosted agent performance evidence (latency waterfall)

For a deployed GHCP pilot (Foundry hosted agent on the Invocations protocol,
generated by `threadlight-deploy`), `scripts/perf_evidence.py` answers the
production question "where does the time go, and what does it cost at
concurrency?" with a real run, not a projection. It uses the same gates as
above (`run_loadtest`: budget ceiling, production confirmation, endpoint), then:

1. **Drives the real endpoint** (`hosted_agent.py`, an injected adapter):
   `{project}/agents/{agent}/endpoint/protocols/invocations?api-version=v1`
   with an Entra token, streamed SSE, in phases: **cold** (a new
   `agent_session_id` per request, so each one starts a sandbox; hosted agents
   run [one VM-isolated sandbox per session](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#scaling-and-right-sizing)),
   a **ramp** (1, 2, 5) and **steady** concurrency (one session per virtual
   user). Every request sends `"new_conversation": true`, so conversations do
   not grow. The MCP Container App replica count is read before and after, so
   a scale-from-zero cold phase is visible. Prompts are the pilot's
   `tests/eval_dataset.jsonl` inputs in rotation; they never reach any output.
2. **Builds the waterfall from the agent's own traces** (`waterfall.py`). The
   Invocations host exports a server span (`invoke_agent`) to the project's
   Application Insights ([Learn](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-hosted-agent-telemetry)).
   The Copilot CLI makes model and MCP calls in its own process, so the GHCP
   container template turns the SDK's session events into child spans:
   `threadlight.copilot.turn` (with `threadlight.invocation_id`),
   `threadlight.copilot.session_open`, `chat <model>` (SDK duration, time to
   first token, input / cached / output / reasoning tokens, HTTP status on
   failure) and `execute_tool <tool>` (MCP server named). Metadata only: no
   prompt, answer, tool arguments or results. Per request: end to end and
   first token (client), endpoint+sandbox+network, session start, model calls,
   tool calls, MCP calls, runtime residual, cold start; p50/p95 split cold and
   warm; error classes and model 429s; tokens per request.
3. **Capacity and cost** (`capacity.py`): the model deployment's TPM/RPM limits
   and the MCP scale rule read from Azure, the steady phase's share of TPM/RPM,
   sessions started, documented limits with Learn links, and the model cost
   per 1,000 requests **calculated** from measured tokens and Azure Retail
   Prices meters (cached input priced separately). Hosted compute is an
   **estimate** (upper bound to the idle timeout): session active time is not
   measured.
4. **Writes** `specs/load-manifest.json` (unchanged contract),
   `specs/perf-evidence.json` (`threadlight-perf-evidence/v1`),
   `specs/perf-run-raw.json` (timings, ids and span metadata for a rebuild) and
   `docs/perf-evidence.md` / `.html` in the pilot value page style. The pilot
   value page (`threadlight-production-ready/scripts/pilot_value.py`) shows a
   **Performance under load** item from a complete run.

What the traces **cannot** split is listed in the report and never estimated:
Foundry endpoint routing vs sandbox start vs client network (one number,
`client - container`), model queueing vs inference, an MCP scale-from-zero
start (no span of its own: the Copilot CLI connects to MCP servers when the
session opens, so it lands in session start), and Copilot CLI orchestration vs
host vs streaming (one residual). Voice turns (`/invocations_ws`) are not instrumented.

```bash
# from the pilot root, logged in with az and azd; a smoke request gives the token estimate
python <threadlight-skills>/skills/threadlight-loadtest/scripts/perf_evidence.py \
  --workspace . --agent <agent-name> --budget-ceiling-usd 80 \
  --tokens-per-request-estimate 126000 --max-concurrency 10 --steady-requests 200
# re-render the report from saved evidence, no requests
python .../perf_evidence.py --workspace . --render-only
```

The budget projection is conservative (all tokens at the uncached input
price). It needs the agent redeployed from the current GHCP template so the
turn spans exist; with an older container the run completes as `partial`
(tokens not measured) instead of reporting zero tokens. This is evidence for a
pilot at modest scale, not a production SLA or a platform benchmark.

## Voice workload harness (scripted spoken calls)

For a pilot with the opt-in voice front-end (Foundry Hosted Voice wrapping the
GHCP hosted agent, SPEC § 11e `voice_frontend: hosted-voice`),
`scripts/voice_eval.py` measures voice as a workload. It plays a fixed corpus of
caller utterances into one or more deployed voice agents over the managed voice
WebSocket and records, per turn:

| Measure | How |
|---|---|
| Speech recognition | WER of `conversation.item.input_audio_transcription.completed` against the reference, after normalising case, punctuation, spelled letters and digit runs; separately, whether the case id digits were heard |
| Task outcome | decision named first in the spoken answer vs the corpus `expected_decision` (keyword heuristic per language; raw transcripts stay in the local results for review) |
| Latency | end of caller speech (last audio frame sent) to first agent audio (often the hold message), to the answer audio, and to `response.done`; call duration |
| Barge-in | the caller starts the next utterance 1.5 s after the agent starts talking: time to `input_audio_buffer.speech_started`, status of the interrupted response, agent audio still delivered after detection, and whether the follow-up is answered correctly |
| Cost per call | Voice Live tokens from Azure Monitor (`VoiceLiveAudioInputTokens`, `VoiceLiveAudioOutputTokens`, text and cached variants) and the engine model's `ProcessedPromptTokens` / `GeneratedTokens` for each variant's time window, priced with the Azure Retail Prices API: **calculated, not billed** |

Audio conditions: `wb16` (16 kHz TTS), `nb8` (low-passed at 3.4 kHz and resampled
to 8 kHz, like a narrowband phone line) and `nb8_noise` (`nb8` plus seeded noise
at 10 dB SNR). The client converts to the voice agent's input format: PCM 24 kHz,
or G.711 mu-law 8 kHz. Silence is streamed continuously, as on a phone line.

**Synthetic TTS callers are not a substitute for testing with human callers**,
real phones and networks, accents and real background noise. The manifest
records `audio_source: synthetic-tts` and `human_caller_tested: false`.

Comparing options on the same agent: add more `kind: voice` services to
`azure.yaml` that use the same hosted agent as conversation engine and differ
only in documented audio settings, then list them in a variants file
([`variants.example.json`](references/voice-eval/variants.example.json)). The
reference run compared:

| Variant | Wrapper audio settings (azure.yaml) |
|---|---|
| `pcm24-speech` | generated default: `audio/pcm` 24 kHz in and out, `azure-speech` (automatic multilingual), `server_vad` |
| `g711-speech-dns` | `audio/pcmu` (G.711 mu-law, 8 kHz) in and out, `azure-speech`, `noiseReduction: azure_deep_noise_suppression` |
| `pcm24-mai` | `audio/pcm` 24 kHz, transcription `mai-transcribe-2` |

The input formats `pcm16`, `g711_ulaw`, `g711_alaw` and the 8 kHz output formats
are documented in the
[Voice Live API reference](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-api-reference-2026-07-15#components);
`azure-speech` automatic multilingual mode covers English and Italian but not
Greek, which needs an explicit `language` or `mai-transcribe-2`
([supported languages](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-language-support?tabs=speechinput)).
On a hosted-agent wrapper the voice agent owns the audio settings and the client
sends no `session.update`
([Learn](https://learn.microsoft.com/azure/foundry/how-to/voice-first-with-hosted-agent)),
so each option is its own wrapper.

```sh
python3 scripts/voice_eval.py prepare --corpus references/voice-eval/corpus.returns-triage.json \
  --audio-dir .voice-eval/audio --speech-region <region> --speech-resource-id <Foundry account id>
python3 scripts/voice_eval.py run --corpus references/voice-eval/corpus.returns-triage.json \
  --variants variants.json --audio-dir .voice-eval/audio --results-dir .voice-eval/results \
  --project-endpoint "$AZURE_AI_PROJECT_ENDPOINT" --budget-ceiling-usd 10 --endpoint-class non-production
python3 scripts/voice_eval.py report --corpus references/voice-eval/corpus.returns-triage.json \
  --results-dir .voice-eval/results --prices references/voice-eval/prices.example.json \
  --account-resource-id <Foundry account id> --out specs/voice-eval-manifest.json --markdown specs/voice-eval-report.md
```

`prepare` uses Azure text to speech with Microsoft Entra auth (Cognitive Services
Speech User on the account). `run` applies the same gates as the load test: a
mandatory budget ceiling against `corpus.projected_usd_per_call` × planned calls,
and `--allow-production` for a production endpoint; it aborts before any call
otherwise. It waits `--gap-s` (default 150 s) between variants so Azure Monitor's
one-minute grain separates their token windows. `report` writes
`specs/voice-eval-manifest.json` (`threadlight.voice-eval/v1`, aggregates only;
the writer rejects transcripts and secret-shaped values) and a Markdown table.

What Azure measures, and what the harness measures instead:

- **Azure Monitor platform metrics** on the Foundry (`Microsoft.CognitiveServices/accounts`)
  resource include Voice Live audio, text and cached token counts at one-minute
  grain, split by `ApiName`, `FeatureName`, `UsageChannel`, `Region`
  ([supported metrics](https://learn.microsoft.com/azure/azure-monitor/reference/supported-metrics/microsoft-cognitiveservices-accounts-metrics)).
  There is no per-agent or per-call dimension and no latency metric there.
- **Foundry voice agent monitoring** (preview) shows average time to first audio,
  sessions, turns, session duration, audio tokens, TTFT/TTFA, LLM, speech-to-text
  and text-to-speech latency and an estimated cost from traces in the connected
  Application Insights
  ([voice agent tracing, monitoring and evaluation](https://learn.microsoft.com/azure/foundry/agents/concepts/voice-agent-observability)).
  Those cost attributes are estimates, not invoice records
  ([voice agent pricing](https://learn.microsoft.com/azure/foundry/agents/concepts/voice-agent-pricing)).
- **The harness** measures from the caller side: what a caller would wait for
  (end of speech to first and answer audio, including the hold message),
  recognition and outcome per language and audio condition, and barge-in. It
  reads Azure Monitor tokens per variant window; when they are absent it falls
  back to the Learn rule of thumb (~10 tokens per second of input audio, ~20 per
  second of output) and labels the row `learn-estimate`.

Which Voice Live meter bills a hosted-agent wrapper (no Voice Live model) is not
documented; the report prices the same tokens under each candidate meter row in
[`prices.example.json`](references/voice-eval/prices.example.json). Hosted
agent compute (vCPU and memory per active session) is billed separately and is
not attributed per call.

### Human caller (browser microphone)

`scripts/voice_human_caller.py` lets a person run the same scored calls in their
own voice. It starts a local page (`http://127.0.0.1:8790`) and a localhost proxy
that holds the Microsoft Entra token (a browser WebSocket cannot send an
Authorization header). One click starts the script: each line appears on screen,
the person reads it, the call ends by itself when the answer has been played,
and a barge-in line tells them when to interrupt. `--ui-lang it` shows the page
in Italian; `--only` picks lines from
[`corpus.human-caller.json`](references/voice-eval/corpus.human-caller.json).
Each call is appended to `calls.jsonl` in the harness format, so
`voice_eval.py report --audio-source human-mic` scores it the same way and the
manifest records `audio_source: human-mic`, `human_caller_tested: true`.

```sh
python3 scripts/voice_human_caller.py --project-endpoint "$AZURE_AI_PROJECT_ENDPOINT" \
  --agent <agent>-voice --ui-lang it --corpus references/voice-eval/corpus.human-caller.json \
  --results-dir .voice-eval/human --only h-it-escalate h-en-deny h-en-barge
```

End of speech is the end of the last microphone frame above the speech level
before the server's `speech_stopped`; browser capture and playback buffering are
not included. Use headphones: speaker echo can trigger the agent's interruption
detection. One person in one room is real speech, not a phone line or a caller
population.

Live results (2026-11-11) are in
[production-readiness.md](../../docs/production-readiness.md#voice-readiness-only-when-the-pilot-has-a-voice-front-end).

## Files

```
SKILL.md
scripts/adapters.py                    # LoadAdapter Protocol, select_adapter, CommandLoadAdapter
scripts/loadtest.py                    # validation, summarize_samples, run_loadtest, CLI, manifest I/O
scripts/hosted_agent.py                # Invocations SSE driver, cold/ramp/steady phases, HostedAgentAdapter
scripts/waterfall.py                   # App Insights join, per-request segments, p50/p95, separability
scripts/capacity.py                    # ARM capacity facts, Retail Prices meters, cost per 1,000 requests
scripts/perf_evidence.py               # CLI: gated run -> evidence JSON -> Markdown/HTML report
references/load-manifest.schema.json   # threadlight.load/v1 manifest contract (draft-07)
scripts/voice_eval.py                  # voice workload harness (prepare / run / report)
references/voice-eval/                 # example corpus, variants, price meters, voice-eval manifest schema
tests/test_loadtest.py                 # pytest suite (FakeAdapter, gates, percentiles, privacy, schema parity)
tests/test_hosted_agent.py             # driver, phases, SSE timing, budget gate before any request
tests/test_waterfall.py                # segments, union of parallel calls, cold start, KQL batching
tests/test_capacity.py                 # rate limits, meters, calculated vs estimated cost
tests/test_perf_evidence.py            # evidence labels, report rendering, no prompt leakage

tests/test_voice_eval.py               # offline maths: audio conversion, WER, timeline, gates, cost, manifest
```

## Tests

```bash
python3 -m pytest skills/threadlight-loadtest/tests/ skills/_shared/tests/test_manifest.py -v
# GHCP turn spans (container template)
python3 -m pytest skills/threadlight-deploy/tests/test_ghcp_turn_telemetry.py -v
```

## Common mistakes

- **Forgetting `budget_ceiling_usd`.** It is a required keyword argument —
  there is no default ceiling, and a missing/zero/negative/non-finite value
  raises `LoadTestValidationError` before anything runs.
- **Expecting a production run without `allow_production=True`.** The run
  aborts by design; this is not a bug.
- **Expecting the skill to install k6/locust.** It only probes `PATH`. Install
  the engine yourself, then re-run.
- **Treating a `partial` manifest as evidence of success.** `LOAD-002:
  not-verified` means exactly that — the run did not produce trustworthy
  complete evidence, whether because the budget projection was `unavailable`,
  no adapter ran, no endpoint was configured, or the adapter itself only
  completed part of the profile.
- **Expecting a budget verdict without a projection.** The skill will **not**
  invent a request count (e.g. from `virtual_users`) to fabricate a cost — that
  could undercount real spend. Supply a declared `projected_token_cost_usd`, or
  enough explicit inputs to derive one; otherwise you get `partial` /
  not-verified, not a green light.
- **Assuming a missing endpoint is a usage error.** An absent endpoint/credential
  yields a `partial` manifest (`LOAD-002: not-verified`), not a raised
  exception. Only a *malformed* endpoint (wrong types) is a controlled
  validation error.
- **Expecting `spec_update_plan` to modify `SPEC.md`.** It is advisory
  quoted-YAML only, present **only** on a `complete` run and omitted entirely
  otherwise, returned inside the manifest for a human to paste in by hand.
