"""Returns Triage — Microsoft Foundry hosted agent (MAF runtime).

Runtime: Agent + FoundryChatClient + ResponsesHostServer (Responses protocol).
- Progressive skill loading via SkillsProvider.from_paths("skills/").
- MCP tools created from mcp-config.json (entries with unresolved ${ENV_VAR}
  URLs are skipped so the agent still runs skills-only before the mock MCP
  ACA is deployed).
- Guarded telemetry init: a missing/broken App Insights connection never kills
  the container (foundry-observability gap O-011 / deploy F-10).
- Async DefaultAzureCredential (deploy F-21: sync credential hangs first request).
- On import/wiring failure, a diagnostic HTTP server keeps the container alive.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("returns-triage")

APP_DIR = Path(__file__).parent
PORT = int(os.environ.get("PORT", "8088"))
_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def _init_telemetry() -> None:
    """Guarded telemetry init — never lets a missing/broken AppIn kill startup."""
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn or "=" not in conn:
        logger.info("APPIN connection string absent/malformed — telemetry disabled (agent functional)")
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn)
        logger.info("Azure Monitor telemetry configured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("App Insights telemetry init skipped: %s (agent functional)", exc)


def _load_instructions() -> str:
    base = (APP_DIR / "copilot-instructions.md").read_text(encoding="utf-8")
    cfg = APP_DIR / "config" / "returns-triage.json"
    if cfg.exists():
        base += "\n\n## Process configuration\n\n```json\n" + cfg.read_text(encoding="utf-8") + "\n```\n"
    return base


def _expand(value: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        return os.environ.get(m.group(1), "")

    return _ENV_RE.sub(repl, value)


def _load_mcp_servers() -> list[dict]:
    """Return [{name, url}] for MCP servers whose URL fully resolves. Skip empties."""
    cfg_path = APP_DIR / "mcp-config.json"
    servers: list[dict] = []
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        for name, spec in (cfg.get("servers") or {}).items():
            url = _expand(spec.get("url", "")).strip()
            if not url or url.startswith("/") or "${" in url:
                logger.warning("Skipping MCP server %r — URL unresolved (%r)", name, spec.get("url"))
                continue
            servers.append({"name": name, "url": url})
    legacy = os.environ.get("MCP_SERVER_URL", "").strip()
    if legacy:
        servers.append({"name": "mcp", "url": legacy.rstrip("/") + "/mcp"})
    return servers


def _parse_tool_results(results):  # avoids the [<Content>] repr leak
    try:
        parts = []
        for r in results or []:
            text = getattr(r, "text", None)
            parts.append(text if text is not None else str(r))
        return "\n".join(parts)
    except Exception:  # noqa: BLE001
        return str(results)


def build_agent():
    from azure.identity.aio import DefaultAzureCredential  # async — F-21
    from agent_framework import Agent, SkillsProvider
    from agent_framework.foundry import FoundryChatClient

    endpoint = (
        os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
        or os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
        or os.environ.get("PROJECT_ENDPOINT")
    )
    model = (
        os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        or os.environ.get("MODEL_DEPLOYMENT_NAME")
        or "gpt-5.4"
    )

    credential = DefaultAzureCredential()
    client = FoundryChatClient(project_endpoint=endpoint, model=model, credential=credential)

    tools = []
    try:
        from agent_framework import MCPStreamableHTTPTool

        for s in _load_mcp_servers():
            tools.append(
                MCPStreamableHTTPTool(name=s["name"], url=s["url"], parse_tool_results=_parse_tool_results)
            )
            logger.info("Registered MCP tool %s -> %s", s["name"], s["url"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP tool wiring skipped: %s", exc)

    skills_provider = SkillsProvider.from_paths(str(APP_DIR / "skills"))

    kwargs = dict(
        client=client,
        instructions=_load_instructions(),
        tools=tools,
        context_providers=[skills_provider],
        default_options={"store": False},
    )
    try:
        return Agent(**kwargs)
    except TypeError:
        kwargs.pop("default_options", None)
        return Agent(**kwargs)


def _diagnostic_server(err: str) -> None:
    """Keep the container alive on import/wiring failure so probes pass + logs are readable."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"returns-triage diagnostic: agent import failed; see logs\n")

        def log_message(self, *a):  # silence
            return

    logger.error("Starting diagnostic server on :%d — %s", PORT, err)
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()


def main() -> None:
    _init_telemetry()  # FIRST — guarded, never raises
    try:
        agent = build_agent()
        try:
            from agent_framework_foundry_hosting import ResponsesHostServer  # type: ignore
        except Exception:  # noqa: BLE001
            from agent_framework.foundry import ResponsesHostServer  # type: ignore
        logger.info("Starting ResponsesHostServer on :%d", PORT)
        ResponsesHostServer(agent).run()
    except Exception as exc:  # noqa: BLE001
        _diagnostic_server(repr(exc))


if __name__ == "__main__":
    main()
