#!/usr/bin/env bash
# Identification batch: 3 isolated AI reviews + triage per app, one session at a time.
#   bash identification/run_batch.sh <app-id> [<app-id> ...]
# Skips repetitions/triage already done (re-runnable after an interruption).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
for app in "$@"; do
  for r in 1 2 3; do
    if [[ -f identification/$app/ai_review_r$r.md ]]; then echo "[skip] $app r$r"; continue; fi
    .venv/bin/python identification/run_ai_review.py --app "$app" --rep "$r" || echo "[FAIL] $app r$r"
  done
  if [[ -d identification/$app/triage ]]; then echo "[skip] $app triage"; continue; fi
  .venv/bin/python identification/run_triage.py --app "$app" || echo "[FAIL] $app triage"
done
echo "[batch] done"
