#!/usr/bin/env bash
# Prepares the bundle on the isolated test machine: integrity check, venv, dependencies.
#   cd aging-validation-bundle-<date> && bash deploy/setup.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"

echo "[setup] verifying file integrity (MANIFEST.sha256) ..."
sha256sum --quiet -c MANIFEST.sha256 && echo "[setup] integrity OK"

"$PY" - <<'PYEOF' || { echo "[setup] Python >= 3.10 is required (set PYTHON=/path/to/python3.x)"; exit 1; }
import sys; assert sys.version_info >= (3, 10), sys.version
print(f"[setup] python {sys.version.split()[0]}")
PYEOF

if [[ ! -x .venv/bin/python ]]; then
  if ! "$PY" -m venv .venv 2>/dev/null; then
    rm -rf .venv
    echo "[setup] 'venv' module incomplete (Ubuntu: sudo apt install python3-venv). Trying --without-pip ..."
    "$PY" -m venv --without-pip .venv
    if [[ -d wheelhouse ]]; then
      echo "[setup] offline: bootstrapping pip from the wheelhouse"
      .venv/bin/python "$(ls wheelhouse/pip-*.whl)/pip" install -q --no-index --find-links wheelhouse pip
    else
      curl -sSfL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python - -q
    fi
  fi
fi

PIP=(.venv/bin/python -m pip install -q)
[[ -d wheelhouse ]] && PIP+=(--no-index --find-links wheelhouse)
"${PIP[@]}" -r requirements.txt -r apps/uptime-fastapi/original/requirements.txt

.venv/bin/python -c "import fastapi, uvicorn, httpx, psutil, pymannkendall, pandas, matplotlib; print('[setup] imports OK')"
echo "[setup] done. Next: bash deploy/preflight.sh"
