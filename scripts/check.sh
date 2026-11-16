#!/usr/bin/env bash
# Full pre-commit check: lint, formatting, tests, build of the reference firmware.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"

echo "==> monaco";               "$PY" scripts/fetch_monaco.py
echo "==> xterm.js";             "$PY" scripts/fetch_xterm.py
echo "==> ruff check";           "$PY" -m ruff check .
echo "==> ruff format --check";  "$PY" -m ruff format --check .
echo "==> translations";         "$PY" scripts/update_translations.py --check
echo "==> pytest";               QT_QPA_PLATFORM=offscreen "$PY" -m pytest -q

if command -v cargo >/dev/null 2>&1; then
  echo "==> cargo test (tests/drivers: the module drivers on the computer)"
  (cd tests/drivers && cargo test --quiet)
  for manifest in reference/*/Cargo.toml; do
    dir="$(dirname "$manifest")"
    echo "==> cargo build --release ($dir)"
    # cd is required: rust-toolchain.toml and .cargo/config.toml are resolved from the cwd.
    (cd "$dir" && cargo build --release --quiet)
  done
else
  echo "cargo not found, skipping firmware build"
fi
echo "OK"
