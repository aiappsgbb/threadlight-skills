FROM mcr.microsoft.com/oryx/python:3.12

WORKDIR /app

# uv for dependency management (prerelease support for the foundry-hosting package).
# Pin uv:0.7 — uv:latest (0.8+) resolves prereleases differently.
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project \
    && rm -rf /root/.cache

COPY container.py .
COPY copilot-instructions.md .
COPY skills/ skills/
COPY config/ config/
COPY mcp-config.json .

EXPOSE 8088

CMD [".venv/bin/python", "container.py"]
