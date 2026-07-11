FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
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

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
