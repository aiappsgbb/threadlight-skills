# Threadlight Lifecycle Canvas

Threadlight Lifecycle Canvas is an optional GitHub Copilot App enhancement for the Threadlight skills plugin. It gives operators an outcome-oriented view of the 17-skill lifecycle, projects progress from committed Threadlight artifacts, and sends safe next-action intents back to chat.

## Requirements

- Minimum tested GitHub Copilot App version: **1.0.78-2**.
- Canvas/extensions are experimental and must be enabled in the Copilot App.
- Installing the plugin exposes this extension wherever the plugin is enabled.
- Non-Canvas hosts retain the normal skills and artifacts UX.

## Security model

- The loopback server binds only to `127.0.0.1` and uses a per-instance capability token.
- Artifact reads use an allowlist that excludes `.env` and `.azure/**/.env`.
- Canvas actions send validated intents to chat only. They never run Threadlight stages, direct file/process operations, or Azure operations.

## Troubleshooting

- If the panel does not appear, confirm Canvas/extensions are enabled and the plugin is installed.
- Inspect startup issues with `/extensions manage` or `extensions_manage inspect`.
- Refresh the panel after changing Threadlight artifacts if updates are not visible.

## Support

Use the repository issue tracker with the Copilot App version, plugin version, host OS, and output from `extensions_manage inspect` when available.

## Reporting security issues

Do not include secrets or customer data in reports. Share only sanitized artifact paths, error messages, and reproduction steps.
