#!/usr/bin/env bash
# Packs the evidence of a campaign (or all runs) for transfer back to the development machine.
#   bash deploy/collect_results.sh                       # every run under runs/
#   bash deploy/collect_results.sh runs/_campaign_<ts>   # only the runs of that campaign
# Output: results/aging-results-<host>-<timestamp>.tar.gz (+ .sha256)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p results
OUT="results/aging-results-$(hostname -s)-$(date +%Y%m%d-%H%M%S).tar.gz"

if [[ -n "${1:-}" ]]; then
  CAMP="${1%/}"
  LIST=("$CAMP" $(cat "$CAMP/runs.txt" 2>/dev/null || true))
  LIST+=($(ls -d runs/_preflight_* 2>/dev/null | tail -1))
else
  LIST=(runs/*)
fi
# The final SQLite file is kept (it is evidence of the accumulated state); code copies are kept
# so each run is self-describing (meta.json has their hashes).
tar --exclude='__pycache__' -czf "$OUT" "${LIST[@]}"
sha256sum "$OUT" > "$OUT.sha256"
echo "[collect] $OUT ($(du -h "$OUT" | cut -f1)) with ${#LIST[@]} entries"
echo "[collect] copy BOTH files to the development machine, then:"
echo "          sha256sum -c $(basename "$OUT").sha256 && tar -xzf $(basename "$OUT") -C <aging-validation>/"
