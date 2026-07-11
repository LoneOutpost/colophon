# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
# Dependency layer first so it caches unless the lockfile changes. README.md is
# referenced by pyproject's `readme` field, so it's needed to build the project.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev
# Then the project itself.
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg gosu tzdata \
    && rm -rf /var/lib/apt/lists/*
# Non-root runtime user; the entrypoint retargets these ids to PUID/PGID.
RUN groupadd -g 1000 app \
    && useradd -u 1000 -g app -d /config -s /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app /app
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" \
    XDG_CONFIG_HOME=/config \
    XDG_DATA_HOME=/config \
    PUID=1000 \
    PGID=1000
VOLUME ["/config"]
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
