# Canonical hosted-agent Dockerfile.
#
# Source of truth for the prose example in
# `../../README.md § Dockerfile`.
#
# Container image for a Foundry hosted agent (GA container-deploy path).
# `azd deploy` builds this remotely in Azure Container Registry by
# default — no local Docker needed. Use `azd ai agent run` when you
# intentionally want to build and run the agent locally.

# MCR base: remote ACR Tasks builds hit Docker Hub anonymous pull limits.
FROM mcr.microsoft.com/oryx/python:3.12
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/
COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project && rm -rf /root/.cache
COPY container.py .
COPY copilot-instructions.md .
EXPOSE 8088
CMD [".venv/bin/python", "container.py"]
