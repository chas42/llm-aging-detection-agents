#!/usr/bin/env bash
# Multi-app diagnostic campaign (see deploy/campaign.py for the options, all via environment variables).
#   APPS="uptime-fastapi uptime-actix" RUNS="R0 R0b R1" bash deploy/campaign.sh
# Run it inside tmux / nohup: with 8 h runs a campaign takes days.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Apps under an environment-triggered stall hold FDs/threads for the whole run (Monitor R1 reaches
# ~1,700 FDs in 8 h): raise the soft limits to the hard ones (no root needed); recorded per run.
ulimit -n "$(ulimit -Hn)" 2>/dev/null || true
ulimit -u "$(ulimit -Hu)" 2>/dev/null || true
exec .venv/bin/python deploy/campaign.py
