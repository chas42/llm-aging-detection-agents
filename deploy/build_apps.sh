#!/usr/bin/env bash
# Builds every app variant whose app.yaml declares a `build` command (e.g. Rust: cargo build --release).
# Run once after setup.sh, and again after any app variant changes.
#   bash deploy/build_apps.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
for manifest in apps/*/*/app.yaml; do
  dir="$(dirname "$manifest")"
  build="$(.venv/bin/python -c "import sys, yaml, shlex; m = yaml.safe_load(open(sys.argv[1])) or {}; print(shlex.join(m['build']) if m.get('build') else '')" "$manifest")"
  [[ -z "$build" ]] && continue
  echo "[build] $dir: $build"
  (cd "$dir" && eval "$build")
done
echo "[build] done"
