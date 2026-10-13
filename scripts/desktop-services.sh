#!/usr/bin/env bash
# Start the same desktop stack the Lima VM runs, directly on a Linux host (CI, or a Linux box).
# Usage: scripts/desktop-services.sh [chrome-binary]
set -euo pipefail
CHROME=${1:-${CHROME_BIN:-chromium}}
export DISPLAY=:1
Xvfb :1 -screen 0 1280x800x24 -nolisten tcp -ac >/tmp/xvfb.log 2>&1 &
for i in $(seq 1 50); do xdpyinfo -display :1 >/dev/null 2>&1 && break; sleep 0.1; done
openbox >/tmp/openbox.log 2>&1 &
sleep 0.5
"$CHROME" --no-first-run --no-default-browser-check --disable-dev-shm-usage --no-sandbox \
  --remote-debugging-port=9222 --window-position=0,0 --window-size=1280,800 --start-maximized \
  --user-data-dir=/tmp/fishbowl-chrome about:blank >/tmp/chrome.log 2>&1 &
for i in $(seq 1 100); do curl -sf http://127.0.0.1:9222/json/version >/dev/null && echo "desktop up" && exit 0; sleep 0.2; done
echo "chrome did not start" >&2; cat /tmp/chrome.log >&2; exit 1
