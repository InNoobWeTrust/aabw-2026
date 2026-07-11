#!/usr/bin/env bash
set -euo pipefail

# ─── 1. Create .env from .env.example if missing ──────────────────────────────

if [[ ! -f .env ]]; then
    echo "No .env found. Creating from .env.example…"
    cp .env.example .env
    echo ".env created — edit passwords/secrets before production use."
else
    echo ".env already exists."
fi

# ─── 2. Sync dependencies if needed ───────────────────────────────────────────

if [[ ! -f uv.lock ]] || ! uv run python -c "import fastapi" &>/dev/null 2>&1; then
    echo "Installing/syncing dependencies…"
    uv sync --extra dev
else
    echo "Dependencies appear to be installed."
fi

# ─── 3. Start server ──────────────────────────────────────────────────────────

echo "Starting RoboData server…"
exec uv run uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload
