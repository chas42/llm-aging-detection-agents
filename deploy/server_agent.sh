#!/usr/bin/env bash
# Starts the control agent on the SERVER machine (two-machine mode). Run it inside tmux:
#   tmux new -s agent 'bash deploy/server_agent.sh'
# Environment: AGENT_PORT [9000], BIND [0.0.0.0], APP_CPUS [empty = no pinning on a dedicated server]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
AGENT_PORT="${AGENT_PORT:-9000}"; BIND="${BIND:-0.0.0.0}"; APP_CPUS="${APP_CPUS:-}"

[[ -x .venv/bin/python ]] || { echo "run deploy/setup.sh first"; exit 1; }
if [[ ! -s .agent_token ]]; then
  ( umask 077; .venv/bin/python -c "import secrets; print(secrets.token_urlsafe(24))" > .agent_token )
  echo "[agent] new token generated in .agent_token"
fi
TOKEN="$(cat .agent_token)"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

if command -v ufw >/dev/null && sudo -n ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "[agent] ufw is active: make sure the client can reach ports $AGENT_PORT and 8000, e.g."
  echo "        sudo ufw allow from <CLIENT_IP> to any port $AGENT_PORT,8000 proto tcp"
fi

cat <<MSG
============================================================================
 Server agent: http://$IP:$AGENT_PORT   (bundle $(cat BUNDLE_VERSION 2>/dev/null || echo 'dev tree'))
 On the CLIENT machine, inside the same bundle version, run:

   export SERVER=http://$IP:$AGENT_PORT
   export AGING_AGENT_TOKEN=$TOKEN
   ROLE=client bash deploy/preflight.sh
   bash deploy/campaign_uptime_F4.sh
============================================================================
MSG
exec .venv/bin/python harness/server_agent.py --token "$TOKEN" --bind "$BIND" --port "$AGENT_PORT" \
     ${APP_CPUS:+--app-cpus "$APP_CPUS"}
