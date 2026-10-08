#!/usr/bin/env bash
# Checks that a machine is ready BEFORE the long runs. Writes runs/_preflight_<role>_<ts>/report.txt
#
#   bash deploy/preflight.sh                                   # ROLE=local: one machine does everything
#   ROLE=server bash deploy/preflight.sh                       # two machines: on the server
#   ROLE=client SERVER=http://<ip>:9000 AGING_AGENT_TOKEN=<t> bash deploy/preflight.sh   # on the client
#
# local  : environment + functional tests + 60 s local smoke run            (~3 min)
# server : environment + functional tests (the agent must NOT be running yet) (~1 min)
# client : environment + agent reachability, auth, bundle match, clock, RTT + 60 s remote smoke run
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY=.venv/bin/python
ROLE="${ROLE:-local}"
case "$ROLE" in
  local)  APP_CPUS="${APP_CPUS-0}"; LOAD_CPUS="${LOAD_CPUS-1-3}" ;;
  client) LOAD_CPUS="${LOAD_CPUS-}" ;;
  server) ;;
  *) echo "ROLE must be local, server or client"; exit 2 ;;
esac
OUT="runs/_preflight_${ROLE}_$(date +%Y%m%d-%H%M%S)"; mkdir -p "$OUT"
FAIL=0; WARN=0
ok()   { echo "  [OK]   $*" | tee -a "$OUT/report.txt"; }
warn() { echo "  [WARN] $*" | tee -a "$OUT/report.txt"; WARN=$((WARN+1)); }
bad()  { echo "  [FAIL] $*" | tee -a "$OUT/report.txt"; FAIL=$((FAIL+1)); }
port_free() { $PY -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.1',$1))==0)"; }

echo "== Environment (role: $ROLE, host: $(hostname))" | tee "$OUT/report.txt"
[[ -x $PY ]] && ok "venv present" || { bad "run deploy/setup.sh first"; exit 1; }
ok "bundle $(cat BUNDLE_VERSION 2>/dev/null || echo 'dev tree')"
N=$(nproc)
if [[ $ROLE == local ]]; then
  [[ $N -ge 4 ]] && ok "$N CPUs" || warn "$N CPUs (<4): run with APP_CPUS= LOAD_CPUS= (no pinning)"
  command -v taskset >/dev/null && ok "taskset available" || warn "taskset missing (util-linux): CPU pinning disabled"
else
  [[ $N -ge 2 ]] && ok "$N CPUs" || warn "only $N CPU"
fi
PORTS="8000 3000"; [[ $ROLE == server ]] && PORTS="8000 3000 ${AGENT_PORT:-9000}"; [[ $ROLE == client ]] && PORTS=""
for port in $PORTS; do port_free "$port" && ok "port $port free" || bad "port $port in use"; done
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
if command -v timedatectl >/dev/null && timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
  ok "clock NTP-synchronized"
else
  [[ $ROLE == local ]] && ok "clock sync not required (single machine)" \
    || warn "clock not NTP-synchronized (offset is measured per run anyway)"
fi

if [[ $ROLE != client ]]; then
  echo "== Functional tests" | tee -a "$OUT/report.txt"
  T=reference/tests/test_uptime.py
  $PY harness/functional_check.py --app apps/uptime-fastapi/original --test $T > "$OUT/fc_original.log" 2>&1 \
    && ok "original app: PASS" || bad "original app: FAIL (see $OUT/fc_original.log)"
  $PY harness/functional_check.py --app apps/uptime-fastapi/instrumented --test $T > "$OUT/fc_instr_off.log" 2>&1 \
    && ok "instrumented app, probes off: PASS" || bad "instrumented app, probes off: FAIL"
  $PY harness/functional_check.py --app apps/uptime-fastapi/instrumented --test $T --instrument > "$OUT/fc_instr_on.log" 2>&1 \
    && ok "instrumented app, probes on: PASS" || bad "instrumented app, probes on: FAIL"
fi

if [[ $ROLE == client ]]; then
  echo "== Server agent" | tee -a "$OUT/report.txt"
  if [[ -z "${SERVER:-}" || -z "${AGING_AGENT_TOKEN:-}" ]]; then
    bad "set SERVER=http://<server-ip>:9000 and AGING_AGENT_TOKEN (printed by deploy/server_agent.sh)"
  else
    $PY - "$SERVER" <<'PYEOF' | tee -a "$OUT/report.txt"
import os, sys
sys.path.insert(0, "harness")
from pathlib import Path
from run_remote import AgentClient
agent = AgentClient(sys.argv[1], os.environ["AGING_AGENT_TOKEN"])
try:
    st = agent.call("GET", "/status", timeout=5)
except Exception as e:
    print(f"  [FAIL] agent not reachable/authorized at {sys.argv[1]}: {e}"); sys.exit()
print(f"  [OK]   agent reachable, token accepted (server root {st['root']})")
local = Path("BUNDLE_VERSION").read_text().strip() if Path("BUNDLE_VERSION").exists() else "dev tree"
print(f"  [OK]   same bundle on both machines ({local})" if st["bundle"] == local else
      f"  [FAIL] bundle mismatch: client {local} vs server {st['bundle']} (copy the same bundle to both)")
print("  [OK]   no active run on the server" if not st["active"] else f"  [FAIL] server busy with {st['active']}")
c = agent.clock_offset()
print(f"  [OK]   clock offset {c['offset_s']*1000:.1f} ms, min RTT {c['rtt_ms']:.2f} ms")
if c["rtt_ms"] > 2:
    print(f"  [WARN] RTT {c['rtt_ms']:.1f} ms is high for a wired LAN (Wi-Fi? other hops?)")
PYEOF
    FAIL=$((FAIL + $(grep -c "\[FAIL\]" "$OUT/report.txt") - FAIL))
    WARN=$((WARN + $(grep -c "\[WARN\]" "$OUT/report.txt") - WARN))
  fi
fi

if [[ $ROLE != server && $FAIL -eq 0 ]]; then
  echo "== 60 s smoke run (targeted workload, instrumented app, $ROLE)" | tee -a "$OUT/report.txt"
  if [[ $ROLE == local ]]; then
    $PY harness/run_diagnostic.py --app apps/uptime-fastapi/instrumented --instrument \
      --workload workloads/uptime-fastapi-F4_targeted_v1.py --duration 60 --label _preflight_smoke \
      --app-cpus "$APP_CPUS" --load-cpus "$LOAD_CPUS" > "$OUT/smoke.log" 2>&1
  else
    $PY harness/run_remote.py --server "$SERVER" --app apps/uptime-fastapi/instrumented --instrument \
      --workload workloads/uptime-fastapi-F4_targeted_v1.py --duration 60 --label _preflight_smoke \
      ${LOAD_CPUS:+--load-cpus "$LOAD_CPUS"} > "$OUT/smoke.log" 2>&1
  fi
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
lat = c[c.endpoint == "heartbeat"].latency_ms.median()
print(f"  requests={len(c)} error_rate={err:.4f} rows.total(final)={last} heartbeat p50={lat:.1f} ms")
print("  [OK]   smoke run healthy" if err < 0.01 and last > 300 else "  [FAIL] smoke run unhealthy")
PYEOF
    grep -q "FAIL\] smoke" "$OUT/report.txt" && FAIL=$((FAIL+1))
    mv "$SMOKE" "$OUT/smoke_run"
  else
    bad "smoke run produced no data (see $OUT/smoke.log)"
  fi
fi

echo "== Result: $FAIL failure(s), $WARN warning(s)" | tee -a "$OUT/report.txt"
if [[ $FAIL -eq 0 ]]; then
  case "$ROLE" in
    local)  echo "Ready. Next: bash deploy/campaign_uptime_F4.sh (inside tmux or with nohup)";;
    server) echo "Ready. Next: tmux new -s agent 'bash deploy/server_agent.sh'";;
    client) echo "Ready. Next: bash deploy/campaign_uptime_F4.sh (with SERVER and AGING_AGENT_TOKEN set, inside tmux)";;
  esac
fi
exit $FAIL
