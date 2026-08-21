# syntax=docker/dockerfile:1.19
#
# KubeThrifty analyser. Multi-stage, non-root, digest-free but tag-exact: never `:latest`, anywhere.
# CI overrides the app tag with the commit SHA; the base image tag is pinned here and bumped by
# Renovate so a rebuild can never silently pick up a different interpreter.
#
# BUILD CONTEXT IS THE REPO ROOT, because the pinned instance catalogue lives at config/:
#     docker build -f analyser/Dockerfile -t kubethrifty-analyser:$(git rev-parse --short HEAD) .

# ---------- builder: compile wheels so the runtime image needs no toolchain ----------------------
FROM python:3.14.6-slim-trixie AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Copy only the dependency manifests first: the layer cache then survives every source edit.
COPY analyser/requirements.txt analyser/requirements.lock ./

# Install from the LOCKFILE, not requirements.txt -- the whole resolved graph, transitives included.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.lock

# ---------- runtime -----------------------------------------------------------------------------
FROM python:3.14.6-slim-trixie AS runtime

LABEL org.opencontainers.image.title="kubethrifty-analyser" \
      org.opencontainers.image.description="Kubernetes pod right-sizing advisor: sizing, forecasting, rehearsal, packing, ThriftDetective" \
      org.opencontainers.image.source="https://github.com/kubethrifty/kubethrifty" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0

# PYTHONHASHSEED=0 is load-bearing, not hygiene: ThriftDetective's verdict digests are asserted
# byte-for-byte in CI, and an unpinned hash seed is a real source of iteration-order drift.

# UID 65532 == `nonroot` in distroless convention. Numeric, so a PodSecurityContext with
# runAsNonRoot can verify it without resolving /etc/passwd.
RUN groupadd --gid 65532 nonroot \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin nonroot

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=65532:65532 analyser/src/ ./src/
COPY --chown=65532:65532 config/ ./config/

USER 65532:65532

# No shell form: exec form means signals reach Python, so a SIGTERM during an analysis run unwinds
# the rehearsal saga's `finally` block instead of being swallowed by /bin/sh.
ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--window", "7d"]
