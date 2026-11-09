# Phase 4: Teams bot (optional)

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when SPEC § 8 or the user puts Teams in scope.

## Phase 4: Teams Bot (optional)

> **See the `foundry-teams-bot` skill** for complete Teams integration — bot.py, app.py,
> Dockerfile, Bicep modules (UAMI, Bot Service, ACA), Teams manifest, and sideloading.

Teams integration is **optional** — only include it if:
- The spec's § 8 Human Interaction Points specifies Teams as a channel
- The user explicitly asks for Teams exposure
- The `specs/manifest.json` includes conversational interaction traits

If included, the scaffold adds `copilot/` (bot code) and `infra/bot/` (Bicep) to
the azd project. The `foundry-teams-bot` skill's `templates/` directory provides
ready-to-copy files.

> **MANDATORY: start from `bot-streaming.py`, not `bot.py`.** The default
> bot template MUST be the streaming variant (`foundry-teams-bot/templates/copilot/bot-streaming.py`).
> The non-streaming variant (`bot.py`) is **legacy/fallback only** — it
> collects all SSE chunks into a list and sends one big `send_activity()`
> at the end. Result: 60+ seconds of typing-dots silence in Teams,
> followed by a wall of text. Bad UX, makes the demo feel broken.
>
> The streaming variant uses `StreamingResponse` from
> `microsoft_agents.hosting.core.app.streaming.streaming_response` —
> `queue_informative_update()` shows interim status, `queue_text_chunk()`
> streams text progressively, `await end_stream()` finalises. Degrades
> gracefully on non-streaming channels (DirectLine, web chat).
>
> Origin: recent pilot retrospective — bot deployed without streaming;
> user got 60s silence + wall-of-text and thought the bot was broken.
> The streaming refactor is ~30 LOC. There is no reason to ship the
> collect-then-send variant for a Teams pilot.

**If the agent generates files** (XLSX/PDF reports via `save_report` @tool):
- Add `"supportsFiles": true` to the manifest bot config
- The bot must implement the full FileConsentCard flow (see `foundry-teams-bot` skill § Sending Files to Teams)
- The bot captures `agent_session_id` from `response.completed`, lists/downloads files from the session files API, then sends FileConsentCard → invoke handler → OneDrive upload → FileInfoCard
