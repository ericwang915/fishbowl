#!/bin/bash
# Packages for the fishbowl desktop. Shared by the Lima template and the WSL setup.
set -eux
export DEBIAN_FRONTEND=noninteractive
command -v chromium >/dev/null && command -v x11vnc >/dev/null && exit 0
apt-get update
apt-get install -y --no-install-recommends \
  xvfb openbox x11vnc novnc websockify chromium xdotool xclip imagemagick xterm \
  dbus-x11 fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji curl ca-certificates
