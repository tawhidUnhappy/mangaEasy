FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MANGAEASY_ROOT=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git git-lfs ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app
COPY . /app
RUN uv sync --frozen --no-dev

VOLUME ["/data"]
ENTRYPOINT ["/app/.venv/bin/mediaconductor"]
CMD ["mcp", "--allow-root", "/data"]
