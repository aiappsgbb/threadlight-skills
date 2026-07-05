# ACA Job (cron) receiver — Shape #1. Built by remoteBuild in ACR.
FROM python:3.11-slim

WORKDIR /app

# uv for fast, reproducible installs from pyproject.toml.
RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system .

# _shared/idempotency.py is copied in by the skill's Step 3 (verbatim from
# references/idempotency-patterns.md). Bring the whole trigger tree.
COPY . .

# ACA Job runs the receiver once per scheduled trigger, then exits.
CMD ["python", "receiver.py"]
