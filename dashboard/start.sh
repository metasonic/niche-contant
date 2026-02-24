#!/usr/bin/env bash
# Start the dashboard and expose it via localtunnel.
# Usage: bash dashboard/start.sh

set -euo pipefail

PORT="${1:-5050}"
DIR="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
  echo ""
  echo "Shutting down..."
  kill "$FLASK_PID" 2>/dev/null || true
  kill "$LT_PID" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# --- Get the tunnel password (your public IP) ---
PASSWORD=$(curl -s https://loca.lt/mytunnelpassword 2>/dev/null || curl -s https://ifconfig.me 2>/dev/null || echo "unknown")

# --- Start Flask ---
echo "Starting dashboard on port $PORT..."
python3 -m flask --app dashboard.app run --port "$PORT" &
FLASK_PID=$!
sleep 2

if ! kill -0 "$FLASK_PID" 2>/dev/null; then
  echo "Error: Flask failed to start."
  exit 1
fi

# --- Start localtunnel ---
echo "Opening tunnel..."
npx localtunnel --port "$PORT" &
LT_PID=$!
sleep 3

echo ""
echo "============================================"
echo "  Dashboard running on http://localhost:$PORT"
echo "  Tunnel password:  $PASSWORD"
echo "============================================"
echo ""
echo "Press Ctrl+C to stop."

wait
