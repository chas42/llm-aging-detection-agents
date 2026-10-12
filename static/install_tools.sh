#!/usr/bin/env bash
# Installs the static-analysis tools used by the identification stage, pinned to the versions of the
# previous paper so results stay comparable. Everything goes under tools/ and .venv-tools/ (gitignored).
#   bash static/install_tools.sh
# Override versions with SEMGREP_VERSION / CODEQL_VERSION / SLOWQL_VERSION.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SEMGREP_VERSION="${SEMGREP_VERSION:-1.168.0}"   # paper: Semgrep OSS 1.168.0
CODEQL_VERSION="${CODEQL_VERSION:-2.25.4}"      # paper: CodeQL 2.25.4
SLOWQL_VERSION="${SLOWQL_VERSION:-}"            # paper version unknown -> latest, recorded in versions.txt

# 1. Python tools in their own venv (semgrep pins many dependencies)
if [[ ! -x .venv-tools/bin/python ]]; then
  python3 -m venv .venv-tools 2>/dev/null || {
    rm -rf .venv-tools; python3 -m venv --without-pip .venv-tools
    curl -sSfL https://bootstrap.pypa.io/get-pip.py | .venv-tools/bin/python - -q; }
fi
.venv-tools/bin/python -m pip install -q "semgrep==$SEMGREP_VERSION" "slowql${SLOWQL_VERSION:+==$SLOWQL_VERSION}"

# 2. CodeQL bundle (CLI + standard query packs) from the codeql-action releases
mkdir -p tools
if [[ ! -x tools/codeql/codeql ]] || ! tools/codeql/codeql version 2>/dev/null | grep -q "$CODEQL_VERSION"; then
  rm -rf tools/codeql
  URL="https://github.com/github/codeql-action/releases/download/codeql-bundle-v$CODEQL_VERSION/codeql-bundle-linux64.tar.gz"
  echo "[tools] downloading CodeQL bundle $CODEQL_VERSION (~1 GB unpacked) ..."
  curl -sSfL "$URL" | tar -xz -C tools
fi
# GitHub Security Lab community packs (the paper's "CodeQL-Community-Packs" suites)
COMMUNITY_PACKS_VERSION="${COMMUNITY_PACKS_VERSION:-0.2.1}"  # paper: 0.2.1 (latest needs a newer CLI)
for pack in githubsecuritylab/codeql-python-queries githubsecuritylab/codeql-javascript-queries; do
  tools/codeql/codeql pack download "$pack@$COMMUNITY_PACKS_VERSION" >/dev/null
done

# 3. LeakAudit: source still to be confirmed (not on PyPI); see docs/PLANO-identificacao.md

{
  echo "date: $(date -Is)"
  echo "semgrep: $(.venv-tools/bin/semgrep --version 2>/dev/null)"
  echo "slowql: $(.venv-tools/bin/python -m pip show slowql 2>/dev/null | awk '/^Version/{print $2}')"
  echo "codeql: $(tools/codeql/codeql version --format=terse)"
  echo "codeql packs:"; tools/codeql/codeql resolve packs 2>/dev/null | grep -E "githubsecuritylab|codeql/(python|javascript|rust)-queries" | sed 's/^/  /'
} | tee tools/versions.txt
