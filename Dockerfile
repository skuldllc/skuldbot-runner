# syntax=docker/dockerfile:1.7
# SkuldBot Runner service image.
#
# Composes wheels from three sources via offline install (--no-index):
#   - ghcr.io/skuldllc/skuldbot-compiler  (Python wheel of skuldbot-compiler-py)
#   - ghcr.io/skuldllc/skuldbot-executor  (Python wheel of skuldbot-executor)
#   - local build                         (skuldbot-runner itself)
#
# Build-time args let CI pin exact upstream versions; :latest for local dev.

ARG COMPILER_VERSION=latest
ARG EXECUTOR_VERSION=latest

# ---- Pull upstream wheels ----
FROM ghcr.io/skuldllc/skuldbot-compiler:${COMPILER_VERSION} AS compiler-wheels
FROM ghcr.io/skuldllc/skuldbot-executor:${EXECUTOR_VERSION} AS executor-wheels

# ---- Build local wheel ----
FROM python:3.12-slim AS runner-builder

WORKDIR /build
COPY pyproject.toml README.md /build/
COPY src/ /build/src/

RUN pip install --no-cache-dir --upgrade pip wheel setuptools \
 && pip wheel --no-deps --wheel-dir /dist .

# ---- Runtime ----
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.vendor="Skuld, LLC"
LABEL org.opencontainers.image.source="https://github.com/skuldllc/skuldbot-runner"
LABEL org.opencontainers.image.url="https://skuldbot.com"
LABEL org.opencontainers.image.licenses="UNLICENSED"
LABEL org.opencontainers.image.title="SkuldBot Runner"
LABEL org.opencontainers.image.description="Agent that claims bot jobs from the Orchestrator and delegates execution to skuldbot-executor."

# System deps (Robot Framework + RPA libs often need build essentials; add as
# needed per telemetry. Keep minimal for now.)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=compiler-wheels /wheels /wheels-compiler
COPY --from=executor-wheels /wheels /wheels-executor
COPY --from=runner-builder  /dist    /wheels-runner

# Offline install: prefer local wheels, reject any public PyPI fetch.
RUN pip install --no-cache-dir --no-index \
    --find-links=/wheels-compiler \
    --find-links=/wheels-executor \
    --find-links=/wheels-runner \
    skuldbot-runner \
 && rm -rf /wheels-compiler /wheels-executor /wheels-runner

# Non-root runtime user
RUN useradd --create-home --shell /bin/bash --uid 1000 skuldbot
USER skuldbot
WORKDIR /home/skuldbot

ENTRYPOINT ["skuldbot-runner"]
