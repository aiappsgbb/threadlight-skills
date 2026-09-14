ARG PYTHON_IMAGE
FROM --platform=linux/amd64 ${PYTHON_IMAGE}
ARG PYTHON_IMAGE
RUN python -c "import re,sys; assert re.fullmatch(r'.+@sha256:[a-f0-9]{64}', sys.argv[1]); assert sys.version_info[:2] == (3,12)" "$PYTHON_IMAGE"
WORKDIR /app/repo
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ACS_OPA_PATH=/usr/local/bin/opa \
    PYTHONPATH=/app/repo:/app/repo/skills/threadlight-govern/references:/app/repo/examples/returns-triage-governed/src/agent:/app/repo/skills/threadlight-deploy/references/governance \
    OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false
COPY . .
RUN python -m pip install --no-cache-dir \
    ./skills/threadlight-govern/references/control-plane \
    ./skills/threadlight-govern/references/gateway ./agent-dependencies
RUN python skills/threadlight-deploy/references/governance/install_opa.py && \
    python -m pip check && useradd --uid 10001 --create-home govern
USER 10001:10001
EXPOSE 8000 8088
ENTRYPOINT ["python", "skills/threadlight-deploy/references/governance/returns_mcp_agent.py"]
