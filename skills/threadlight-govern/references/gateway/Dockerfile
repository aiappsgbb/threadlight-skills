FROM --platform=linux/amd64 python:3.12-slim AS builder
WORKDIR /build
ENV TMPDIR=/build/scratch PIP_DISABLE_PIP_VERSION_CHECK=1
RUN mkdir -p /build/scratch /wheels
COPY skills/threadlight-govern/references/control-plane /build/skills/threadlight-govern/references/control-plane
COPY skills/threadlight-govern/references/gateway /build/skills/threadlight-govern/references/gateway
COPY skills/threadlight-govern/scripts/policy_bundle.py /build/skills/threadlight-govern/scripts/policy_bundle.py
COPY skills/threadlight-governed-actions/scripts/canonical.py skills/threadlight-governed-actions/scripts/__init__.py /build/skills/threadlight-governed-actions/scripts/
COPY skills/_shared/governance-upstream-pin.json /build/skills/_shared/governance-upstream-pin.json
RUN python -m pip wheel --wheel-dir /wheels \
    ./skills/threadlight-govern/references/control-plane \
    ./skills/threadlight-govern/references/gateway
RUN python - <<'PY'
import hashlib, json, pathlib, urllib.request
pin = json.loads(pathlib.Path("skills/_shared/governance-upstream-pin.json").read_text())["opa"]
url = f"https://github.com/open-policy-agent/opa/releases/download/v{pin['version']}/opa_linux_amd64_static"
with urllib.request.urlopen(url, timeout=120) as response:
    binary = response.read()
assert hashlib.sha256(binary).hexdigest() == pin["linux_amd64_static_sha256"]
pathlib.Path("/build/opa").write_bytes(binary)
PY

FROM --platform=linux/amd64 python:3.12-slim
WORKDIR /app
ENV TMPDIR=/app/scratch PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ACS_OPA_PATH=/usr/local/bin/opa GATEWAY_CONFIG_FILE=/config/gateway.json
COPY --from=builder /wheels /wheels
COPY --from=builder /build/opa /usr/local/bin/opa
RUN mkdir -p /app/scratch && chmod 755 /usr/local/bin/opa && \
    python -m pip install --no-index --find-links /wheels threadlight-govern-gateway==0.1.0 && \
    python -m pip check && rm -rf /wheels && chown -R 10001:10001 /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=6s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]
ENTRYPOINT ["threadlight-govern-gateway"]
