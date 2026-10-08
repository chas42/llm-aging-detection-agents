#!/usr/bin/env bash
# Checks that the isolated machine is ready and that the apps/workloads work, BEFORE the long runs.
# Takes ~3 minutes. Writes runs/_preflight_<timestamp>/report.txt
#   bash deploy/preflight.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY=.venv/bin/python
APP_CPUS="${APP_CPUS:-0}"; LOAD_CPUS="${LOAD_CPUS:-1-3}"
OUT="runs/_preflight_$(date +%Y%m%d-%H%M%S)"; mkdir -p "$OUT"
FAIL=0; WARN=0
ok()   { echo "  [OK]   $*" | tee -a "$OUT/report.txt"; }
warn() { echo "  [WARN] $*" | tee -a "$OUT/report.txt"; WARN=$((WARN+1)); }
bad()  { echo "  [FAIL] $*" | tee -a "$OUT/report.txt"; FAIL=$((FAIL+1)); }

echo "== Environment" | tee "$OUT/report.txt"
[[ -x $PY ]] && ok "venv present" || { bad "run deploy/setup.sh first"; exit 1; }
N=$(nproc); [[ $N -ge 4 ]] && ok "$N CPUs" || warn "$N CPUs (<4): run with APP_CPUS= LOAD_CPUS= (no pinning)"
command -v taskset >/dev/null && ok "taskset available" || warn "taskset missing (util-linux): CPU pinning disabled"
for port in 8000 3000; do
  if $PY -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.1',$port))==0)"; then ok "port $port free"
  else bad "port $port in use"; fi
done
FREE_GB=$(df -Pk . | awk 'NR==2{print int($4/1024/1024)}')
[[ $FREE_GB -ge 2 ]] && ok "${FREE_GB} GB free disk" || bad "only ${FREE_GB} GB free disk (need >= 2)"
MEM_GB=$(awk '/MemAvailable/{print int($2/1024/1024)}' /proc/meminfo)
[[ $MEM_GB -ge 2 ]] && ok "${MEM_GB} GB RAM available" || warn "only ${MEM_GB} GB RAM available"
LOAD=$(cut -d' ' -f1 /proc/loadavg)
$PY -c "import sys; sys.exit(float('$LOAD') > 0.5*$N)" && ok "load average $LOAD" \
  || warn "load average $LOAD is high: stop other workloads for clean measurements"
grep -qi microsoft /proc/version && warn "running under WSL: prefer a native Linux machine/VM"
GOV=/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
[[ -r $GOV ]] && { [[ $(cat $GOV) == performance ]] && ok "CPU governor: performance" \
  || warn "CPU governor: $(cat $GOV) (consider 'performance' for stable latency)"; }
command -v timedatectl >/dev/null && timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes \
  && ok "clock NTP-synchronized" || warn "clock sync unknown (only matters for cross-machine timestamps)"

echo "== Functional tests" | tee -a "$OUT/report.txt"
T=reference/tests/test_uptime.py
$PY harness/functional_check.py --app apps/uptime-fastapi/original --test $T > "$OUT/fc_original.log" 2>&1 \
  && ok "original app: PASS" || bad "original app: FAIL (see $OUT/fc_original.log)"
$PY harness/functional_check.py --app apps/uptime-fastapi/instrumented --test $T > "$OUT/fc_instr_off.log" 2>&1 \
  && ok "instrumented app, probes off: PASS" || bad "instrumented app, probes off: FAIL"
$PY harness/functional_check.py --app apps/uptime-fastapi/instrumented --test $T --instrument > "$OUT/fc_instr_on.log" 2>&1 \
  && ok "instrumented app, probes on: PASS" || bad "instrumented app, probes on: FAIL"

echo "== 60 s smoke run (targeted workload, instrumented app)" | tee -a "$OUT/report.txt"
$PY harness/run_diagnostic.py --app apps/uptime-fastapi/instrumented --instrument \
  --workload workloads/uptime-fastapi-F4_targeted_v1.py --duration 60 --label _preflight_smoke \
  --app-cpus "$APP_CPUS" --load-cpus "$LOAD_CPUS" > "$OUT/smoke.log" 2>&1
SMOKE=$(ls -d runs/*__preflight_smoke 2>/dev/null | tail -1)
if [[ -n "$SMOKE" && -s "$SMOKE/client.csv" ]]; then
  $PY - "$SMOKE" <<'PYEOF' | tee -a "$OUT/report.txt"
import json, sys, pandas as pd
run = sys.argv[1]
c = pd.read_csv(f"{run}/client.csv")
err = (~c.status.between(200, 399)).mean()
rows = [json.loads(l) for l in open(f"{run}/instrumentation.jsonl")]
snap = [r for r in rows if r.get("kind") == "snapshot" and "rows.total" in r.get("metrics", {})]
last = snap[-1]["metrics"]["rows.total"] if snap else 0
print(f"  requests={len(c)} error_rate={err:.4f} rows.total(final)={last}")
print("  [OK]   smoke run healthy" if err < 0.01 and last > 300 else "  [FAIL] smoke run unhealthy")
PYEOF
  grep -q "FAIL\] smoke" "$OUT/report.txt" && FAIL=$((FAIL+1))
  mv "$SMOKE" "$OUT/smoke_run"
else
  bad "smoke run produced no data (see $OUT/smoke.log)"
fi

echo "== Result: $FAIL failure(s), $WARN warning(s)" | tee -a "$OUT/report.txt"
[[ $FAIL -eq 0 ]] && echo "Ready. Next: bash deploy/campaign_uptime_F4.sh (inside tmux or with nohup)"
exit $FAIL
