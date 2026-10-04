#!/usr/bin/env bash
# Levanta Kev-0.8B (:8009) si no está corriendo y la bandeja de soporte (:8002). Ctrl+C corta todo.
set -euo pipefail
cd "$(dirname "$0")/.."
PIDS=""
trap '[ -n "$PIDS" ] && kill $PIDS 2>/dev/null' EXIT
if ! curl -sf localhost:8009/v1/models >/dev/null; then
  (cd vendor/kev && uv run --extra serve python -m kev.serve --run "${KEV_MODEL:-jaredpalmer/kev-0.8b}" --port 8009) &
  PIDS="$!"
  echo "Esperando a Kev (:8009)…"
  until curl -sf localhost:8009/v1/models >/dev/null; do sleep 1; done
fi
echo "Listo. Abrí http://127.0.0.1:8002"
uv run uvicorn soporte.server:app --port 8002
