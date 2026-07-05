# ACA Consumer (KEDA) receiver — Shape #4. Built by remoteBuild in ACR.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system .

# _shared/idempotency.py is copied in by the skill's Step 3.
COPY . .

# Long-running consumer: pulls from Service Bus until KEDA scales it to zero.
CMD ["python", "receiver.py"]
