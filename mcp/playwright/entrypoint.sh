#!/usr/bin/env bash
set -euo pipefail

mkdir -p /vibeprod-shots

node /files-mcp/server.mjs &
FILES_PID=$!
trap 'kill "$FILES_PID" 2>/dev/null || true' EXIT

node /vision-mcp/server.mjs &
VISION_PID=$!
trap 'kill "$FILES_PID" "$VISION_PID" 2>/dev/null || true' EXIT

exec playwright-mcp \
  --port 8931 \
  --host 0.0.0.0 \
  --allowed-hosts '*' \
  --browser chromium \
  --isolated \
  --output-dir /vibeprod-shots
