# Canonical GHCP hosted-agent Dockerfile.
# The entrypoint file MUST stay named container.py: the build check and CMD use it.
# MCR base: ACR Tasks (remote builds) hit Docker Hub anonymous pull limits.
FROM mcr.microsoft.com/oryx/python:3.12
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir uv && uv pip install --system --compile-bytecode -r pyproject.toml
# skills/ and copilot-instructions.md are optional (container.py guards both),
# so copy the whole context (filtered by .dockerignore) and fail fast only on
# the required entrypoint.
COPY . .
RUN test -f container.py || (echo "container.py entrypoint missing" >&2; exit 1)
ENV PROJECT_DIR=/app
EXPOSE 8088
CMD ["python", "container.py"]
