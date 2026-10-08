# Canonical GHCP hosted-agent Dockerfile.
# The entrypoint file MUST stay named container.py: the build check and CMD use it.
# MCR base: ACR Tasks (remote builds) hit Docker Hub anonymous pull limits.
FROM mcr.microsoft.com/oryx/python:3.12
WORKDIR /app
# Exact, sha256-hashed pins resolved from pyproject.toml (see requirements.lock).
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
# skills/ and copilot-instructions.md are optional (container.py guards both),
# so copy the whole context (filtered by .dockerignore) and fail fast only on
# the required entrypoint.
COPY . .
RUN test -f container.py || (echo "container.py entrypoint missing" >&2; exit 1)
ENV PROJECT_DIR=/app
EXPOSE 8088
CMD ["python", "container.py"]
