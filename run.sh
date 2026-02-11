#!/usr/bin/env bash
set -euo pipefail
if [ $# -lt 1 ]; then
  echo "Usage: ./run.sh https://example.com [max_pages]"
  exit 1
fi
URL="$1"
MAX_PAGES="${2:-}"
cd "$(dirname "$0")"
source .venv/bin/activate
if [ -n "$MAX_PAGES" ]; then
  python main.py --url "$URL" --max_pages "$MAX_PAGES"
else
  python main.py --url "$URL"
fi
