# ACA Job (manual) receiver — Shape #2. Built by remoteBuild in ACR.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system .

# _shared/idempotency.py is copied in by the skill's Step 3.
COPY . .

# ACA Job runs once per manual `start`, then exits.
CMD ["python", "receiver.py"]
