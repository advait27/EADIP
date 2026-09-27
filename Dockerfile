# syntax=docker/dockerfile:1.7
# Multi-stage, non-root, uv-based build (TAD §15.1) + the Glass Box UI.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# vite.config.ts writes to ../src/eadip/gateway/static/app -> /src/... here.
RUN mkdir -p /src/eadip/gateway/static && npm run build

FROM python:3.13-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY uv.lock* ./
COPY src/ ./src/
COPY --from=web /src/eadip/gateway/static/app ./src/eadip/gateway/static/app
# Use the lockfile when present; fall back to a resolve for the initial scaffold.
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

FROM python:3.13-slim AS runtime
RUN useradd -u 10001 -m app
WORKDIR /app
COPY --from=build /app /app
USER app
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "eadip.gateway:app", "--host", "0.0.0.0", "--port", "8000"]
