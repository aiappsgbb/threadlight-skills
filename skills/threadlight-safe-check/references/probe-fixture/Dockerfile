# Build the existing gateway image first and supply its immutable digest.
ARG GATEWAY_IMAGE
FROM --platform=linux/amd64 python:3.12-slim AS builder
WORKDIR /build
ENV TMPDIR=/build/scratch PIP_DISABLE_PIP_VERSION_CHECK=1
RUN mkdir -p /build/scratch /build/wheels
COPY skills/threadlight-safe-check/references/probe-fixture/*.py skills/threadlight-safe-check/references/probe-fixture/pyproject.toml /build/fixture/
RUN python -m pip wheel --no-deps --wheel-dir /build/wheels /build/fixture

FROM ${GATEWAY_IMAGE}
USER root
COPY --from=builder /build/wheels /app/probe-wheels
RUN python -m pip install --no-deps /app/probe-wheels/*.whl && python -m pip check && rm -rf /app/probe-wheels
USER 10001:10001
HEALTHCHECK NONE
ENTRYPOINT ["threadlight-govern-probe-fixture"]
