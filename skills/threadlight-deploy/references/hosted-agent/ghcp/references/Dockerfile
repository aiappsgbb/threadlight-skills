# Canonical GHCP hosted-agent Dockerfile.
# The entrypoint file MUST stay named container.py: COPY and CMD below use it.
# MCR base: ACR Tasks (remote builds) hit Docker Hub anonymous pull limits.
FROM mcr.microsoft.com/oryx/python:3.12
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir uv && uv pip install --system --compile-bytecode -r pyproject.toml
COPY container.py copilot-instructions.md ./
COPY skills/ ./skills/
ENV PROJECT_DIR=/app
EXPOSE 8088
CMD ["python", "container.py"]
