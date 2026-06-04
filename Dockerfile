# syntax=docker/dockerfile:1.7
# SkuldBot Runner service image.
#
# Composes wheels from three sources via offline install (--no-index):
#   - ghcr.io/skuldllc/skuldbot-compiler  (Python wheel of skuldbot-compiler-py)
#   - ghcr.io/skuldllc/skuldbot-executor  (Python wheel of skuldbot-executor)
#   - visual-dependency-wheels            (image recognition deps for RPA.Desktop)
#   - local build                         (skuldbot-runner itself)
#
# Build-time args let CI pin exact upstream versions; :latest for local dev.

ARG COMPILER_VERSION=latest
ARG EXECUTOR_VERSION=latest

# ---- Pull upstream wheels ----
FROM ghcr.io/skuldllc/skuldbot-compiler:${COMPILER_VERSION} AS compiler-wheels
FROM ghcr.io/skuldllc/skuldbot-executor:${EXECUTOR_VERSION} AS executor-wheels

# ---- Build local wheel ----
FROM python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c AS runner-builder

WORKDIR /build
COPY pyproject.toml README.md /build/
COPY requirements.build.lock /build/
COPY src/ /build/src/

# K-05 reproducible wheel build controls.
ENV SOURCE_DATE_EPOCH=1704067200 \
    PYTHONHASHSEED=0 \
    CFLAGS="-O2 -g0 -fdebug-prefix-map=/build=. -ffile-prefix-map=/build=."

RUN pip install --no-cache-dir --require-hashes -r /build/requirements.build.lock \
 && pip wheel --no-deps --no-build-isolation --wheel-dir /dist . \
 && if command -v strip >/dev/null 2>&1; then \
      find /dist -name "*.so" -type f -exec strip --strip-unneeded {} \;; \
    fi

# ---- Build visual dependency wheels ----
FROM python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c AS visual-dependency-wheels

RUN pip wheel --no-cache-dir --wheel-dir /wheels "rpaframework-recognition>=5.0.0"

# ---- Runtime ----
FROM python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c AS runtime

LABEL org.opencontainers.image.vendor="Skuld, LLC"
LABEL org.opencontainers.image.source="https://github.com/skuldllc/skuldbot-runner"
LABEL org.opencontainers.image.url="https://skuldbot.com"
LABEL org.opencontainers.image.licenses="UNLICENSED"
LABEL org.opencontainers.image.title="SkuldBot Runner"
LABEL org.opencontainers.image.description="Agent that claims bot jobs from the Orchestrator and delegates execution to skuldbot-executor."

# System deps required by the linux_virtual_display graphical plane.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      libgl1 \
      libglib2.0-0 \
      libgtk-3-0 \
      libsm6 \
      libxext6 \
      libxi6 \
      libxrandr2 \
      libxrender1 \
      libxss1 \
      libxtst6 \
      scrot \
      x11-utils \
      xauth \
      xvfb \
 && rm -rf /var/lib/apt/lists/*

COPY --from=compiler-wheels /wheels /wheels-compiler
COPY --from=executor-wheels /wheels /wheels-executor
COPY --from=visual-dependency-wheels /wheels /wheels-visual
COPY --from=runner-builder  /dist    /wheels-runner

# Offline install: prefer local wheels, reject any public PyPI fetch.
RUN pip install --no-cache-dir --no-index \
    --find-links=/wheels-compiler \
    --find-links=/wheels-executor \
    --find-links=/wheels-visual \
    --find-links=/wheels-runner \
    skuldbot-runner \
 && python -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('RPA.recognition') else 1)" \
 && rm -rf /wheels-compiler /wheels-executor /wheels-visual /wheels-runner

# Non-root runtime user
RUN useradd --create-home --shell /bin/bash --uid 1000 skuldbot
USER skuldbot
WORKDIR /home/skuldbot

ENTRYPOINT ["skuldbot-runner"]
