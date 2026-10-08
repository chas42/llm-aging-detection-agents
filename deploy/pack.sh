#!/usr/bin/env bash
# Builds the transfer bundle for the isolated test machine (run on the development machine).
#
#   deploy/pack.sh                    # online install on the target (pip downloads packages)
#   deploy/pack.sh --wheels 3.10      # also bundle wheels for an OFFLINE target with Python 3.10
#
# Output: dist/aging-validation-bundle-<date>.tar.gz (+ .sha256)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WHEELS_PY=""
if [[ "${1:-}" == "--wheels" ]]; then WHEELS_PY="${2:?usage: --wheels <python version, e.g. 3.10>}"; fi

STAMP="$(date +%Y%m%d-%H%M)"
NAME="aging-validation-bundle-$STAMP"
STAGE="$(mktemp -d)/$NAME"
mkdir -p "$STAGE" dist

# Only what the test machine needs: no .venv, no runs, no agent definitions.
INCLUDE=(README.md requirements.txt docs findings harness workloads reference deploy
         apps/uptime-fastapi/original apps/uptime-fastapi/instrumented)
for p in "${INCLUDE[@]}"; do
  mkdir -p "$STAGE/$(dirname "$p")"
  cp -r "$p" "$STAGE/$p"
done
find "$STAGE" \( -name __pycache__ -o -name '*.pyc' -o -name '*.sqlite3*' \) -prune -exec rm -rf {} +
mkdir -p "$STAGE/runs"

if [[ -n "$WHEELS_PY" ]]; then
  echo "[pack] downloading wheels for CPython $WHEELS_PY / manylinux x86_64 ..."
  .venv/bin/pip download -q -d "$STAGE/wheelhouse" \
    --python-version "$WHEELS_PY" --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
    --platform manylinux_2_28_x86_64 --only-binary=:all: \
    -r requirements.txt -r apps/uptime-fastapi/original/requirements.txt pip setuptools wheel
fi

# Integrity manifest, verified by setup.sh on the target
(cd "$STAGE" && find . -type f ! -name MANIFEST.sha256 ! -path './wheelhouse/*' -print0 | sort -z \
   | xargs -0 sha256sum > MANIFEST.sha256)
echo "$STAMP" > "$STAGE/BUNDLE_VERSION"

tar -C "$(dirname "$STAGE")" -czf "dist/$NAME.tar.gz" "$NAME"
(cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
rm -rf "$(dirname "$STAGE")"
echo "[pack] dist/$NAME.tar.gz ($(du -h "dist/$NAME.tar.gz" | cut -f1))"
echo "[pack] checksum: $(cat "dist/$NAME.tar.gz.sha256")"
