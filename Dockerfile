FROM oven/bun:1.1-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/bun.lock* ./
RUN bun install
COPY frontend/ ./
RUN bun run build

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV DATA_DIR=/app/data

RUN pip install --no-cache-dir uv

WORKDIR /app

# uv.lock must be generated before docker build with: `uv lock`
# The lock file ensures reproducible production builds.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

COPY . .
COPY --from=frontend-builder /app/frontend/out ./frontend/out

RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uv", "run", "uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
