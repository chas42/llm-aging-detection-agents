#!/usr/bin/env bash
# Diagnostic campaign for finding uptime-fastapi-F4 (Phase 3 of docs/PLANO.md).
# Runs sequentially, each on a fresh database, the instrumented app:
#   R0   control, faithful to the original JMeter plan (4 fixed services, no think time)
#   R0b  matched control: targeted workload with --key-space 4 (same load, no accumulation)
#   R1   targeted: a new serviceId every 1/10 s (the finding's trigger)
# then analyzes each run and packs everything with deploy/collect_results.sh.
#
# Configuration via environment variables (defaults in brackets):
#   DURATION [3600]  seconds per run        RUNS ["R0 R0b R1"]   subset/order of runs
#   APP_CPUS [0]     server CPU list        LOAD_CPUS [1-3]      workload CPU list
#   SEED [42]        workload seed          COOLDOWN [120]       idle seconds between runs
#
# Run it detached so it survives an SSH disconnect, e.g.:
#   tmux new -s aging 'bash deploy/campaign_uptime_F4.sh'
#   nohup bash deploy/campaign_uptime_F4.sh > campaign.out 2>&1 &
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY=.venv/bin/python
DURATION="${DURATION:-3600}"; RUNS="${RUNS:-R0 R0b R1}"; SEED="${SEED:-42}"
APP_CPUS="${APP_CPUS-0}"; LOAD_CPUS="${LOAD_CPUS-1-3}"; COOLDOWN="${COOLDOWN:-120}"
TARGETED=workloads/uptime-fastapi-F4_targeted_v1.py
CONTROL=workloads/uptime-fastapi-F4_control.py
APP=apps/uptime-fastapi/instrumented

CAMP="runs/_campaign_$(date +%Y%m%d-%H%M%S)"
mkdir -p "$CAMP"
LOG="$CAMP/campaign.log"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# System description for the paper's experimental-setup section
{
  echo "## date";      date -Is
  echo "## bundle";    cat BUNDLE_VERSION 2>/dev/null || echo "(dev tree)"
  echo "## uname";     uname -a
  echo "## os";        cat /etc/os-release 2>/dev/null | head -4
  echo "## cpu";       lscpu 2>/dev/null | grep -E 'Model name|^CPU\(s\)|Thread|MHz' 
  echo "## memory";    free -h
  echo "## disk";      df -h . ; findmnt -T . -o SOURCE,FSTYPE,OPTIONS 2>/dev/null
  echo "## python";    $PY -V
  echo "## packages";  $PY -m pip freeze
  echo "## config";    echo "DURATION=$DURATION RUNS='$RUNS' SEED=$SEED APP_CPUS=$APP_CPUS LOAD_CPUS=$LOAD_CPUS COOLDOWN=$COOLDOWN"
} > "$CAMP/system.txt" 2>&1

run() {  # run <label> <workload> [workload args...]
  local label="$1" workload="$2"; shift 2
  log "START $label ($workload $*) for ${DURATION}s"
  $PY harness/run_diagnostic.py --app "$APP" --instrument --workload "$workload" \
      --duration "$DURATION" --seed "$SEED" --label "$label" \
      --app-cpus "$APP_CPUS" --load-cpus "$LOAD_CPUS" -- "$@" >> "$LOG" 2>&1
  local rc=$?
  local dir; dir=$(ls -d runs/*_"$label" 2>/dev/null | tail -1)
  log "END   $label exit=$rc dir=$dir"
  if [[ -n "$dir" ]]; then
    echo "$dir" >> "$CAMP/runs.txt"
    $PY harness/analyze_run.py "$dir" >> "$LOG" 2>&1 && log "      analysis -> $dir/analysis/summary.md"
  fi
}

TOTAL=$(( $(wc -w <<< "$RUNS") * (DURATION + COOLDOWN) / 60 ))
log "campaign $CAMP: runs='$RUNS', ~${TOTAL} min total"
first=1
for r in $RUNS; do
  [[ $first -eq 0 ]] && { log "cooldown ${COOLDOWN}s"; sleep "$COOLDOWN"; }
  first=0
  case "$r" in
    R0)  run R0_F4_control_jmx      "$CONTROL" ;;
    R0b) run R0b_F4_matched_control "$TARGETED" --key-space 4 ;;
    R1)  run R1_F4_targeted_v1      "$TARGETED" ;;
    *)   log "unknown run '$r' (valid: R0 R0b R1)";;
  esac
done

log "campaign finished; packing results"
bash deploy/collect_results.sh "$CAMP" | tee -a "$LOG"
