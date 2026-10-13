#!/bin/bash
set -eux
# Runs as the desktop user (Lima) or as root with FB_USER/FB_HOME set (WSL).
U=${FB_USER:-$(id -un)}; H=${FB_HOME:-$HOME}
unit() {  # name, description, after, command
  sudo tee /etc/systemd/system/fishbowl-$1.service >/dev/null <<EOF
[Unit]
Description=fishbowl $2
After=$3
Requires=$3
[Service]
User=$U
Environment=DISPLAY=:1
Environment=HOME=$H
ExecStart=$4
Restart=always
RestartSec=1
[Install]
WantedBy=multi-user.target
EOF
}
sudo tee /etc/systemd/system/fishbowl-xvfb.service >/dev/null <<EOF
[Unit]
Description=fishbowl X display
[Service]
User=$U
ExecStart=/usr/bin/Xvfb :1 -screen 0 1280x800x24 -nolisten tcp -ac
Restart=always
[Install]
WantedBy=multi-user.target
EOF
unit wm "window manager" fishbowl-xvfb.service "/usr/bin/openbox"
unit vnc "VNC server" fishbowl-xvfb.service "/usr/bin/x11vnc -display :1 -localhost -forever -shared -nopw -rfbport 5900 -quiet"
unit novnc "noVNC" fishbowl-vnc.service "/usr/bin/websockify --web /usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900"
# The clipboard is bridged in memory by the MCP server (no service, nothing on disk).
unit chromium "Chromium" fishbowl-wm.service "/usr/bin/chromium --no-first-run --no-default-browser-check --disable-dev-shm-usage --password-store=basic --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --window-position=0,0 --window-size=1280,800 --start-maximized --user-data-dir=$H/.config/fishbowl-chromium about:blank"
sudo systemctl daemon-reload
sudo systemctl enable --now fishbowl-xvfb fishbowl-wm fishbowl-vnc fishbowl-novnc fishbowl-chromium
