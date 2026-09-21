# syntax=docker/dockerfile:1

# ---- build ------------------------------------------------------------------
# argon2-cffi and lxml need a compiler; keeping that in a discarded stage keeps
# the toolchain out of the published image.
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# ---- runtime ----------------------------------------------------------------
FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends curl gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin canvas

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8000 \
    DB_PATH=/data/auth.sqlite \
    CANVAS_TOKEN_FILE=/run/secrets/canvas_token

# OAuth state persists here. Without a volume mounted on it, every redeploy
# silently deauthorizes the connector.
RUN mkdir -p /data && chown canvas:canvas /data
VOLUME ["/data"]

# Deliberately no USER: the entrypoint needs root to take ownership of a
# bind-mounted volume, which arrives owned by root however the image was
# built, and then drops to `canvas` before exec'ing the server. Nothing
# application-level ever runs as root.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

# Probes liveness only; see the health route for why it does not touch Canvas.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["canvas-viewer-mcp"]
