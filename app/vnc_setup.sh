#!/usr/bin/env bash
set -euo pipefail

DISPLAY_ID="${LLM_BRIDGE_VNC_DISPLAY:-:99}"
SCREEN_SPEC="${LLM_BRIDGE_VNC_SCREEN:-1920x1080x24}"
VNC_PORT="${LLM_BRIDGE_VNC_PORT:-5900}"
NOVNC_PORT="${LLM_BRIDGE_NOVNC_PORT:-6080}"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${BASE_DIR}/data/vnc"
NOVNC_DIR="${BASE_DIR}/novnc"

mkdir -p "${LOG_DIR}"

display_number="${DISPLAY_ID#:}"
display_number="${display_number%%.*}"

is_running() {
  pgrep -f "$1" >/dev/null 2>&1
}

if ! is_running "Xvfb ${DISPLAY_ID}"; then
  if [ -e "/tmp/.X${display_number}-lock" ] && [ ! -S "/tmp/.X11-unix/X${display_number}" ]; then
    rm -f "/tmp/.X${display_number}-lock"
  fi
  setsid nohup Xvfb "${DISPLAY_ID}" -screen 0 "${SCREEN_SPEC}" -ac +extension RANDR \
    >"${LOG_DIR}/xvfb.log" 2>&1 &
  echo "$!" >"${LOG_DIR}/xvfb.pid"
fi

for _ in $(seq 1 50); do
  if [ -S "/tmp/.X11-unix/X${display_number}" ]; then
    break
  fi
  sleep 0.1
done

if [ ! -S "/tmp/.X11-unix/X${display_number}" ]; then
  echo "Xvfb did not create ${DISPLAY_ID}" >&2
  exit 1
fi

if ! is_running "x11vnc .*${DISPLAY_ID}.*-rfbport ${VNC_PORT}"; then
  setsid nohup x11vnc -display "${DISPLAY_ID}" -rfbport "${VNC_PORT}" -forever -shared -nopw \
    -xkb -noxdamage -repeat -listen 0.0.0.0 \
    >"${LOG_DIR}/x11vnc.log" 2>&1 &
  echo "$!" >"${LOG_DIR}/x11vnc.pid"
fi

if ! is_running "websockify .*${NOVNC_PORT} .*localhost:${VNC_PORT}"; then
  websockify -D --web "${NOVNC_DIR}" --log-file "${LOG_DIR}/websockify.log" \
    "0.0.0.0:${NOVNC_PORT}" "localhost:${VNC_PORT}"
fi
