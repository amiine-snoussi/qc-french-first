#!/usr/bin/env bash
# start_api.sh — Entry point for the FastAPI server (Railway/Vercel)
set -euo pipefail

cd "$(dirname "$0")"

# Use virtualenv if it exists
if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

# Default port from Railway env var
PORT="${PORT:-8000}"

# Start the API server (no reload in production)
exec uvicorn api:app --host 0.0.0.0 --port "$PORT" --workers 1