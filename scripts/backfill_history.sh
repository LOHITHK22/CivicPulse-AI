#!/usr/bin/env bash
# Bounded, resumable historical backfill in half-year chunks (M2). Each chunk is its own run with
# its own [start, end) filter; re-running resumes paused/failed chunks and skips succeeded ones.
set -euo pipefail
PY=${PY:-.venv/Scripts/python}
CHUNKS=("2023-01-01 2023-07-01" "2023-07-01 2024-01-01" "2024-01-01 2024-07-01"
        "2024-07-01 2025-01-01" "2025-01-01 2025-07-01" "2025-07-01 2025-11-01")
for c in "${CHUNKS[@]}"; do
  set -- $c
  echo "=== chunk $1 -> $2 ==="
  "$PY" -m civicpulse_ingestion.cli extract --mode backfill --start "$1" --end "$2" --skip-if-succeeded
done
