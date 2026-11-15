FROM ghcr.io/astral-sh/uv:0.11.22 AS uv
FROM python:3.14-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY vendor/RenderEngine vendor/RenderEngine
COPY backend backend
RUN uv sync --locked --no-dev
COPY frontend frontend
CMD ["sh", "-c", "exec .venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port \"${PORT:-10000}\""]
